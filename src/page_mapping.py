import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable, List, Optional, Tuple


Element = Dict[str, Any]
Page = Dict[str, Any]
EXECUTABLE_ACTION_KINDS = {"form", "button", "link", "file"}


def page_id(source: str) -> str:
    """Return a stable page id from a Windows or POSIX JSP path."""
    return PureWindowsPath(source).name


def action_name(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = str(value).strip()
    if not value or any(token in value for token in ("<", ">", "%", "${")):
        return None
    value = re.sub(r"^(javascript:|JavaScript:)", "", value)
    value = value.split("?", 1)[0].strip().strip("\"'")

    function_match = re.match(r"^([A-Za-z_][\w$]*)\s*\(", value)
    if function_match:
        return function_match.group(1)

    path_match = re.search(r"([A-Za-z_][\w./-]*?)(?:\.do)?(?:['\")]|\s|;|$)", value)
    if not path_match:
        return None
    value = path_match.group(1).rsplit("/", 1)[-1].removesuffix(".do")
    if not value or value in {"#", ".", ".."}:
        return None
    if not re.match(r"^[A-Za-z_][\w$.-]*$", value):
        return None
    return value


def attr(element: Element, *names: str) -> Optional[str]:
    attributes = element.get("attributes", {})
    lower = {str(key).lower(): value for key, value in attributes.items()}
    for name in names:
        value = lower.get(name.lower())
        if value not in (None, ""):
            return str(value)
    return None



def normalize_locator(element: Element) -> Optional[str]:
    """Return an executable locator.

    - Struts html:file: name is Form Bean, property is real input name.
    - form elements may not have an explicit name, so fall back to action.
    """
    kind = str(element.get("kind") or "")
    tag = str(element.get("tag") or "").lower()
    attrs = element.get("attributes") or {}

    if kind == "file" or tag == "html:file":
        field_name = attrs.get("property") or attrs.get("path") or attrs.get("name") or attrs.get("id")
        if field_name:
            return f"input[name='{field_name}']"

    if kind == "form" or tag in {"html:form", "form:form"}:
        form_name = attrs.get("name") or attrs.get("id")
        if form_name:
            return f"form[name='{form_name}']"
        action = attrs.get("action")
        if action:
            action_name_part = str(action).rstrip("/").rsplit("/", 1)[-1].removesuffix(".do")
            if action_name_part:
                return f"form[action*='{action_name_part}']"

    locator = element.get("locator")
    if locator:
        return str(locator)

    return None

def element_field_name(element: Element) -> Optional[str]:
    """Return the business field name for semantic matching.

    Struts html:file is special: name is the Form Bean name, while property is
    the generated input name. For normal Struts tags, property is also preferred.
    """
    kind = str(element.get("kind") or "")
    tag = str(element.get("tag") or "").lower()

    if element.get("field_name"):
        return str(element.get("field_name"))

    if kind == "file" or tag == "html:file":
        return attr(element, "property", "path", "name", "id", "styleId")

    if tag.startswith("html:"):
        return attr(element, "property", "name", "id", "styleId")

    if tag.startswith("form:"):
        return attr(element, "path", "name", "id", "modelAttribute", "commandName")

    return attr(
        element,
        "id",
        "styleId",
        "name",
        "property",
        "path",
        "modelAttribute",
        "commandName",
    )


def element_label(element: Element) -> str:
    return (
        element_field_name(element)
        or action_name(attr(element, "action", "href", "onClick", "onclick"))
        or attr(element, "value", "title")
        or normalize_locator(element)
        or element.get("tag")
        or "unknown"
    )


def navigation_target(element: Element) -> Optional[str]:
    if element.get("kind") == "form":
        return action_name(attr(element, "action"))
    if element.get("kind") == "link":
        return action_name(attr(element, "href", "action"))
    return None


def action_target(element: Element) -> Optional[str]:
    return (
        action_name(attr(element, "action"))
        or action_name(attr(element, "href"))
        or action_name(attr(element, "onClick", "onclick"))
    )


def element_key(element: Element) -> Tuple[str, str]:
    kind = str(element.get("kind") or "unknown")
    locator = element.get("locator")
    if locator:
        return kind, f"locator:{locator}"

    id_or_name = attr(element, "id", "styleId", "name", "property", "path", "modelAttribute", "commandName")
    if id_or_name:
        return kind, f"name:{id_or_name}"

    target = action_target(element)
    if target:
        return kind, f"action:{target}"

    raw = " ".join(str(element.get("raw") or "").split())
    return kind, f"raw:{raw[:120]}"


def semantic_key(element: Element) -> Tuple[str, str]:
    kind = str(element.get("kind") or "unknown")

    # Forms, buttons and links are primarily business actions.
    if kind in {"form", "button", "link"}:
        target = action_target(element)
        if target:
            return kind, f"action:{target}"

    # Input-like controls are primarily fields. This fixes Struts html:file
    # matching against Spring/native input[type=file].
    field_name = element_field_name(element)
    if field_name:
        return kind, f"field:{field_name}"

    target = action_target(element)
    if target:
        return kind, f"action:{target}"

    locator = normalize_locator(element)
    if locator:
        return kind, f"locator:{locator}"

    return element_key(element)


def index_pages(data: Dict[str, Any]) -> Dict[str, List[Page]]:
    pages: Dict[str, List[Page]] = defaultdict(list)
    for page in data.get("pages", []):
        pages[page_id(page.get("source", ""))].append(page)
    return dict(pages)


def flatten_elements(pages: Iterable[Page]) -> List[Element]:
    elements: List[Element] = []
    for page in pages:
        for element in page.get("elements", []):
            item = dict(element)
            item["_page_source"] = page.get("source")
            elements.append(item)
    return elements


def count_by_key(elements: Iterable[Element], key_fn) -> Counter:
    return Counter(key_fn(element) for element in elements)


def locator_change_candidates(legacy: List[Element], new: List[Element]) -> List[Dict[str, Any]]:
    legacy_by_semantic: Dict[Tuple[str, str], List[Element]] = defaultdict(list)
    new_by_semantic: Dict[Tuple[str, str], List[Element]] = defaultdict(list)

    for element in legacy:
        legacy_by_semantic[semantic_key(element)].append(element)
    for element in new:
        new_by_semantic[semantic_key(element)].append(element)

    changes: List[Dict[str, Any]] = []
    for key in sorted(set(legacy_by_semantic) & set(new_by_semantic)):
        for old, current in zip(legacy_by_semantic[key], new_by_semantic[key]):
            old_locator = normalize_locator(old)
            new_locator = normalize_locator(current)
            if old_locator and new_locator and old_locator != new_locator:
                changes.append(
                    {
                        "kind": old.get("kind"),
                        "semantic_key": key[1],
                        "label": element_label(old),
                        "legacy_locator": old_locator,
                        "new_locator": new_locator,
                        "legacy_line": old.get("line"),
                        "new_line": current.get("line"),
                    }
                )
    return changes


def classify_risk(missing_count: int, locator_change_count: int, legacy_count: int) -> str:
    if missing_count or locator_change_count:
        if missing_count >= 3 or locator_change_count >= 3 or missing_count >= max(1, legacy_count // 2):
            return "High"
        return "Medium"
    return "Low"


def normalized_locator(element: Element) -> Optional[str]:
    return normalize_locator(element)


def action_hint_for_step(element: Element) -> Optional[str]:
    kind = str(element.get("kind") or "")
    attrs = element.get("attributes") or {}
    onclick = str(attrs.get("onclick") or attrs.get("onClick") or "").lower()
    href = str(attrs.get("href") or "").lower()

    if kind == "file":
        return "upload"
    if kind == "form":
        return "submit"
    if "window.close" in onclick:
        return "close_window"
    if "download" in onclick or "templatedownload" in onclick:
        return "download"
    if "fnsubmit" in onclick or ".submit" in onclick:
        return "submit"
    if kind == "link" and href and href not in {"#", "javascript:void(0)"}:
        return "navigate"
    return element.get("action_hint") or ("click" if kind in {"button", "link"} else None)


def executable_action_step(element: Element) -> Optional[Dict[str, Any]]:
    kind = str(element.get("kind") or "")
    if kind not in EXECUTABLE_ACTION_KINDS:
        return None

    locator = normalized_locator(element)
    if not locator:
        return None

    step = {
        "kind": kind,
        "label": element_label(element),
        "legacy_locator": locator,
        "semantic_key": semantic_key(element)[1],
        "action_hint": action_hint_for_step(element),
        "line": element.get("line"),
        "attributes": element.get("attributes", {}),
    }
    if element.get("raw"):
        step["raw"] = element.get("raw")
    return step


def compare_page(page: str, legacy_pages: List[Page], new_pages: List[Page]) -> Dict[str, Any]:
    legacy_elements = flatten_elements(legacy_pages)
    new_elements = flatten_elements(new_pages)
    
    # [月眸增强] 提取所有潜在的可交互元素，不仅限于 locator_changes
    # 这将显著增加回归测试的项目数量，涵盖所有识别到的按钮、链接和输入框
    full_action_steps = []
    for element in legacy_elements:
        step = executable_action_step(element)
        if step:
            full_action_steps.append(step)

    legacy_counts = count_by_key(legacy_elements, semantic_key)
    new_counts = count_by_key(new_elements, semantic_key)
    new_semantic_keys = {semantic_key(element) for element in new_elements}
    new_locators = {
        normalized_locator(element)
        for element in new_elements
        if normalized_locator(element)
    }
    
    missing: List[Dict[str, Any]] = []
    for key, count in legacy_counts.items():
        delta = count - new_counts.get(key, 0)
        if delta <= 0:
            continue

        sample = next(element for element in legacy_elements if semantic_key(element) == key)

        sample_locator = normalized_locator(sample)

        # 二次兜底：如果 New 侧存在相同 normalized locator，则不认为缺失
        if sample_locator and sample_locator in new_locators:
            continue

        missing.append(
            {
                "kind": key[0],
                "key": key[1],
                "label": element_label(sample),
                "locator": sample_locator or sample.get("locator"),
                "action": action_target(sample),
                "line": sample.get("line"),
                "count": delta,
            }
        )
        
    locator_changes = locator_change_candidates(legacy_elements, new_elements)
    legacy_nav = sorted({target for target in map(navigation_target, legacy_elements) if target})
    new_nav = sorted({target for target in map(navigation_target, new_elements) if target})
    common_nav = sorted(set(legacy_nav) & set(new_nav))

    actions = sorted(
        {
            target
            for target in map(action_target, legacy_elements + new_elements)
            if target
        }
    )
    return {
        "page_id": page,
        "legacy_sources": [item.get("source") for item in legacy_pages],
        "new_sources": [item.get("source") for item in new_pages],
        "legacy_element_count": len(legacy_elements),
        "new_element_count": len(new_elements),
        "legacy_counts": dict(sum((Counter(page.get("counts", {})) for page in legacy_pages), Counter())),
        "new_counts": dict(sum((Counter(page.get("counts", {})) for page in new_pages), Counter())),
        "common_navigation_paths": common_nav,
        "legacy_only_navigation_paths": sorted(set(legacy_nav) - set(new_nav)),
        "new_only_navigation_paths": sorted(set(new_nav) - set(legacy_nav)),
        "actions": actions,
        "full_action_steps": full_action_steps,
        "missing_legacy_elements": missing,
        "locator_changes": locator_changes,
        "risk": classify_risk(len(missing), len(locator_changes), len(legacy_elements)),
    }


def build_mapping(legacy_data: Dict[str, Any], new_data: Dict[str, Any]) -> Dict[str, Any]:
    legacy_pages = index_pages(legacy_data)
    new_pages = index_pages(new_data)
    common_pages = sorted(set(legacy_pages) & set(new_pages))

    page_mappings = [
        compare_page(page, legacy_pages[page], new_pages[page])
        for page in common_pages
    ]
    high_risk = [page for page in page_mappings if page["risk"] == "High"]
    medium_risk = [page for page in page_mappings if page["risk"] == "Medium"]

    action_to_pages: Dict[str, List[str]] = defaultdict(list)
    for mapping in page_mappings:
        for action in mapping["actions"]:
            action_to_pages[action].append(mapping["page_id"])

    return {
        "summary": {
            "legacy_root": legacy_data.get("root"),
            "new_root": new_data.get("root"),
            "legacy_pages": len(legacy_data.get("pages", [])),
            "new_pages": len(new_data.get("pages", [])),
            "matched_pages": len(common_pages),
            "legacy_only_pages": len(set(legacy_pages) - set(new_pages)),
            "new_only_pages": len(set(new_pages) - set(legacy_pages)),
            "high_risk_pages": len(high_risk),
            "medium_risk_pages": len(medium_risk),
        },
        "common_navigation_paths": sorted(
            {
                target
                for mapping in page_mappings
                for target in mapping["common_navigation_paths"]
            }
        ),
        "action_to_pages": {key: sorted(value) for key, value in sorted(action_to_pages.items())},
        "high_risk_pages": high_risk,
        "medium_risk_pages": medium_risk,
        "legacy_only_pages": sorted(set(legacy_pages) - set(new_pages)),
        "new_only_pages": sorted(set(new_pages) - set(legacy_pages)),
        "page_mappings": page_mappings,
    }


def write_json(data: Dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def render_markdown(mapping: Dict[str, Any], limit: int = 30) -> str:
    summary = mapping["summary"]
    lines = [
        "# Legacy/New JSP 降维比对摘要",
        "",
        "## 总览",
        "",
        f"- Legacy 页面数：{summary['legacy_pages']}",
        f"- New 页面数：{summary['new_pages']}",
        f"- 同名页面匹配数：{summary['matched_pages']}",
        f"- Legacy 独有页面数：{summary['legacy_only_pages']}",
        f"- New 独有页面数：{summary['new_only_pages']}",
        f"- 高风险页面数：{summary['high_risk_pages']}",
        f"- 中风险页面数：{summary['medium_risk_pages']}",
        f"- 公共导航路径数：{len(mapping['common_navigation_paths'])}",
        f"- Action 映射数：{len(mapping['action_to_pages'])}",
        "",
        "## 一致性比对执行流规划",
        "",
        "1. 读取 `page_mapping.json`，按 `risk` 优先级选择页面；先执行 High，再执行 Medium/Low。",
        "2. Legacy 环境打开目标页面，使用 Legacy 定位器执行动作，记录 URL、DOM 快照、网络请求、弹窗、下载文件和截图。",
        "3. New 环境打开同一 `page_id`，通过映射后的定位器复现同一 Action；定位器变化时优先使用 New locator，缺失时标记为阻断。",
        "4. 对比两端结果：截图差异、URL/action、关键文本、表格数据、下载文件名/大小/hash、服务端错误页。",
        "5. 输出每个 Action 的 `PASS / DIFF / BLOCKED`，并把缺失元素和定位器变更回写到风险摘要。",
        "",
        "## 公共导航路径 Top 30",
        "",
    ]
    for target in mapping["common_navigation_paths"][:30]:
        lines.append(f"- `{target}`")

    lines.extend(["", "## 高风险页面 Top 30", "", "| 页面 | Legacy元素 | New元素 | 缺失 | 定位器变更 | 公共导航 |", "|---|---:|---:|---:|---:|---|"])
    for page in mapping["high_risk_pages"][:limit]:
        lines.append(
            "| {page_id} | {legacy_element_count} | {new_element_count} | {missing} | {changed} | {nav} |".format(
                page_id=page["page_id"],
                legacy_element_count=page["legacy_element_count"],
                new_element_count=page["new_element_count"],
                missing=len(page["missing_legacy_elements"]),
                changed=len(page["locator_changes"]),
                nav=", ".join(f"`{item}`" for item in page["common_navigation_paths"][:3]) or "-",
            )
        )

    lines.extend(["", "## 代表性缺失/定位器变更", ""])
    for page in mapping["high_risk_pages"][:10]:
        lines.append(f"### {page['page_id']}")
        for item in page["missing_legacy_elements"][:5]:
            lines.append(
                f"- 缺失：{item['kind']} `{item['label']}` locator={item.get('locator') or '-'} action={item.get('action') or '-'}"
            )
        for item in page["locator_changes"][:5]:
            lines.append(
                f"- 定位器变更：{item['kind']} `{item['label']}` {item['legacy_locator']} -> {item['new_locator']}"
            )
        lines.append("")

    lines.extend(["## Action -> 页面 Top 50", "", "| Action | 页面数 | 示例页面 |", "|---|---:|---|"])
    for action, pages in list(mapping["action_to_pages"].items())[:50]:
        lines.append(f"| `{action}` | {len(pages)} | {', '.join(pages[:5])} |")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build reduced Legacy/New JSP page mappings.")
    parser.add_argument("legacy", type=Path, help="Legacy elements.json")
    parser.add_argument("new", type=Path, help="New elements.json")
    parser.add_argument(
        "-o", "--output", type=Path, help="JSON mapping output path (optional)"
    )
    parser.add_argument(
        "--md", type=Path, help="Markdown summary output path (optional)"
    )
    args = parser.parse_args()

    legacy_data = json.loads(args.legacy.read_text(encoding="utf-8"))
    new_data = json.loads(args.new.read_text(encoding="utf-8"))

    mapping = build_mapping(legacy_data, new_data)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    md_report = render_markdown(mapping)
    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(md_report, encoding="utf-8")

    if not args.output and not args.md:
        print(md_report)


if __name__ == "__main__":
    main()
