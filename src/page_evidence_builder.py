import html
import re
from pathlib import PureWindowsPath
from typing import Any, Dict, Iterable, List, Optional, Tuple

from bs4 import BeautifulSoup

try:
    from src.page_case_planner import PageProfileBuilder
except ImportError:  # pragma: no cover - direct script execution fallback
    from page_case_planner import PageProfileBuilder  # type: ignore


PAGE_EVIDENCE_SCHEMA = "moonlight.page_evidence.v1"


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _truncate(value: Any, limit: int) -> str:
    text = _as_text(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"


def _safe_name(value: Any) -> str:
    name = PureWindowsPath(_as_text(value).replace("/", "\\")).name
    return name or _as_text(value, "unknown")


def _first(page: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = page.get(key)
        if value not in (None, "", [], {}):
            return _as_text(value)
    return ""


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _attributes(item: Dict[str, Any]) -> Dict[str, Any]:
    attrs = item.get("attributes")
    return attrs if isinstance(attrs, dict) else {}


def _css_string(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _tag_attrs(tag: Any) -> Dict[str, str]:
    output: Dict[str, str] = {}
    for key, value in getattr(tag, "attrs", {}).items():
        if isinstance(value, list):
            output[str(key)] = " ".join(str(item) for item in value)
        elif value is not None:
            output[str(key)] = str(value)
    return output


def _selector_for_tag(tag: Any) -> str:
    attrs = _tag_attrs(tag)
    name = attrs.get("name", "")
    tag_name = getattr(tag, "name", "") or ""
    tag_name = tag_name.lower()
    if attrs.get("id"):
        return f"#{_css_string(attrs['id'])}"
    if name and attrs.get("type") and tag_name == "input":
        if attrs.get("value") and attrs.get("type", "").lower() in {"checkbox", "radio", "hidden"}:
            return f'input[name="{_css_string(name)}"][value="{_css_string(attrs.get("value"))}"]'
        return f'{tag_name}[name="{_css_string(name)}"][type="{_css_string(attrs.get("type"))}"]'
    if name:
        return f'{tag_name}[name="{_css_string(name)}"]'
    if attrs.get("href"):
        return f'{tag_name}[href="{_css_string(attrs.get("href"))}"]'
    if attrs.get("action"):
        return f'{tag_name}[action="{_css_string(attrs.get("action"))}"]'
    if attrs.get("onclick"):
        return f'{tag_name}[onclick*="{_css_string(attrs.get("onclick"))[:90]}"]'
    return tag_name


def _hidden_by_self_or_parent(tag: Any) -> bool:
    node = tag
    while node is not None and getattr(node, "name", None):
        attrs = _tag_attrs(node)
        style = attrs.get("style", "").replace(" ", "").lower()
        if "display:none" in style or "visibility:hidden" in style or "hidden" in attrs:
            return True
        node = getattr(node, "parent", None)
    attrs = _tag_attrs(tag)
    return tag.name == "input" and attrs.get("type", "").lower() == "hidden"


def _clean_text(value: Any, limit: int = 240) -> str:
    text = re.sub(r"[\s\u3000]+", " ", html.unescape(_as_text(value))).strip()
    return _truncate(text, limit)


def _tag_text(tag: Any, limit: int = 240) -> str:
    return _clean_text(tag.get_text(" ", strip=True), limit=limit)


def _html_sources(page: Dict[str, Any]) -> List[Tuple[str, str]]:
    sources: List[Tuple[str, str]] = []
    for key in ("html", "full_html", "source_html", "dom", "content", "document_html"):
        value = page.get(key)
        if isinstance(value, str) and value.strip():
            sources.append((key, value))
    for index, frame in enumerate(_as_list(page.get("frames"))):
        if not isinstance(frame, dict):
            continue
        for key in ("html", "full_html", "source_html", "dom", "content", "document_html"):
            value = frame.get(key)
            if isinstance(value, str) and value.strip():
                frame_id = frame.get("name") or frame.get("url") or index
                sources.append((f"frame:{frame_id}:{key}", value))
                break
    return sources


def _all_dom_controls(soup: BeautifulSoup, *, limit: int = 1000) -> List[Dict[str, Any]]:
    controls: List[Dict[str, Any]] = []
    for tag in soup.select("form,input,select,textarea,button,a,table,[onclick],[formaction]"):
        attrs = _tag_attrs(tag)
        tag_name = str(tag.name or "").lower()
        control: Dict[str, Any] = {
            "tag": tag_name,
            "type": attrs.get("type", ""),
            "name": attrs.get("name", ""),
            "id": attrs.get("id", ""),
            "value": attrs.get("value", ""),
            "label": _tag_text(tag),
            "locator": _selector_for_tag(tag),
            "href": attrs.get("href", ""),
            "onclick": attrs.get("onclick") or attrs.get("onClick") or "",
            "action": attrs.get("action", ""),
            "style": attrs.get("style", ""),
            "visible": not _hidden_by_self_or_parent(tag),
            "attributes": {
                key: value
                for key, value in attrs.items()
                if key in {"class", "title", "alt", "target", "method", "enctype", "checked", "disabled", "style"}
            },
        }
        if tag_name == "select":
            control["options"] = [
                {"value": option.get("value", ""), "text": _tag_text(option, limit=120)}
                for option in tag.find_all("option")[:50]
            ]
        controls.append({key: value for key, value in control.items() if value not in (None, "", [], {})})
        if len(controls) >= limit:
            break
    return controls


def _script_text(soup: BeautifulSoup) -> str:
    return "\n".join(script.get_text("\n") for script in soup.find_all("script"))


def _find_function_blocks(script: str) -> List[Tuple[str, str]]:
    blocks: List[Tuple[str, str]] = []
    matches = list(re.finditer(r"function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*\{", script))
    for index, match in enumerate(matches):
        name = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(script)
        blocks.append((name, script[start:end]))
    return blocks


def _rule_name(function_name: str) -> str:
    text = re.sub(r"Validations$", "", function_name)
    text = re.sub(r"Check$", "", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text).lower()
    aliases = {
        "date": "date",
        "search_string_length": "search_string_length",
        "word_length": "word_length",
        "wildcard_percent_number": "wildcard_percent_number",
        "percent_dollar_combine": "percent_dollar_combine",
        "wildcard_percent_dollar_number": "wildcard_percent_dollar_number",
        "all_wildcard": "all_wildcard",
        "wildcard_question_combine": "wildcard_question_combine",
        "ww_number": "ww_number",
        "matching_pattern": "matching_pattern",
        "ipc": "ipc",
        "evalwild_card": "eval_wildcard",
    }
    return aliases.get(text, text)


def _decode_js_string(value: str) -> str:
    text = value.replace(r"\"", '"').replace(r"\'", "'").replace(r"\\", "\\")
    return html.unescape(text)


def _rule_options(function_body: str) -> Dict[str, str]:
    output: Dict[str, str] = {}
    for match in re.finditer(r"this\.([A-Za-z0-9_]+)\s*=\s*'([^']*)'", function_body):
        output[match.group(1)] = _decode_js_string(match.group(2))
    for match in re.finditer(r'this\.([A-Za-z0-9_]+)\s*=\s*"([^"]*)"', function_body):
        output[match.group(1)] = _decode_js_string(match.group(2))
    return output


def _label_from_message(message: str) -> str:
    cleaned = _clean_text(message, limit=300)
    for separator in (
        " \u306f",
        " \u3067",
        " \u306e",
        "\u306f",
        "\u3067",
        "\u306e",
        "\u3092",
        "\u304c",
    ):
        if separator in cleaned:
            return cleaned.split(separator, 1)[0].strip()
    return cleaned[:80]


def _validation_sequence(script: str) -> List[str]:
    match = re.search(r"function\s+validate\w*Form\s*\([^)]*\)\s*\{(?P<body>.*?)\n\s*\}", script, flags=re.S)
    if not match:
        return []
    return re.findall(r"\b(validate[A-Za-z0-9_]+)\s*\(", match.group("body"))


def _validation_rules(script: str, *, limit: int = 1000) -> List[Dict[str, Any]]:
    rules: List[Dict[str, Any]] = []
    entry_re = re.compile(
        r"this\.\w+\s*=\s*new Array\(\s*\"((?:\\.|[^\"])*)\"\s*,\s*\"((?:\\.|[^\"])*)\"\s*,\s*new Function\s*\(\s*\"varName\"\s*,\s*\"((?:\\.|[^\"])*)\"\s*\)\s*\)",
        flags=re.S,
    )
    for function_name, body in _find_function_blocks(script):
        if "new Array" not in body or "varName" not in body:
            continue
        rule = _rule_name(function_name)
        for match in entry_re.finditer(body):
            field_id = _decode_js_string(match.group(1))
            message = _decode_js_string(match.group(2))
            function_body = _decode_js_string(match.group(3))
            options = _rule_options(function_body)
            item: Dict[str, Any] = {
                "field_id": field_id,
                "field_label": _label_from_message(message),
                "rule": rule,
                "message": _clean_text(message, limit=500),
            }
            item.update(options)
            rules.append({key: value for key, value in item.items() if value not in (None, "", [], {})})
            if len(rules) >= limit:
                return rules
    return rules


def _category_label_from_row(row: Any, category_id: str) -> str:
    text = _tag_text(row, limit=200)
    text = re.sub(r"^\+?\s*(folder)?\s*", "", text, flags=re.I).strip()
    return text or f"category_{category_id}"


def _search_item_categories(soup: BeautifulSoup, validation_rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    validation_by_item: Dict[str, List[str]] = {}
    for rule in validation_rules:
        field_id = str(rule.get("field_id") or "")
        item_match = re.match(r"i([0-9A-Za-z]+)", field_id)
        if item_match:
            validation_by_item.setdefault(item_match.group(1), []).append(str(rule.get("rule") or ""))

    categories: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for row in soup.find_all("tr"):
        row_html = str(row)
        category_match = re.search(r"showTree\(['\"]([^'\"]+)['\"]\)", row_html)
        if category_match:
            category_id = category_match.group(1)
            current = {
                "id": category_id,
                "label": _category_label_from_row(row, category_id),
                "toggle": f"showTree('{category_id}')",
                "toggle_locator": f'a[href="javaScript:showTree(\'{_css_string(category_id)}\')"]',
                "items": [],
            }
            categories.append(current)
            continue
        checkbox = row.find("input", attrs={"name": "itemId"})
        if not checkbox:
            continue
        attrs = _tag_attrs(checkbox)
        item_id = attrs.get("value", "")
        if not item_id:
            continue
        if current is None:
            current = {"id": "uncategorized", "label": "uncategorized", "items": []}
            categories.append(current)
        onclick = attrs.get("onclick") or attrs.get("onClick") or ""
        item = {
            "item_id": item_id,
            "label": _tag_text(row, limit=180),
            "checkbox_locator": f'input[name="itemId"][value="{_css_string(item_id)}"]',
            "dynamic_handler": onclick,
            "dynamic_input_prefix": f"i{item_id}",
            "validation_rules": sorted(set(validation_by_item.get(item_id, []))),
            "initial_visible": not _hidden_by_self_or_parent(row),
        }
        current.setdefault("items", []).append(item)
    return [category for category in categories if category.get("items") or category.get("toggle")]


def _dynamic_input_mapping(categories: List[Dict[str, Any]], validation_rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    fields_by_item: Dict[str, List[str]] = {}
    for rule in validation_rules:
        field_id = str(rule.get("field_id") or "")
        match = re.match(r"i([0-9A-Za-z]+)", field_id)
        if match:
            fields_by_item.setdefault(match.group(1), []).append(field_id)

    output: List[Dict[str, Any]] = []
    for category in categories:
        for item in category.get("items") or []:
            item_id = str(item.get("item_id") or "")
            field_ids = sorted(set(fields_by_item.get(item_id) or [f"i{item_id}"]))
            output.append(
                {
                    "category_id": category.get("id"),
                    "category_label": category.get("label"),
                    "item_id": item_id,
                    "label": item.get("label"),
                    "toggle_locator": category.get("toggle_locator"),
                    "checkbox_locator": item.get("checkbox_locator"),
                    "dynamic_handler": item.get("dynamic_handler"),
                    "field_ids": field_ids,
                    "input_locators": [f'input[name="{_css_string(field_id)}"]' for field_id in field_ids],
                    "validation_rules": item.get("validation_rules") or [],
                }
            )
    return output


def _build_static_dom_profile(page: Dict[str, Any]) -> Dict[str, Any]:
    html_sources = _html_sources(page)
    all_controls: List[Dict[str, Any]] = []
    categories: List[Dict[str, Any]] = []
    validation_rules: List[Dict[str, Any]] = []
    validation_sequence: List[str] = []
    source_labels: List[str] = []

    for source_label, html_text in html_sources:
        source_labels.append(source_label)
        soup = BeautifulSoup(html_text, "html.parser")
        all_controls.extend(_all_dom_controls(soup))
        script = _script_text(soup)
        if script:
            validation_rules.extend(_validation_rules(script))
            validation_sequence.extend(_validation_sequence(script))
        categories.extend(_search_item_categories(soup, validation_rules))

    if not html_sources:
        all_controls = [_compact_control(item) for item in _iter_mapping_elements(page) if isinstance(item, dict)]

    seen_controls = set()
    unique_controls = []
    for control in all_controls:
        key = repr((control.get("locator"), control.get("tag"), control.get("name"), control.get("value")))
        if key in seen_controls:
            continue
        seen_controls.add(key)
        unique_controls.append(control)

    dynamic_mapping = _dynamic_input_mapping(categories, validation_rules)
    hidden_count = sum(1 for control in unique_controls if control.get("visible") is False)
    return {
        "source_count": len(html_sources),
        "source_labels": source_labels[:20],
        "all_control_count": len(unique_controls),
        "hidden_control_count": hidden_count,
        "all_controls": unique_controls[:600],
        "all_controls_truncated": max(0, len(unique_controls) - 600),
        "search_item_categories": categories[:80],
        "search_item_category_count": len(categories),
        "dynamic_input_mapping": dynamic_mapping[:600],
        "dynamic_input_mapping_count": len(dynamic_mapping),
        "validation_profile": {
            "validation_sequence": list(dict.fromkeys(validation_sequence)),
            "validation_rule_count": len(validation_rules),
            "validation_rules": validation_rules[:1200],
            "validation_rules_truncated": max(0, len(validation_rules) - 1200),
        },
    }


def _pick_attr(attrs: Dict[str, Any], *names: str) -> Dict[str, Any]:
    lowered = {str(key).lower(): key for key in attrs}
    output: Dict[str, Any] = {}
    for name in names:
        key = name if name in attrs else lowered.get(name.lower())
        if key is not None and attrs.get(key) not in (None, ""):
            output[name] = attrs.get(key)
    return output


def _compact_control(item: Dict[str, Any], *, raw_limit: int = 500, text_limit: int = 300) -> Dict[str, Any]:
    attrs = _attributes(item)
    locator = (
        item.get("locator")
        or item.get("selector")
        or item.get("legacy_locator")
        or item.get("new_locator")
        or attrs.get("selector")
    )
    output = {
        "locator": _truncate(locator, 300),
        "tag": _as_text(item.get("tag")),
        "kind": _as_text(item.get("kind")),
        "type": _as_text(item.get("type") or attrs.get("type")),
        "label": _truncate(item.get("label") or item.get("text") or item.get("value") or attrs.get("value"), text_limit),
        "name": _as_text(item.get("name") or attrs.get("name")),
        "id": _as_text(item.get("id") or attrs.get("id")),
        "href": _truncate(item.get("href") or attrs.get("href"), 300),
        "onclick": _truncate(item.get("onclick") or attrs.get("onclick") or attrs.get("onClick"), 500),
        "action": _truncate(item.get("action") or attrs.get("action"), 300),
        "owner_form": _as_text(item.get("owner_form_name") or item.get("ownerFormName")),
        "owner_action": _truncate(item.get("owner_form_action") or item.get("ownerFormAction"), 300),
        "frame": _as_text(item.get("frame") or item.get("frame_name") or item.get("frame_index")),
        "frame_url": _truncate(item.get("frame_url"), 300),
        "visible": item.get("visible", True),
    }
    attr_subset = _pick_attr(
        attrs,
        "title",
        "alt",
        "aria-label",
        "value",
        "target",
        "method",
        "enctype",
        "checked",
        "disabled",
    )
    if attr_subset:
        output["attributes"] = attr_subset
    options = _as_list(item.get("options"))
    if options:
        output["options"] = options[:20]
    raw = item.get("raw")
    if raw:
        output["raw"] = _truncate(raw, raw_limit)
    return {key: value for key, value in output.items() if value not in (None, "", [], {})}


def _iter_runtime_controls(page: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for item in _as_list(page.get("controls")):
        if isinstance(item, dict):
            yield item
    for frame in _as_list(page.get("frames")):
        if not isinstance(frame, dict):
            continue
        for item in _as_list(frame.get("controls")):
            if isinstance(item, dict):
                copied = dict(item)
                copied.setdefault("frame_name", frame.get("name"))
                copied.setdefault("frame_url", frame.get("url"))
                yield copied


def _iter_mapping_elements(page: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for key in (
        "elements",
        "legacy_elements",
        "new_elements",
        "full_action_steps",
        "executable_cases",
        "locator_changes",
        "missing_legacy_elements",
        "missing_new_elements",
    ):
        for item in _as_list(page.get(key)):
            if isinstance(item, dict):
                yield item


def _collect_text_sample(page: Dict[str, Any], controls: List[Dict[str, Any]], *, limit: int) -> str:
    direct = _first(page, "text", "text_sample", "body_text", "title")
    pieces = [direct] if direct else []
    for item in controls[:80]:
        label = item.get("label") or item.get("text") or item.get("value")
        if label:
            pieces.append(_as_text(label))
    return _truncate("\n".join(piece for piece in pieces if piece), limit)


def _frame_summary(page: Dict[str, Any]) -> List[Dict[str, Any]]:
    frames: List[Dict[str, Any]] = []
    for index, frame in enumerate(_as_list(page.get("frames"))):
        if not isinstance(frame, dict):
            continue
        frames.append(
            {
                "index": index,
                "name": _as_text(frame.get("name")),
                "url": _truncate(frame.get("url"), 300),
                "text_sample": _truncate(frame.get("text"), 300),
                "control_count": len(_as_list(frame.get("controls"))),
            }
        )
    return frames


def _bucket(profile: Dict[str, Any], name: str, limit: int) -> List[Dict[str, Any]]:
    return [_compact_control(item) for item in _as_list(profile.get(name))[:limit] if isinstance(item, dict)]


class PageEvidenceBuilder:
    def __init__(self, profile_builder: Optional[PageProfileBuilder] = None, *, max_controls: int = 140, max_text: int = 2000) -> None:
        self.profile_builder = profile_builder or PageProfileBuilder()
        self.max_controls = max_controls
        self.max_text = max_text

    def build(self, page_mapping: Dict[str, Any]) -> Dict[str, Any]:
        profile = self.profile_builder.build(page_mapping)
        controls = list(_iter_runtime_controls(page_mapping))
        if not controls:
            controls = list(_iter_mapping_elements(page_mapping))
        visible_controls = [
            item for item in controls
            if not isinstance(item, dict) or item.get("visible", True) is not False
        ]
        static_dom_profile = _build_static_dom_profile(page_mapping)
        compact_controls = [
            _compact_control(item)
            for item in visible_controls[: self.max_controls]
            if isinstance(item, dict)
        ]
        page_id = (
            profile.get("page_id")
            or page_mapping.get("page_id")
            or page_mapping.get("target_page_name")
            or page_mapping.get("target_page")
            or _safe_name(page_mapping.get("source"))
        )

        operation_candidates = {
            "files": _bucket(profile, "files", 20),
            "submit_actions": _bucket(profile, "submit_actions", 30),
            "template_download_actions": _bucket(profile, "template_download_actions", 20),
            "file_download_actions": _bucket(profile, "file_download_actions", 20),
            "search_actions": _bucket(profile, "search_actions", 20),
            "navigation_links": _bucket(profile, "navigation_links", 30),
            "close_actions": _bucket(profile, "close_actions", 20),
            "create_actions": _bucket(profile, "create_actions", 20),
            "update_actions": _bucket(profile, "update_actions", 20),
            "delete_actions": _bucket(profile, "delete_actions", 20),
            "tables": _bucket(profile, "tables", 20),
        }

        dependency_hints: List[str] = []
        capabilities = profile.get("capabilities") or {}
        if capabilities.get("search") and capabilities.get("text_input"):
            dependency_hints.append("Search operations usually require filling one or more input/select controls before clicking the search button.")
        if capabilities.get("file_download") and (capabilities.get("search") or capabilities.get("result_table")):
            dependency_hints.append("File output/download operations may require a result/history state produced by search or row selection.")
        if capabilities.get("upload_submit"):
            dependency_hints.append("Upload submit operations require selecting a file before submitting.")
        if capabilities.get("delete_action"):
            dependency_hints.append("Delete operations usually require selecting an existing row and may need confirmation.")

        visible_snapshot = {
            "frames": _frame_summary(page_mapping),
            "text_sample": _collect_text_sample(page_mapping, controls, limit=self.max_text),
            "visible_controls": compact_controls,
            "visible_control_count": len(visible_controls),
            "visible_controls_truncated": max(0, len(visible_controls) - len(compact_controls)),
        }

        return {
            "schema": PAGE_EVIDENCE_SCHEMA,
            "page_id": _as_text(page_id, "<unknown>"),
            "source": _as_text(page_mapping.get("profile_source") or page_mapping.get("source")),
            "runtime_profile_path": _as_text(page_mapping.get("runtime_profile_path")),
            "route_id": _as_text(page_mapping.get("route_id")),
            "side": _as_text(page_mapping.get("side")),
            "url": _truncate(page_mapping.get("url") or page_mapping.get("entry_url") or page_mapping.get("resolved_entry_url"), 500),
            "title": _as_text(page_mapping.get("title")),
            "target_page": _as_text(page_mapping.get("target_page")),
            "target_page_name": _as_text(page_mapping.get("target_page_name")),
            "counts": profile.get("counts") or page_mapping.get("counts") or {},
            "capabilities_from_rules": profile.get("capabilities") or {},
            "visible_snapshot": visible_snapshot,
            "frames": visible_snapshot["frames"],
            "text_sample": visible_snapshot["text_sample"],
            "visible_controls": visible_snapshot["visible_controls"],
            "visible_control_count": visible_snapshot["visible_control_count"],
            "visible_controls_truncated": visible_snapshot["visible_controls_truncated"],
            "static_dom_profile": {
                key: value
                for key, value in static_dom_profile.items()
                if key != "validation_profile"
            },
            "validation_profile": static_dom_profile.get("validation_profile", {}),
            "operation_candidates": operation_candidates,
            "dependency_hints": dependency_hints,
            "page_profile": {
                key: profile.get(key)
                for key in (
                    "page_id",
                    "profile_source",
                    "runtime_profile_path",
                    "entry_url",
                    "view_page",
                    "ready_selector",
                    "counts",
                    "capabilities",
                )
            },
        }
