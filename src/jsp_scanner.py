import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    from bs4 import BeautifulSoup
except ImportError as exc:  # pragma: no cover - exercised only when dependency is missing
    raise SystemExit(
        "BeautifulSoup is required. Install dependencies with: pip install -r requirements.txt"
    ) from exc


FORM_CONTAINER_TAGS = {"form", "html:form", "form:form"}
FORM_FIELD_TAGS = {
    "form:checkbox",
    "form:checkboxes",
    "form:hidden",
    "form:input",
    "form:option",
    "form:options",
    "form:password",
    "form:radiobutton",
    "form:radiobuttons",
    "form:select",
    "form:textarea",
    "html:checkbox",
    "html:hidden",
    "html:multibox",
    "html:option",
    "html:options",
    "html:password",
    "html:radio",
    "html:select",
    "html:text",
    "html:textarea",
}
ACTION_TAGS = {"html:file", "html:link", "input"}
TARGET_TAGS = FORM_CONTAINER_TAGS | FORM_FIELD_TAGS | ACTION_TAGS
TAG_RE = re.compile(
    r"<\s*(?P<tag>form:[\w.-]+|html:[\w.-]+|form|input)\b"
    r"(?P<attrs>(?:[^>\"']+|\"[^\"]*\"|'[^']*')*)"
    r"(?P<selfclose>/?)>",
    re.IGNORECASE | re.DOTALL,
)
ATTR_RE = re.compile(
    r"""(?P<name>[:\w.-]+)(?:\s*=\s*(?P<value>"[^"]*"|'[^']*'|[^\s"'=<>`]+))?""",
    re.DOTALL,
)


def parse_attributes(raw_attrs: str) -> Dict[str, Any]:
    """Parse JSP/HTML attributes while preserving Struts names such as styleId."""
    attributes: Dict[str, Any] = {}
    for match in ATTR_RE.finditer(raw_attrs):
        name = match.group("name")
        value = match.group("value")
        if not name:
            continue
        if value is None:
            attributes[name] = True
            continue
        attributes[name] = value.strip("\"'")
    return attributes


def line_number(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def classify_tag(tag: str, attributes: Dict[str, Any]) -> Optional[str]:
    normalized = tag.lower()
    if normalized in FORM_CONTAINER_TAGS:
        return "form"
    if normalized == "html:file":
        return "file"
    # 针对 Spring 标签或原生 input，检查 type=file
    if (normalized == "input" or normalized == "form:input") and str(attributes.get("type", "")).lower() == "file":
        return "file"
    if normalized == "html:link":
        return "link"
    if normalized == "input" and str(attributes.get("type", "")).lower() == "button":
        return "button"
    if normalized.startswith("form:") and normalized != "form:form":
        return "field"
    if normalized in FORM_FIELD_TAGS:
        return "field"
    return None


def build_locator(attributes: Dict[str, Any]) -> Optional[str]:
    style_id = attributes.get("styleId") or attributes.get("styleid")
    element_id = attributes.get("id") or style_id
    if element_id:
        return f"#{element_id}"

    name = attributes.get("name") or attributes.get("property") or attributes.get("path")
    if name:
        return f"[name='{name}']"

    model_attr = attributes.get("modelAttribute") or attributes.get("commandName")
    if model_attr:
        return f"[modelAttribute='{model_attr}']"

    href = attributes.get("href")
    if href:
        return f"a[href='{href}']"

    return None


def action_hint(kind: str, attributes: Dict[str, Any]) -> str:
    if kind == "form":
        return "submit"
    if kind == "file":
        return "upload"
    if kind == "button":
        return "click"
    if kind == "link":
        return "navigate"
    return "inspect"


def element_record(
    *,
    tag: str,
    attributes: Dict[str, Any],
    line: Optional[int],
    raw: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    kind = classify_tag(tag, attributes)
    if kind is None:
        return None

    record: Dict[str, Any] = {
        "kind": kind,
        "tag": tag.lower(),
        "line": line,
        "attributes": attributes,
        "locator": build_locator(attributes),
        "action_hint": action_hint(kind, attributes),
    }
    if raw is not None:
        record["raw"] = " ".join(raw.split())
    return record


def scan_with_regex(source: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for match in TAG_RE.finditer(source):
        tag = match.group("tag")
        attributes = parse_attributes(match.group("attrs"))
        record = element_record(
            tag=tag,
            attributes=attributes,
            line=line_number(source, match.start()),
            raw=match.group(0),
        )
        if record:
            records.append(record)
    return records


def scan_with_beautifulsoup(source: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(source, "html.parser")
    records: List[Dict[str, Any]] = []
    for node in soup.find_all(True):
        tag = str(node.name).lower()
        if tag not in TARGET_TAGS:
            continue
        attributes = dict(node.attrs)
        normalized_attributes = {
            key: " ".join(value) if isinstance(value, list) else value
            for key, value in attributes.items()
        }
        record = element_record(tag=tag, attributes=normalized_attributes, line=None)
        if record:
            records.append(record)
    return records


def merge_records(primary: List[Dict[str, Any]], secondary: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def canonical_attributes(item: Dict[str, Any]) -> str:
        attributes = {
            str(key).lower(): value
            for key, value in item.get("attributes", {}).items()
        }
        return json.dumps(attributes, sort_keys=True, ensure_ascii=False)

    merged = list(primary)
    seen = {
        (
            item.get("tag"),
            canonical_attributes(item),
            item.get("line"),
        )
        for item in merged
    }

    for item in secondary:
        key_without_line = (
            item.get("tag"),
            canonical_attributes(item),
            None,
        )
        already_seen = any(
            existing_tag == key_without_line[0]
            and existing_attrs == key_without_line[1]
            for existing_tag, existing_attrs, _ in seen
        )
        if already_seen:
            continue
        merged.append(item)
        seen.add(key_without_line)
    return merged


def scan_jsp_source(source: str, source_name: str = "<memory>") -> Dict[str, Any]:
    regex_records = scan_with_regex(source)
    soup_records = scan_with_beautifulsoup(source)
    elements = merge_records(regex_records, soup_records)
    counts: Dict[str, int] = {}
    for element in elements:
        kind = element["kind"]
        counts[kind] = counts.get(kind, 0) + 1

    return {
        "source": source_name,
        "counts": counts,
        "elements": elements,
    }


def iter_jsp_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
        return
    yield from sorted(path.rglob("*.jsp"))


def read_source(path: Path) -> str:
    for encoding in ("utf-8", "cp932"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def scan_path(path: Path) -> Dict[str, Any]:
    pages = []
    totals: Dict[str, int] = {}
    for jsp_file in iter_jsp_files(path):
        result = scan_jsp_source(read_source(jsp_file), str(jsp_file))
        pages.append(result)
        for kind, count in result["counts"].items():
            totals[kind] = totals.get(kind, 0) + count
    return {
        "root": str(path),
        "totals": totals,
        "pages": pages,
    }


def write_json(data: Dict[str, Any], output: Optional[Path]) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        return
    print(text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan JSP files and export Moonlight UI mapping JSON.")
    parser.add_argument("path", type=Path, help="JSP file or directory containing JSP files")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="JSON output file. Defaults to stdout.",
    )
    args = parser.parse_args()

    if not args.path.exists():
        raise SystemExit(f"Path does not exist: {args.path}")

    write_json(scan_path(args.path), args.output)


if __name__ == "__main__":
    main()
