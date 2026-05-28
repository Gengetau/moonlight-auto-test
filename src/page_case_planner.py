import json
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_CASE_TEMPLATES = [
    {
        "template_id": "initial_display",
        "case_type": "initial_display",
        "action_type": "snapshot",
        "requires": ["initial_display"],
        "priority": 10,
    },
    {
        "template_id": "upload_select",
        "case_type": "upload_select",
        "action_type": "upload",
        "requires": ["file_upload"],
        "priority": 30,
    },
    {
        "template_id": "upload_submit",
        "case_type": "upload_submit",
        "action_type": "upload_submit",
        "requires": ["upload_submit"],
        "priority": 40,
    },
    {
        "template_id": "upload_without_file",
        "case_type": "upload_without_file",
        "action_type": "submit",
        "requires": ["form_submit", "file_upload"],
        "priority": 50,
    },
    {
        "template_id": "download_template",
        "case_type": "download_template",
        "action_type": "download",
        "requires": ["template_download"],
        "priority": 60,
    },
    {
        "template_id": "file_download",
        "case_type": "file_download",
        "action_type": "download",
        "requires": ["file_download"],
        "priority": 65,
    },
    {
        "template_id": "link_navigation",
        "case_type": "link_navigation",
        "action_type": "navigate",
        "requires": ["navigation_link"],
        "priority": 70,
    },
    {
        "template_id": "search_normal",
        "case_type": "search_normal",
        "action_type": "search",
        "requires": ["search"],
        "priority": 80,
    },
    {
        "template_id": "create_action",
        "case_type": "create_action",
        "action_type": "click",
        "requires": ["create_action"],
        "priority": 850,
    },
    {
        "template_id": "update_action",
        "case_type": "update_action",
        "action_type": "click",
        "requires": ["update_action"],
        "priority": 860,
        "destructive": True,
    },
    {
        "template_id": "result_table_verify",
        "case_type": "result_table_verify",
        "action_type": "snapshot",
        "requires": ["result_table"],
        "priority": 90,
    },
    {
        "template_id": "back_action",
        "case_type": "back_action",
        "action_type": "click",
        "requires": ["back_action"],
        "priority": 880,
    },
    {
        "template_id": "delete_action",
        "case_type": "delete_action",
        "action_type": "click",
        "requires": ["delete_action"],
        "priority": 890,
        "destructive": True,
    },
    {
        "template_id": "close_window",
        "case_type": "close_window",
        "action_type": "close_window",
        "requires": ["close_window"],
        "priority": 900,
        "destructive": True,
    },
    {
        "template_id": "negative_js_error",
        "case_type": "negative_js_error",
        "action_type": "negative_js_error",
        "requires": ["initial_display"],
        "priority": 930,
        "negative": True,
    },
    {
        "template_id": "negative_http_500",
        "case_type": "negative_http_500",
        "action_type": "negative_http_500",
        "requires": ["form_submit"],
        "priority": 940,
        "negative": True,
    },
    {
        "template_id": "negative_network_abort",
        "case_type": "negative_network_abort",
        "action_type": "negative_network_abort",
        "requires": ["form_submit"],
        "priority": 950,
        "negative": True,
    },
]


NEGATIVE_CASE_TYPES = {
    "negative_js_error",
    "negative_network_abort",
    "negative_http_500",
    "negative_invalid_input",
    "negative_file_upload",
}


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _css_attr(value: Any) -> str:
    return _as_text(value).replace("\\", "\\\\").replace('"', '\\"')


def _attributes(item: Dict[str, Any]) -> Dict[str, Any]:
    value = item.get("attributes")
    return value if isinstance(value, dict) else {}


def _first_attr(item: Dict[str, Any], *names: str) -> str:
    attrs = _attributes(item)
    lowered = {str(key).lower(): value for key, value in attrs.items()}
    for name in names:
        if name in attrs:
            return _as_text(attrs[name])
        value = lowered.get(name.lower())
        if value is not None:
            return _as_text(value)
    return ""


def _field_name(item: Dict[str, Any]) -> str:
    if item.get("field_name"):
        return _as_text(item.get("field_name"))
    kind = _as_text(item.get("kind")).lower()
    tag = _as_text(item.get("tag")).lower()
    if kind == "file" or tag == "html:file":
        return _first_attr(item, "property", "path", "name", "id", "styleId")
    if tag.startswith("html:"):
        return _first_attr(item, "property", "name", "id", "styleId")
    if tag.startswith("form:"):
        return _first_attr(item, "path", "name", "id", "modelAttribute", "commandName")
    return _first_attr(item, "id", "styleId", "name", "property", "path", "value", "title", "href", "action")


def _locator(item: Dict[str, Any]) -> str:
    kind = _as_text(item.get("kind")).lower()
    tag = _as_text(item.get("tag")).lower()
    input_type = _first_attr(item, "type").lower()
    name = _first_attr(item, "name")
    value = _first_attr(item, "value")
    if tag == "input" and input_type in {"checkbox", "radio"} and name and value:
        return f'input[name="{_css_attr(name)}"][type="{_css_attr(input_type)}"][value="{_css_attr(value)}"]'
    if kind == "file" or tag == "html:file":
        field = _field_name(item)
        if field:
            return f"input[name='{field}']"
    main_step = item.get("main_step") if isinstance(item.get("main_step"), dict) else {}
    return _as_text(
        item.get("locator")
        or item.get("legacy_locator")
        or item.get("new_locator")
        or main_step.get("legacy_locator")
        or main_step.get("locator")
    )


def _label(item: Dict[str, Any]) -> str:
    return (
        _as_text(item.get("label"))
        or _field_name(item)
        or _as_text(item.get("semantic_key"))
        or _as_text(item.get("key"))
        or _as_text(item.get("label_key"))
        or _locator(item)
        or _as_text(item.get("tag"), "unknown")
    )


def _search_blob(item: Dict[str, Any]) -> str:
    attrs = _attributes(item)
    values = [
        item.get("kind"),
        item.get("tag"),
        item.get("action_type"),
        item.get("action_hint"),
        item.get("case_type"),
        item.get("label"),
        item.get("label_key"),
        item.get("semantic_key"),
        item.get("key"),
        item.get("raw"),
        item.get("locator"),
        item.get("legacy_locator"),
        item.get("new_locator"),
        item.get("test_data"),
    ]
    values.extend(attrs.values())
    return " ".join(_as_text(value) for value in values if value is not None).lower()


def _compact_label(value: Any) -> str:
    return re.sub(r"[\s\u3000]+", "", _as_text(value)).lower()


def _has_english_word(blob: str, *words: str) -> bool:
    return re.search(r"\b(?:" + "|".join(re.escape(word) for word in words) + r")\b", blob) is not None


def _is_file(item: Dict[str, Any]) -> bool:
    kind = _as_text(item.get("kind")).lower()
    tag = _as_text(item.get("tag")).lower()
    input_type = _first_attr(item, "type").lower()
    return kind == "file" or tag == "html:file" or input_type == "file" or "type=\"file\"" in _search_blob(item) or "type='file'" in _search_blob(item)


def _is_form(item: Dict[str, Any]) -> bool:
    kind = _as_text(item.get("kind")).lower()
    tag = _as_text(item.get("tag")).lower()
    return kind == "form" or tag in {"form", "html:form", "form:form"} or tag.endswith(":form")


def _is_table(item: Dict[str, Any]) -> bool:
    kind = _as_text(item.get("kind")).lower()
    tag = _as_text(item.get("tag")).lower()
    return kind == "table" or tag == "table"


def _is_result_table(item: Dict[str, Any]) -> bool:
    if not _is_table(item):
        return False
    attrs = _attributes(item)
    identity = " ".join(
        _as_text(value)
        for value in (
            attrs.get("id"),
            attrs.get("class"),
            attrs.get("name"),
            attrs.get("summary"),
            item.get("label"),
            item.get("locator"),
            item.get("semantic_key"),
            item.get("key"),
        )
        if value is not None
    ).lower()
    result_markers = (
        "result",
        "results",
        "search_result",
        "searchresult",
        "errlist",
        "errorlist",
        "\u4e00\u89a7",
        "\u691c\u7d22\u7d50\u679c",
    )
    return any(marker in identity for marker in result_markers)


def _is_select(item: Dict[str, Any]) -> bool:
    kind = _as_text(item.get("kind")).lower()
    tag = _as_text(item.get("tag")).lower()
    return kind == "select" or tag in {"select", "html:select", "form:select"} or tag.endswith(":select")


def _is_textarea(item: Dict[str, Any]) -> bool:
    kind = _as_text(item.get("kind")).lower()
    tag = _as_text(item.get("tag")).lower()
    return kind == "textarea" or tag in {"textarea", "html:textarea", "form:textarea"} or tag.endswith(":textarea")


def _is_text_input(item: Dict[str, Any]) -> bool:
    if _is_file(item) or _is_select(item) or _is_textarea(item):
        return False
    kind = _as_text(item.get("kind")).lower()
    tag = _as_text(item.get("tag")).lower()
    input_type = _first_attr(item, "type").lower()
    if input_type in {"hidden", "button", "submit", "reset", "image", "file", "checkbox", "radio"}:
        return False
    if input_type in {"", "text", "password", "search", "number", "email", "tel", "url"} and (tag in {"input", "html:text", "html:password"} or tag.endswith(":input") or kind == "field"):
        return True
    return tag in {"html:text", "html:password", "form:input"}


def _is_choice_input(item: Dict[str, Any]) -> bool:
    tag = _as_text(item.get("tag")).lower()
    input_type = _first_attr(item, "type").lower()
    return tag == "input" and input_type in {"checkbox", "radio"}


def _is_clickable_control(item: Dict[str, Any]) -> bool:
    kind = _as_text(item.get("kind")).lower()
    tag = _as_text(item.get("tag")).lower()
    input_type = _first_attr(item, "type").lower()
    return (
        kind in {"button", "link"}
        or tag in {"a", "button"}
        or (tag == "input" and input_type in {"button", "submit", "reset", "image"})
        or bool(_first_attr(item, "onclick", "onClick") and tag not in {"form", "table"})
    )


def _is_submit_action(item: Dict[str, Any]) -> bool:
    if _is_download_action(item) or _is_close_action(item):
        return False
    if _is_form(item):
        return False
    blob = _search_blob(item)
    action_type = _as_text(item.get("action_type") or item.get("action_hint") or item.get("case_type")).lower()
    input_type = _first_attr(item, "type").lower()
    return (
        action_type in {"submit", "upload_submit"}
        or "fnsubmit" in blob
        or "submitform" in blob
        or ".submit(" in blob
        or input_type == "submit"
    )


def _is_download_action(item: Dict[str, Any]) -> bool:
    return _is_template_download_action(item) or _is_file_download_action(item)


def _is_template_download_action(item: Dict[str, Any]) -> bool:
    if not _is_clickable_control(item):
        return False
    blob = _search_blob(item)
    action_type = _as_text(item.get("action_type") or item.get("action_hint") or item.get("case_type")).lower()
    has_template_marker = any(marker in blob for marker in ("templatedownload", "template", "雛形", "テンプレート"))
    has_download_marker = action_type == "download" or any(marker in blob for marker in ("download", "ダウンロード", "template", "テンプレート", "雛形"))
    return has_template_marker and has_download_marker


def _is_file_download_action(item: Dict[str, Any]) -> bool:
    if not _is_clickable_control(item) or _is_template_download_action(item):
        return False
    blob = _search_blob(item)
    action_type = _as_text(item.get("action_type") or item.get("action_hint") or item.get("case_type")).lower()
    label = _compact_label(_label(item))
    onclick = _as_text(_first_attr(item, "onclick", "onClick") or item.get("onclick")).lower()
    if any(marker in blob for marker in ("window.close", "parent.close", "fncancel", "キャンセル", "取消", "戻る", "戻り")):
        return False
    if re.search(r"\b(?:cancel|back|bak|close)\b", blob):
        return False
    output_label = label in {"出力", "ファイル出力", "pdf出力"} or label.endswith("出力")
    return (
        (action_type == "download" and output_label)
        or "fndownload" in blob
        or "download" in blob
        or "ダウンロード" in blob
        or (output_label and ("download" in onclick or "fndownload" in onclick or "download" in blob))
    )


def _is_close_action(item: Dict[str, Any]) -> bool:
    blob = _search_blob(item)
    action_type = _as_text(item.get("action_type") or item.get("action_hint") or item.get("case_type")).lower()
    return action_type == "close_window" or "window.close" in blob or "parent.close" in blob


def _is_delete_action(item: Dict[str, Any]) -> bool:
    if _is_close_action(item) or _is_download_action(item):
        return False
    blob = _search_blob(item)
    kind = _as_text(item.get("kind")).lower()
    tag = _as_text(item.get("tag")).lower()
    input_type = _first_attr(item, "type").lower()
    is_button = kind == "button" or tag == "button" or (tag == "input" and input_type in {"button", "submit", "image"})
    return is_button and ("delete" in blob or "削除" in blob)


def _is_back_action(item: Dict[str, Any]) -> bool:
    if _is_close_action(item) or _is_delete_action(item) or _is_download_action(item):
        return False
    blob = _search_blob(item)
    kind = _as_text(item.get("kind")).lower()
    tag = _as_text(item.get("tag")).lower()
    input_type = _first_attr(item, "type").lower()
    is_button = kind == "button" or tag == "button" or (tag == "input" and input_type in {"button", "submit", "image"})
    has_back_marker = (
        "戻" in blob
        or "キャンセル" in blob
        or "cancel" in blob
        or re.search(r"\b(?:back|bak)\b", blob) is not None
    )
    return is_button and has_back_marker


def _is_create_action(item: Dict[str, Any]) -> bool:
    if _is_close_action(item) or _is_delete_action(item) or _is_download_action(item):
        return False
    blob = _search_blob(item)
    kind = _as_text(item.get("kind")).lower()
    tag = _as_text(item.get("tag")).lower()
    input_type = _first_attr(item, "type").lower()
    is_button = kind == "button" or tag == "button" or (tag == "input" and input_type in {"button", "submit", "image"})
    return is_button and (
        _has_english_word(blob, "register", "insert", "entry", "create", "add")
        or any(token in blob for token in ("登録", "新規", "追加", "作成"))
    )


def _is_update_action(item: Dict[str, Any]) -> bool:
    if _is_close_action(item) or _is_delete_action(item) or _is_download_action(item) or _is_create_action(item):
        return False
    blob = _search_blob(item)
    kind = _as_text(item.get("kind")).lower()
    tag = _as_text(item.get("tag")).lower()
    input_type = _first_attr(item, "type").lower()
    is_button = kind == "button" or tag == "button" or (tag == "input" and input_type in {"button", "submit", "image"})
    return is_button and (
        _has_english_word(blob, "update", "modify", "edit", "save")
        or any(token in blob for token in ("更新", "変更", "編集", "保存"))
    )


def _is_search_action(item: Dict[str, Any]) -> bool:
    if _is_form(item):
        return False
    blob = _search_blob(item)
    return "search" in blob or "検索" in blob


def _is_navigation_link(item: Dict[str, Any]) -> bool:
    if _is_download_action(item) or _is_close_action(item) or _is_submit_action(item):
        return False
    kind = _as_text(item.get("kind")).lower()
    tag = _as_text(item.get("tag")).lower()
    action_type = _as_text(item.get("action_type") or item.get("action_hint") or item.get("case_type")).lower()
    href = _first_attr(item, "href").strip()
    blob = _search_blob(item)
    if "logout" in blob or "logoff" in blob:
        return False
    if href in {"", "#"} and action_type != "navigate":
        return False
    if href.lower().startswith("javascript:"):
        return False
    return kind == "link" or tag == "a" or action_type == "navigate"


def _is_popup_action(item: Dict[str, Any]) -> bool:
    blob = _search_blob(item)
    target = _first_attr(item, "target").strip().lower()
    return bool(target and target not in {"_self", "self"}) or "window.open" in blob


def _safe_case_id(value: Any) -> str:
    text = Path(_as_text(value, "Page").replace("\\", "/")).stem or "Page"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "Page"


class PageProfileBuilder:
    def build(self, page_mapping: Dict[str, Any]) -> Dict[str, Any]:
        page_id = _as_text(
            page_mapping.get("page_id")
            or page_mapping.get("target_page_name")
            or page_mapping.get("target_page")
            or page_mapping.get("source")
            or page_mapping.get("legacy_page"),
            "<unknown>",
        )
        elements = self._collect_elements(page_mapping)

        forms: List[Dict[str, Any]] = []
        files: List[Dict[str, Any]] = []
        submit_actions: List[Dict[str, Any]] = []
        download_actions: List[Dict[str, Any]] = []
        template_download_actions: List[Dict[str, Any]] = []
        file_download_actions: List[Dict[str, Any]] = []
        close_actions: List[Dict[str, Any]] = []
        delete_actions: List[Dict[str, Any]] = []
        back_actions: List[Dict[str, Any]] = []
        create_actions: List[Dict[str, Any]] = []
        update_actions: List[Dict[str, Any]] = []
        tables: List[Dict[str, Any]] = []
        option_controls: List[Dict[str, Any]] = []
        search_actions: List[Dict[str, Any]] = []
        navigation_links: List[Dict[str, Any]] = []

        counts = {
            "form": 0,
            "file": 0,
            "button": 0,
            "link": 0,
            "input": 0,
            "select": 0,
            "textarea": 0,
            "table": 0,
        }

        for item in elements:
            kind = _as_text(item.get("kind")).lower()
            if kind == "button":
                counts["button"] += 1
            if kind == "link":
                counts["link"] += 1

            if _is_form(item):
                counts["form"] += 1
                forms.append(self._form_entry(item))
            if _is_file(item):
                counts["file"] += 1
                files.append(self._file_entry(item))
            if _is_textarea(item):
                counts["textarea"] += 1
            elif _is_select(item):
                counts["select"] += 1
            elif _is_text_input(item):
                counts["input"] += 1
            if _is_table(item):
                counts["table"] += 1
            if _is_select(item) or _is_choice_input(item):
                option_controls.append(self._control_entry(item))
            if _is_result_table(item):
                tables.append(self._control_entry(item))
            if _is_submit_action(item):
                submit_actions.append(self._action_entry(item, "submit"))
            if _is_template_download_action(item):
                entry = self._action_entry(item, "download")
                entry["download_kind"] = "template"
                template_download_actions.append(entry)
                download_actions.append(entry)
            elif _is_file_download_action(item):
                entry = self._action_entry(item, "download")
                entry["download_kind"] = "file_output"
                file_download_actions.append(entry)
                download_actions.append(entry)
            if _is_close_action(item):
                close_actions.append(self._action_entry(item, "close_window"))
            if _is_delete_action(item):
                delete_actions.append(self._action_entry(item, "click"))
            if _is_back_action(item):
                back_actions.append(self._action_entry(item, "click"))
            if _is_create_action(item):
                create_actions.append(self._action_entry(item, "click"))
            if _is_update_action(item):
                update_actions.append(self._action_entry(item, "click"))
            if _is_search_action(item):
                search_actions.append(self._action_entry(item, "search"))
            if _is_navigation_link(item):
                navigation_links.append(self._action_entry(item, "navigate"))

        self._merge_declared_counts(counts, page_mapping)

        # A form action is a valid submit fallback, but keep concrete controls
        # first for better replay quality.
        for form in forms:
            if form.get("action") and not any(action.get("locator") == form.get("locator") for action in submit_actions):
                submit_actions.append(
                    {
                        "locator": form.get("locator"),
                        "action": form.get("action"),
                        "onclick": "",
                        "action_type": "submit",
                        "label": "form submit",
                    }
                )

        has_text_input = counts["input"] > 0 or counts["textarea"] > 0
        has_select = counts["select"] > 0
        capabilities = {
            "initial_display": True,
            "form": bool(forms) or counts["form"] > 0,
            "file_upload": bool(files) or counts["file"] > 0,
            "form_submit": bool(submit_actions),
            "upload_submit": bool(files) and bool(submit_actions),
            "template_download": bool(template_download_actions),
            "file_download": bool(file_download_actions),
            "navigation_link": bool(navigation_links),
            "close_window": bool(close_actions),
            "delete_action": bool(delete_actions),
            "back_action": bool(back_actions),
            "create_action": bool(create_actions),
            "update_action": bool(update_actions),
            "text_input": has_text_input,
            "select": has_select,
            "search": bool(search_actions) and (has_text_input or has_select),
            "result_table": bool(tables),
            "output_options": bool(option_controls),
            "popup": any(_is_popup_action(item) for item in elements) or any(form.get("target") and form.get("target").lower() not in {"_self", "self"} for form in forms),
        }

        ready_selector = ""
        for bucket in (files, submit_actions, download_actions, create_actions, update_actions, back_actions, delete_actions, close_actions, forms, tables):
            if bucket and bucket[0].get("locator"):
                ready_selector = bucket[0]["locator"]
                break

        return {
            "page_id": page_id,
            "entry_url": _as_text(page_mapping.get("entry_url") or page_mapping.get("resolved_entry_url") or page_mapping.get("url")),
            "view_page": _as_text(page_mapping.get("view_page") or page_mapping.get("target_page") or page_mapping.get("url") or page_id),
            "ready_selector": _as_text(page_mapping.get("ready_selector") or ready_selector or "__page__"),
            "profile_source": _as_text(page_mapping.get("schema") or page_mapping.get("profile_source") or "static_mapping"),
            "runtime_profile_path": _as_text(page_mapping.get("runtime_profile_path")),
            "counts": counts,
            "forms": forms,
            "files": files,
            "submit_actions": submit_actions,
            "download_actions": download_actions,
            "template_download_actions": template_download_actions,
            "file_download_actions": file_download_actions,
            "navigation_links": navigation_links,
            "close_actions": close_actions,
            "delete_actions": self._dedupe_action_entries(delete_actions),
            "back_actions": self._dedupe_action_entries(back_actions),
            "create_actions": self._dedupe_action_entries(create_actions),
            "update_actions": self._dedupe_action_entries(update_actions),
            "search_actions": search_actions,
            "tables": tables,
            "option_controls": option_controls,
            "capabilities": capabilities,
        }

    @staticmethod
    def _dedupe_action_entries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        seen = set()
        for item in items:
            blob = " ".join(_as_text(item.get(key)) for key in ("label", "action", "onclick", "locator")).lower()
            semantic = re.sub(r"'[^']*'|\"[^\"]*\"|\d+", "<arg>", blob)
            semantic = re.sub(r"\s+", " ", semantic).strip()
            if not semantic:
                semantic = _as_text(item.get("locator"))
            if semantic in seen:
                continue
            seen.add(semantic)
            result.append(item)
        return result

    def _collect_elements(self, page_mapping: Dict[str, Any]) -> List[Dict[str, Any]]:
        elements: List[Dict[str, Any]] = []

        def add_item(item: Any, source: str) -> None:
            if not isinstance(item, dict):
                return
            copied = dict(item)
            copied.setdefault("_source", source)
            elements.append(copied)
            for nested_key in ("legacy_element", "new_element", "legacy", "new", "element"):
                nested = item.get(nested_key)
                if isinstance(nested, dict):
                    nested_copy = dict(nested)
                    nested_copy.setdefault("_source", f"{source}.{nested_key}")
                    elements.append(nested_copy)

        for item in _as_list(page_mapping.get("controls")):
            runtime_item = self._runtime_control_to_element(item, "controls")
            if runtime_item:
                add_item(runtime_item, "runtime_profile.controls")

        for frame in _as_list(page_mapping.get("frames")):
            if not isinstance(frame, dict):
                continue
            for item in _as_list(frame.get("controls")):
                runtime_item = self._runtime_control_to_element(item, "frames.controls")
                if runtime_item:
                    add_item(runtime_item, "runtime_profile.frames.controls")

        for key in (
            "elements",
            "legacy_elements",
            "new_elements",
            "matched_elements",
            "locator_changes",
            "missing_legacy_elements",
            "missing_new_elements",
            "full_action_steps",
            "actions",
            "action_items",
            "test_actions",
        ):
            for item in _as_list(page_mapping.get(key)):
                add_item(item, key)

        for case in _as_list(page_mapping.get("executable_cases")):
            if not isinstance(case, dict):
                continue
            copied = dict(case)
            copied.setdefault("kind", "scenario")
            copied.setdefault("action_type", copied.get("case_type"))
            copied.setdefault("locator", copied.get("legacy_locator") or (copied.get("main_step") or {}).get("legacy_locator"))
            add_item(copied, "executable_cases")
            for step in _as_list(copied.get("pre_steps")):
                if isinstance(step, dict):
                    step_copy = dict(step)
                    step_copy.setdefault("kind", "file" if _as_text(step_copy.get("action_type")).lower() == "upload" else "scenario")
                    add_item(step_copy, "executable_cases.pre_steps")
            main_step = copied.get("main_step")
            if isinstance(main_step, dict):
                step_copy = dict(main_step)
                step_copy.setdefault("kind", "button")
                add_item(step_copy, "executable_cases.main_step")

        normalized: List[Dict[str, Any]] = []
        seen = set()
        for item in elements:
            key = (
                _as_text(item.get("kind")),
                _as_text(item.get("tag")),
                _locator(item),
                _label(item),
                _search_blob(item)[:240],
            )
            if key in seen:
                continue
            seen.add(key)
            normalized.append(item)
        return normalized

    def _runtime_control_to_element(self, control: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
        if not isinstance(control, dict):
            return None

        tag = _as_text(control.get("tag")).lower()
        input_type = _as_text(control.get("type")).lower()
        if control.get("visible") is False and tag not in {"form", "table"}:
            return None

        if tag == "form":
            kind = "form"
        elif tag == "table":
            kind = "table"
        elif tag == "a":
            kind = "link"
        elif tag == "button":
            kind = "button"
        elif tag == "select":
            kind = "select"
        elif tag == "textarea":
            kind = "textarea"
        elif tag == "input" and input_type == "file":
            kind = "file"
        elif tag == "input" and input_type == "hidden":
            kind = "hidden"
        elif tag == "input" and input_type in {"button", "submit", "reset", "image"}:
            kind = "button"
        elif tag == "input":
            kind = "field"
        elif control.get("onclick"):
            kind = "button"
        else:
            kind = tag or "unknown"

        raw_attrs = control.get("attributes")
        attrs = dict(raw_attrs) if isinstance(raw_attrs, dict) else {}
        for key in (
            "type",
            "name",
            "id",
            "value",
            "href",
            "onclick",
            "action",
            "formAction",
            "ownerFormAction",
            "ownerFormId",
            "ownerFormName",
            "method",
            "enctype",
            "target",
            "disabled",
        ):
            value = control.get(key)
            if value not in (None, ""):
                attrs.setdefault(key, value)

        selector = _as_text(control.get("selector"))
        if tag == "input" and input_type in {"checkbox", "radio"} and control.get("name") and control.get("value"):
            selector = (
                f'input[name="{_css_attr(control.get("name"))}"]'
                f'[type="{_css_attr(input_type)}"]'
                f'[value="{_css_attr(control.get("value"))}"]'
            )

        return {
            "kind": kind,
            "tag": tag,
            "attributes": attrs,
            "locator": selector,
            "label": _as_text(control.get("text") or control.get("value") or control.get("name") or control.get("id")),
            "raw": "" if tag in {"form", "table"} else _as_text(control.get("raw")),
            "action": _as_text(control.get("action") or control.get("formAction") or control.get("ownerFormAction")),
            "action_type": self._runtime_action_type(control, kind),
            "frame_index": control.get("frame_index"),
            "frame_url": control.get("frame_url"),
            "checked": bool(control.get("checked")),
            "selected_value": _as_text(control.get("selectedValue")),
            "options": control.get("options") if isinstance(control.get("options"), list) else [],
            "_source": source,
        }

    @staticmethod
    def _runtime_action_type(control: Dict[str, Any], kind: str) -> str:
        values = [
            control.get("selector"),
            control.get("onclick"),
            control.get("action"),
            control.get("formAction"),
            control.get("ownerFormAction"),
            control.get("href"),
            control.get("text"),
            control.get("value"),
            control.get("name"),
            control.get("id"),
        ]
        blob = " ".join(_as_text(value) for value in values if value is not None).lower()
        tag = _as_text(control.get("tag")).lower()
        input_type = _as_text(control.get("type")).lower()
        is_clickable = kind in {"button", "link"} or tag in {"a", "button"} or (tag == "input" and input_type in {"button", "submit", "reset", "image"})
        if "window.close" in blob or "parent.close" in blob:
            return "close_window"
        if "fncancel" in blob or "キャンセル" in blob or "取消" in blob or re.search(r"\b(?:cancel|back|bak|close)\b", blob):
            return "click"
        if is_clickable and ("download" in blob or "template" in blob or "ダウンロード" in blob or "fndownload" in blob):
            return "download"
        if "search" in blob:
            return "search"
        if "fnsubmit" in blob or "submitform" in blob or ".submit(" in blob or input_type == "submit":
            return "submit"
        if kind == "file":
            return "upload"
        if tag == "a" and control.get("href"):
            return "navigate"
        if kind == "button":
            return "click"
        return ""

    def _merge_declared_counts(self, counts: Dict[str, int], page_mapping: Dict[str, Any]) -> None:
        aliases = {
            "field": "input",
            "textarea": "textarea",
            "select": "select",
            "form": "form",
            "file": "file",
            "button": "button",
            "link": "link",
            "table": "table",
        }
        for source_key in ("counts", "legacy_counts", "new_counts"):
            source = page_mapping.get(source_key)
            if not isinstance(source, dict):
                continue
            for raw_key, raw_value in source.items():
                target = aliases.get(str(raw_key).lower())
                if not target:
                    continue
                try:
                    counts[target] = max(counts[target], int(raw_value))
                except Exception:
                    pass

    def _form_entry(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "locator": _locator(item) or "form",
            "action": _first_attr(item, "action") or _as_text(item.get("action")),
            "method": _first_attr(item, "method"),
            "enctype": _first_attr(item, "enctype"),
            "target": _first_attr(item, "target"),
            "label": _label(item),
        }

    def _file_entry(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "locator": _locator(item),
            "name": _first_attr(item, "name") or _field_name(item),
            "property": _first_attr(item, "property", "path") or _field_name(item),
            "action": _first_attr(item, "action") or _as_text(item.get("action")),
            "owner_form_name": _first_attr(item, "ownerFormName"),
            "owner_form_id": _first_attr(item, "ownerFormId"),
            "label": _label(item),
        }

    def _action_entry(self, item: Dict[str, Any], action_type: str) -> Dict[str, Any]:
        return {
            "locator": _locator(item),
            "onclick": _first_attr(item, "onclick", "onClick"),
            "action": _first_attr(item, "action") or _as_text(item.get("action")),
            "owner_form_name": _first_attr(item, "ownerFormName"),
            "owner_form_id": _first_attr(item, "ownerFormId"),
            "action_type": action_type,
            "label": _label(item),
        }

    def _control_entry(self, item: Dict[str, Any]) -> Dict[str, Any]:
        attrs = _attributes(item)
        return {
            "locator": _locator(item) or "table",
            "label": _label(item),
            "tag": _as_text(item.get("tag")).lower(),
            "type": _first_attr(item, "type").lower(),
            "name": _first_attr(item, "name"),
            "value": _first_attr(item, "value"),
            "checked": bool(item.get("checked")) or "checked" in {str(key).lower() for key in attrs},
            "selected_value": _as_text(item.get("selected_value") or item.get("selectedValue")),
            "options": item.get("options") if isinstance(item.get("options"), list) else [],
        }


class CaseTemplateRegistry:
    def __init__(self, templates: Optional[List[Dict[str, Any]]] = None) -> None:
        self.templates = list(templates or DEFAULT_CASE_TEMPLATES)

    def list_templates(self) -> List[Dict[str, Any]]:
        return list(self.templates)


class CaseExpansionRules:
    def expand(self, profile: Dict[str, Any], seed_cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        expanded: List[Dict[str, Any]] = []
        seen = {case.get("case_id") for case in seed_cases}
        for case in seed_cases:
            for spec in self._specs_for(case, profile):
                item = self._build(case, spec)
                case_id = item.get("case_id")
                if not case_id or case_id in seen:
                    continue
                seen.add(case_id)
                expanded.append(item)
        return expanded

    def _specs_for(self, case: Dict[str, Any], profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        case_type = _as_text(case.get("case_type") or case.get("template_id")).lower()
        if case_type == "initial_display":
            return [
                {
                    "viewpoint_id": "layout_text",
                    "title": "主要文言・レイアウト確認",
                    "objective": "初期表示の主要文言、位置、余白、表示欠落が移行前後で同等であることを確認する。",
                    "steps": "対象画面到達後、ページ全体スクリーンショットと主要テキストを比較する。",
                    "expected": "白画面、文字化け、主要文言欠落、レイアウト崩れがない。",
                    "expected_type": "visual",
                    "automation_mode": "auto",
                    "priority_offset": 1,
                },
                {
                    "viewpoint_id": "script_error",
                    "title": "JS/HTTP エラー確認",
                    "objective": "画面表示時に JavaScript エラー、HTTP エラー、権限エラーが発生しないことを確認する。",
                    "steps": "対象画面到達後、コンソール/ネットワーク/画面状態を比較する。",
                    "expected": "Legacy/New とも業務利用を阻害するエラーが発生しない。",
                    "expected_type": "visual",
                    "automation_mode": "auto",
                    "priority_offset": 2,
                },
            ]
        if case_type == "upload_select":
            return [
                {
                    "viewpoint_id": "file_control_state",
                    "title": "ファイル選択後の制御状態確認",
                    "objective": "ファイル選択後に入力欄、ボタン活性、表示状態が移行前後で同等であることを確認する。",
                    "steps": "file input に自動化用ファイルを設定し、画面状態を比較する。",
                    "expected": "選択ファイルが反映され、JS エラーや活性状態の差分がない。",
                    "expected_type": "control_state",
                    "automation_mode": "auto",
                    "priority_offset": 1,
                },
            ]
        if case_type == "upload_submit":
            return [
                {
                    "viewpoint_id": "confirm_transition",
                    "title": "アップロード確認画面遷移確認",
                    "objective": "正常ファイル送信後の確認画面、メッセージ、遷移先が移行前後で同等であることを確認する。",
                    "steps": "正常ファイルを設定して submit し、遷移後の画面状態を比較する。",
                    "expected": "確認画面または業務メッセージが同等に表示される。",
                    "expected_type": "page_or_message",
                    "automation_mode": "auto",
                    "priority_offset": 1,
                },
                {
                    "viewpoint_id": "invalid_format",
                    "title": "不正フォーマットファイル確認",
                    "objective": "業務フォーマット不正ファイルを送信した場合のエラー制御を確認する。",
                    "steps": "不正フォーマットのアップロードファイルを指定し、submit 後のエラー表示を比較する。",
                    "expected": "業務エラーが同等に表示され、500 エラーや空登録が発生しない。",
                    "test_data": "${UPLOAD_INVALID_FILE}",
                    "automation_mode": "semi-auto",
                    "enabled": "true",
                    "priority_offset": 20,
                },
                {
                    "viewpoint_id": "zero_byte",
                    "title": "0 byte ファイル確認",
                    "objective": "0 byte または空内容ファイル送信時の制御を確認する。",
                    "steps": "0 byte ファイルを指定し、submit 後の画面状態とメッセージを比較する。",
                    "expected": "仕様通りにエラーまたは受付が行われ、移行差分がない。",
                    "test_data": "${UPLOAD_EMPTY_FILE}",
                    "automation_mode": "semi-auto",
                    "enabled": "true",
                    "priority_offset": 21,
                },
                {
                    "viewpoint_id": "double_submit",
                    "title": "二重送信防止確認",
                    "objective": "アップロードボタンの連打や二重送信で重複登録が発生しないことを確認する。",
                    "steps": "正常ファイル設定後、submit 操作を短時間で複数回実行する。",
                    "expected": "二重登録、二重遷移、二重エラーが発生しない。",
                    "automation_mode": "manual/assist",
                    "enabled": "true",
                    "priority_offset": 60,
                },
            ]
        if case_type == "upload_without_file":
            return [
                {
                    "viewpoint_id": "required_message",
                    "title": "未選択エラーメッセージ確認",
                    "objective": "ファイル未選択時の必須エラー表示が移行前後で同等であることを確認する。",
                    "steps": "ファイルを設定せず submit locator を実行し、表示メッセージを比較する。",
                    "expected": "必須エラーまたは画面保持が同等で、500 エラーが発生しない。",
                    "expected_type": "message_or_stay",
                    "automation_mode": "auto",
                    "priority_offset": 1,
                },
            ]
        if case_type == "download_template":
            return [
                {
                    "viewpoint_id": "filename_extension",
                    "title": "ダウンロードファイル名・拡張子確認",
                    "objective": "テンプレートダウンロードのファイル名、拡張子、保存可否が同等であることを確認する。",
                    "steps": "ダウンロード locator を実行し、download event と保存ファイル名を確認する。",
                    "expected": "Legacy/New とも想定拡張子のファイルがダウンロードされる。",
                    "expected_type": "download",
                    "automation_mode": "auto",
                    "priority_offset": 1,
                },
                {
                    "viewpoint_id": "page_state_after_download",
                    "title": "ダウンロード後画面状態確認",
                    "objective": "ダウンロード実行後に元画面の入力状態や表示状態が不正に変化しないことを確認する。",
                    "steps": "ダウンロード実行前後の画面状態を比較する。",
                    "expected": "元画面が保持され、入力欄やボタン活性に意図しない差分がない。",
                    "expected_type": "download",
                    "automation_mode": "auto",
                    "priority_offset": 2,
                },
            ]
        if case_type == "file_download":
            return self._file_download_option_specs(case, profile)
        if case_type == "link_navigation":
            return [
                {
                    "viewpoint_id": "target_display",
                    "title": "リンク先表示確認",
                    "objective": "ヘルプ/参照リンクのリンク先が移行前後で同等に開けることを確認する。",
                    "steps": "対象リンクをクリックし、リンク先画面または popup の表示を比較する。",
                    "expected": "404、白画面、文字化け、popup ブロックが発生しない。",
                    "expected_type": "page_or_popup",
                    "automation_mode": "auto",
                    "priority_offset": 1,
                },
                {
                    "viewpoint_id": "return_to_origin",
                    "title": "リンク先から元画面復帰確認",
                    "objective": "リンク先を閉じる/戻る操作後に元画面へ安全に復帰できることを確認する。",
                    "steps": "リンク先表示後、閉じるまたは戻る操作を行い元画面状態を確認する。",
                    "expected": "元画面の入力状態、表示状態、セッションが保持される。",
                    "automation_mode": "manual/assist",
                    "enabled": "true",
                    "priority_offset": 40,
                },
            ]
        if case_type == "search_normal":
            return [
                {
                    "viewpoint_id": "empty_condition",
                    "title": "空条件検索確認",
                    "objective": "検索条件未入力時の一覧表示またはエラー制御が同等であることを確認する。",
                    "steps": "検索条件を空にして検索 locator を実行する。",
                    "expected": "仕様通りの結果またはメッセージが同等に表示される。",
                    "automation_mode": "semi-auto",
                    "enabled": "true",
                    "priority_offset": 20,
                },
                {
                    "viewpoint_id": "no_result",
                    "title": "0件検索結果確認",
                    "objective": "該当なし条件で検索した場合のメッセージ、一覧、ページング表示を確認する。",
                    "steps": "0件となる検索条件を設定し、検索後の画面を比較する。",
                    "expected": "0件メッセージ、空一覧、ページング表示が同等である。",
                    "automation_mode": "semi-auto",
                    "enabled": "true",
                    "priority_offset": 21,
                },
            ]
        if case_type == "result_table_verify":
            return [
                {
                    "viewpoint_id": "column_row_layout",
                    "title": "一覧列・行レイアウト確認",
                    "objective": "一覧の列、行、ヘッダ、空データ表示が移行前後で同等であることを確認する。",
                    "steps": "結果一覧領域をスクリーンショットで比較する。",
                    "expected": "列欠落、行崩れ、余計なスクロール、文字化けがない。",
                    "expected_type": "visual",
                    "automation_mode": "auto",
                    "priority_offset": 1,
                },
            ]
        if case_type == "close_window":
            return [
                {
                    "viewpoint_id": "close_recover",
                    "title": "閉じる後の復帰確認",
                    "objective": "閉じる/キャンセル操作後に親画面または再到達手順で復帰できることを確認する。",
                    "steps": "閉じる操作後、親画面状態と route map による再到達可否を確認する。",
                    "expected": "ブラウザ停止やセッション破壊が発生せず、必要に応じて対象画面へ再到達できる。",
                    "automation_mode": "manual/assist",
                    "enabled": "true",
                    "priority_offset": 40,
                },
            ]
        return []

    def _file_download_option_specs(self, case: Dict[str, Any], profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        controls = [item for item in profile.get("option_controls") or [] if isinstance(item, dict)]
        download_locator = _as_text(case.get("locator"))
        if not controls or not download_locator:
            return []

        variants = self._option_variants(controls)
        specs: List[Dict[str, Any]] = []
        for index, variant in enumerate(variants, start=1):
            pre_steps = variant.get("pre_steps") or []
            if not pre_steps:
                continue
            viewpoint_id = "option_matrix_representative" if index == 1 else f"option_matrix_{variant.get('id', index)}"
            specs.append(
                {
                    "viewpoint_id": viewpoint_id,
                    "title": f"出力条件代表パターン確認：{variant.get('title')}",
                    "objective": "出力形式、番号形式、出力項目、チェックボックス等の代表条件でファイル出力が同等に行えることを確認する。",
                    "steps": f"{variant.get('description')}。条件設定後、出力 locator を実行する。",
                    "expected": "代表条件ごとに Legacy/New ともファイル出力が開始され、エラーや想定外 close が発生しない。",
                    "expected_type": "download",
                    "automation_mode": "auto",
                    "enabled": "true",
                    "priority_offset": 30 + index,
                    "pre_steps": json.dumps(pre_steps, ensure_ascii=False),
                    "main_step": json.dumps({"action_type": "download", "locator": download_locator}, ensure_ascii=False),
                }
            )
        return specs[:3]

    def _option_variants(self, controls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        variants: List[Dict[str, Any]] = []
        check_controls = [
            item for item in controls
            if _as_text(item.get("tag")).lower() == "input"
            and _as_text(item.get("type")).lower() in {"checkbox", "radio"}
            and item.get("locator")
        ]
        select_controls = [
            item for item in controls
            if _as_text(item.get("tag")).lower() == "select" and item.get("locator")
        ]

        if check_controls:
            variants.append(
                {
                    "id": "all_selected",
                    "title": "全チェック",
                    "description": "表示されている checkbox/radio を代表的に選択状態へ変更する",
                    "pre_steps": [
                        {"action_type": "check", "locator": item.get("locator")}
                        for item in check_controls
                    ],
                }
            )

            first_locator = check_controls[0].get("locator")
            variants.append(
                {
                    "id": "minimum_selected",
                    "title": "最小選択",
                    "description": "先頭 option のみを選択し、その他の checkbox/radio を解除する",
                    "pre_steps": [
                        {
                            "action_type": "check" if item.get("locator") == first_locator else "uncheck",
                            "locator": item.get("locator"),
                        }
                        for item in check_controls
                    ],
                }
            )

            variants.append(
                {
                    "id": "alternate_selected",
                    "title": "代替選択",
                    "description": "偶数/奇数位置を切り替えた代替 checkbox/radio 条件へ変更する",
                    "pre_steps": [
                        {
                            "action_type": "check" if index % 2 else "uncheck",
                            "locator": item.get("locator"),
                        }
                        for index, item in enumerate(check_controls)
                    ],
                }
            )

        for select in select_controls:
            options = [
                option for option in select.get("options") or []
                if isinstance(option, dict) and not option.get("disabled") and _as_text(option.get("value"))
            ]
            selected_value = _as_text(select.get("selected_value"))
            alternate = next((option for option in options if _as_text(option.get("value")) != selected_value), None)
            if not alternate:
                continue
            variants.append(
                {
                    "id": f"select_{_safe_case_id(select.get('name') or select.get('label'))}",
                    "title": f"{select.get('label') or select.get('name') or 'select'} 代替値",
                    "description": "select 項目を代表的な代替値へ変更する",
                    "pre_steps": [
                        {
                            "action_type": "select",
                            "locator": select.get("locator"),
                            "value": _as_text(alternate.get("value")),
                        }
                    ],
                }
            )

        if check_controls and select_controls:
            check_variants = [item for item in variants if not _as_text(item.get("id")).startswith("select_")]
            select_variants = [item for item in variants if _as_text(item.get("id")).startswith("select_")]
            mixed: List[Dict[str, Any]] = []
            for bucket in (
                check_variants[:1],
                select_variants[:1],
                check_variants[1:2],
                select_variants[1:2],
                check_variants[2:],
                select_variants[2:],
            ):
                mixed.extend(bucket)
            return mixed
        return variants

    @staticmethod
    def _build(seed: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
        parent_case_id = _as_text(seed.get("case_id"))
        viewpoint_id = _as_text(spec.get("viewpoint_id"), "viewpoint")
        item = dict(seed)
        item.update(
            {
                "case_id": f"{parent_case_id}-{viewpoint_id}" if parent_case_id else "",
                "parent_case_id": parent_case_id,
                "viewpoint_id": viewpoint_id,
                "title": spec.get("title", seed.get("title")),
                "objective": spec.get("objective", seed.get("objective")),
                "steps": spec.get("steps", seed.get("steps")),
                "expected": spec.get("expected", seed.get("expected")),
                "automation_mode": spec.get("automation_mode", seed.get("automation_mode", "auto")),
                "enabled": spec.get("enabled", seed.get("enabled", "true")),
                "generated_by": "CaseExpansionRules",
                "template_id": seed.get("template_id"),
                "priority": int(seed.get("priority", 999)) + int(spec.get("priority_offset", 0)),
            }
        )
        for key in ("action_type", "case_type", "locator", "submit_locator", "expected_type", "expected_value", "test_data", "pre_steps", "main_step"):
            if key in spec:
                item[key] = spec[key]
        if spec.get("test_data") and item.get("pre_steps"):
            replacement = _as_text(spec.get("test_data"))
            previous = _as_text(seed.get("test_data"), "${UPLOAD_FILE}")
            item["pre_steps"] = _as_text(item.get("pre_steps")).replace(previous, replacement).replace("${UPLOAD_FILE}", replacement)
        if item.get("automation_mode") != "auto" and "enabled" not in spec:
            item["enabled"] = "true"
        return item



class PageCasePlanner:
    def __init__(
        self,
        profile_builder: Optional[PageProfileBuilder] = None,
        registry: Optional[CaseTemplateRegistry] = None,
        expansion_rules: Optional[CaseExpansionRules] = None,
    ) -> None:
        self.profile_builder = profile_builder or PageProfileBuilder()
        self.registry = registry or CaseTemplateRegistry()
        self.expansion_rules = expansion_rules or CaseExpansionRules()

    def plan(self, page_mapping: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        profile = self.profile_builder.build(page_mapping)
        cases: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        capabilities = profile.get("capabilities") or {}

        for template in sorted(self.registry.list_templates(), key=lambda item: int(item.get("priority", 999))):
            required = list(template.get("requires") or [])
            missing = [name for name in required if not capabilities.get(name)]
            if missing:
                skipped.append(self._skip(profile, template, "missing required capabilities", missing))
                continue

            case, reason = self._instantiate(profile, template)
            if not case:
                skipped.append(self._skip(profile, template, reason or "not applicable to page profile", []))
                continue
            cases.append(case)

        if not cases:
            initial = next((template for template in self.registry.list_templates() if template.get("template_id") == "initial_display"), DEFAULT_CASE_TEMPLATES[0])
            fallback, _ = self._instantiate(profile, initial)
            if fallback:
                cases.append(fallback)

        cases.extend(self.expansion_rules.expand(profile, cases))
        cases.sort(key=lambda item: (bool(item.get("destructive")), int(item.get("priority", 999)), item.get("case_id", "")))
        return cases, skipped, profile

    def _instantiate(self, profile: Dict[str, Any], template: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
        template_id = _as_text(template.get("template_id"))
        page_id = _as_text(profile.get("page_id"), "<unknown>")

        locator = "__page__"
        test_data = ""
        submit_locator = ""
        expected_type = "visual"
        expected_value = ""
        pre_steps = ""
        main_step = ""

        if template_id == "initial_display":
            title = "画面初期表示確認"
            objective = "Legacy/New の同一業務入口から画面が正常に表示されることを確認する。"
            steps = "対象画面に到達し、スクリーンショット、主要文言、HTTP/JS エラー有無を比較する。"
            expected = "両環境で対象画面が表示され、重大な表示欠落や白画面がない。"
        elif template_id == "upload_select":
            file_item = self._first(profile, "files")
            locator = _as_text(file_item.get("locator"))
            if not locator:
                return None, "missing file locator"
            test_data = "${UPLOAD_FILE}"
            expected_type = "control_state"
            title = "ファイル選択欄操作確認"
            objective = "ファイル選択欄に自動化用アップロードファイルを設定できることを確認する。"
            steps = "file input にテストファイルを設定し、選択後の画面状態を比較する。"
            expected = "Legacy/New とも同じ file input にファイルが設定され、JS エラーや表示崩れがない。"
        elif template_id == "upload_submit":
            file_item = self._first(profile, "files")
            submit_item = self._best_submit_for_file(profile, file_item)
            locator = _as_text(file_item.get("locator"))
            submit_locator = _as_text(submit_item.get("locator"))
            if not locator:
                return None, "missing file locator"
            if not submit_locator:
                return None, "missing submit locator"
            test_data = "${UPLOAD_FILE}"
            expected_type = "page_or_message"
            pre_steps = json.dumps([{"action_type": "upload", "locator": locator, "value": test_data}], ensure_ascii=False)
            main_step = json.dumps({"action_type": "submit", "locator": submit_locator}, ensure_ascii=False)
            title = "ファイルアップロード主経路確認"
            objective = "ファイル選択後に submit し、アップロード主経路の移行差分を確認する。"
            steps = "file input にテストファイルを設定し、submit locator を実行する。"
            expected = "Legacy/New ともアップロード後の遷移、メッセージ、画面状態が同等である。"
        elif template_id == "upload_without_file":
            submit_item = self._best_submit_for_file(profile, self._first(profile, "files"))
            locator = _as_text(submit_item.get("locator"))
            submit_locator = locator
            if not locator:
                return None, "missing submit locator"
            expected_type = "message_or_stay"
            title = "未選択アップロード確認"
            objective = "ファイル未選択で submit した場合のエラー制御を確認する。"
            steps = "ファイルを設定せず submit locator を実行する。"
            expected = "業務エラーまたは画面保持となり、500 エラーや空登録が発生しない。"
        elif template_id == "download_template":
            download_item = self._first(profile, "template_download_actions")
            locator = _as_text(download_item.get("locator"))
            if not locator:
                return None, "missing download locator"
            expected_type = "download"
            title = "テンプレートダウンロード確認"
            objective = "テンプレート/雛形ダウンロード操作が移行前後で同等に動作することを確認する。"
            steps = "ダウンロード locator を実行し、download event と画面状態を確認する。"
            expected = "Legacy/New ともダウンロードが開始され、画面に重大差分がない。"
        elif template_id == "file_download":
            download_item = self._first(profile, "file_download_actions")
            locator = _as_text(download_item.get("locator"))
            if not locator:
                return None, "missing file output locator"
            expected_type = "download"
            title = "ファイル出力ダウンロード確認"
            objective = "出力/ファイル出力/PDF出力ボタンで業務ファイルが移行前後同等にダウンロードされることを確認する。"
            steps = "出力 locator を実行し、download event、保存ファイル名、出力後の画面 close/保持状態を確認する。"
            expected = "Legacy/New とも業務ファイルがダウンロードされ、画面 close が発生しても次 case 前に対象画面へ復帰できる。"
        elif template_id == "link_navigation":
            link_item = self._first(profile, "navigation_links")
            locator = _as_text(link_item.get("locator"))
            if not locator:
                return None, "missing link locator"
            expected_type = "page_or_popup"
            title = "リンク遷移確認"
            objective = "ヘルプや参照リンクが移行前後で同等に動作することを確認する。"
            steps = "対象リンク locator を実行し、リンク先の表示、popup、遷移状態を比較する。"
            expected = "Legacy/New ともリンク先に重大差分がなく、元画面の状態が不正に変化しない。"
        elif template_id == "close_window":
            close_item = self._first(profile, "close_actions")
            locator = _as_text(close_item.get("locator"))
            if not locator:
                return None, "missing close locator"
            expected_type = "window_closed"
            title = "閉じる/キャンセル動作確認"
            objective = "閉じる/キャンセル操作で対象画面が仕様通り閉じることを確認する。"
            steps = "close locator を実行し、ウィンドウ close または前画面復帰を確認する。"
            expected = "対象ウィンドウが閉じる、または仕様通り戻る。親画面に異常がない。"
        elif template_id == "search_normal":
            search_item = self._first(profile, "search_actions")
            locator = _as_text(search_item.get("locator"))
            if not locator:
                return None, "missing search locator"
            expected_type = "page_or_message"
            title = "検索主経路確認"
            objective = "検索条件入力後の検索操作が移行前後で同等に動作することを確認する。"
            steps = "検索条件がある場合は自動化データを入力し、検索 locator を実行する。"
            expected = "Legacy/New とも検索後の遷移、メッセージ、結果領域が同等である。"
        elif template_id == "create_action":
            create_item = self._first(profile, "create_actions")
            locator = _as_text(create_item.get("locator"))
            if not locator:
                return None, "missing create locator"
            expected_type = "db_operation"
            title = "登録/追加ボタン動作確認"
            objective = "登録・追加系操作の前後で画面状態が仕様通り変化することを確認する。"
            steps = "テスト用データを入力済みの状態で登録/追加 locator を実行し、操作前後の画面状態を比較する。"
            expected = "Legacy/New とも各環境内の操作前後差分が同じ傾向となり、エラーや想定外遷移が発生しない。"
        elif template_id == "update_action":
            update_item = self._first(profile, "update_actions")
            locator = _as_text(update_item.get("locator"))
            if not locator:
                return None, "missing update locator"
            expected_type = "db_operation"
            title = "更新/保存ボタン動作確認"
            objective = "更新・保存系操作の前後で画面状態が仕様通り変化することを確認する。"
            steps = "テスト対象データを編集済みの状態で更新/保存 locator を実行し、操作前後の画面状態を比較する。"
            expected = "Legacy/New とも各環境内の操作前後差分が同じ傾向となり、エラーや想定外遷移が発生しない。"
        elif template_id == "result_table_verify":
            table_item = self._first(profile, "tables")
            locator = _as_text(table_item.get("locator") or "__page__")
            expected_type = "visual"
            title = "結果一覧表示確認"
            objective = "結果 table/list の表示状態が移行前後で同等であることを確認する。"
            steps = "初期表示または検索後の結果領域をスクリーンショットで比較する。"
            expected = "列、行、メッセージ、空データ表示に重大差分がない。"
        elif template_id == "back_action":
            back_item = self._first(profile, "back_actions")
            locator = _as_text(back_item.get("locator"))
            if not locator:
                return None, "missing back locator"
            expected_type = "page_or_message"
            title = "戻るボタン動作確認"
            objective = "戻るボタン押下時に仕様通り前画面へ戻り、セッションや親画面状態が壊れないことを確認する。"
            steps = "戻る locator をクリックし、遷移先画面または復帰後の画面状態を比較する。"
            expected = "Legacy/New とも仕様通り前画面へ戻り、白画面・エラー・セッション破壊が発生しない。"
        elif template_id == "delete_action":
            delete_item = self._first(profile, "delete_actions")
            locator = self._stable_delete_locator(delete_item) or _as_text(delete_item.get("locator"))
            if not locator:
                return None, "missing delete locator"
            expected_type = "confirm_or_message"
            title = "削除ボタン動作確認"
            objective = "一覧行の削除ボタンが表示され、削除確認または削除処理の入口が移行前後で同等であることを確認する。"
            steps = "テスト用データ行の削除 locator を操作し、確認ダイアログ、メッセージ、一覧更新を確認する。"
            expected = "Legacy/New とも確認・削除後のメッセージ・一覧状態が同等で、対象外データを誤削除しない。"
            test_data = "${DELETE_TEST_ROW}"
        elif template_id == "negative_js_error":
            trigger_item = self._best_negative_trigger(profile)
            locator = _as_text(trigger_item.get("locator") or "__page__")
            expected_type = "console_error"
            expected_value = "CONSOLE:error,JS_ERROR"
            title = "JS エラー検知確認"
            objective = "Playwright から console.error と JavaScript runtime error を注入し、エラー証跡がレポートに残ることを確認する。"
            steps = "対象画面で simulated JS error を発火し、必要に応じて代表 locator をクリックして console evidence を取得する。"
            expected = "Legacy/New とも JS エラー検知結果と console evidence 画像がレポートに出力される。"
        elif template_id in {"negative_http_500", "negative_network_abort"}:
            file_item = self._first(profile, "files")
            trigger_item = self._best_submit_for_file(profile, file_item) if file_item else self._best_negative_trigger(profile)
            locator = _as_text(trigger_item.get("locator"))
            if not locator:
                return None, "missing trigger locator"
            expected_value = self._route_pattern_for_action(trigger_item) or "**/*"
            expected_type = "http_error" if template_id == "negative_http_500" else "network_abort"
            if file_item:
                file_locator = _as_text(file_item.get("locator"))
                if file_locator:
                    test_data = "${UPLOAD_FILE}"
                    pre_steps = json.dumps([{"action_type": "upload", "locator": file_locator, "value": test_data}], ensure_ascii=False)
                    main_step = json.dumps({"action_type": template.get("action_type"), "locator": locator, "expected_value": expected_value}, ensure_ascii=False)
            if template_id == "negative_http_500":
                title = "HTTP 500 応答時エラー処理確認"
                objective = "submit/遷移リクエストを Playwright route で HTTP 500 に置き換え、画面状態と console/network 証跡を確認する。"
                steps = "対象 submit locator に対して HTTP 500 mock を設定し、実操作後の画面と console evidence を取得する。"
                expected = "白画面化、無限待機、ブラウザ停止が発生せず、HTTP 500 の証跡がレポートに残る。"
            else:
                title = "通信中断時エラー処理確認"
                objective = "submit/遷移リクエストを Playwright route で abort し、通信失敗時の画面状態と証跡を確認する。"
                steps = "対象 submit locator に対して network abort mock を設定し、実操作後の画面と console evidence を取得する。"
                expected = "通信中断時もブラウザ停止や不正遷移が発生せず、request failed の証跡がレポートに残る。"
        else:
            return None, "not applicable to page profile"

        matched = [name for name, enabled in (profile.get("capabilities") or {}).items() if enabled]
        if template_id in {"create_action", "update_action", "delete_action"}:
            automation_mode = "auto-db"
        elif template_id in NEGATIVE_CASE_TYPES:
            automation_mode = "auto-negative"
        else:
            automation_mode = "auto"
        return {
            "case_id": f"{_safe_case_id(page_id)}-{template_id}-001",
            "page_id": page_id,
            "title": title,
            "objective": objective,
            "precondition": "対象画面へ route map または手動接管で到達済みであること。",
            "steps": steps,
            "expected": expected,
            "severity": "High" if template_id not in {"result_table_verify"} else "Medium",
            "risk": "High" if template_id not in {"result_table_verify"} else "Medium",
            "automation_mode": automation_mode,
            "enabled": "true",
            "case_type": template.get("case_type"),
            "action_type": template.get("action_type"),
            "locator": locator,
            "test_data": test_data,
            "submit_locator": submit_locator,
            "expected_type": expected_type,
            "expected_value": expected_value,
            "pre_steps": pre_steps,
            "main_step": main_step,
            "generated_by": "PageCasePlanner",
            "profile_source": profile.get("profile_source"),
            "runtime_profile_path": profile.get("runtime_profile_path"),
            "matched_capabilities": ",".join(matched),
            "template_id": template_id,
            "priority": template.get("priority", 999),
            "destructive": "true" if template.get("destructive") else "false",
        }, ""

    @staticmethod
    def _best_submit_for_file(profile: Dict[str, Any], file_item: Dict[str, Any]) -> Dict[str, Any]:
        actions = [item for item in profile.get("submit_actions") or [] if isinstance(item, dict)]
        if not actions:
            return {}

        file_action = _as_text(file_item.get("action")).lower()
        file_form_name = _as_text(file_item.get("owner_form_name")).lower()
        file_form_id = _as_text(file_item.get("owner_form_id")).lower()

        def score(action: Dict[str, Any]) -> Tuple[int, str]:
            action_values = [
                action.get("locator"),
                action.get("label"),
                action.get("action"),
                action.get("onclick"),
                action.get("owner_form_name"),
                action.get("owner_form_id"),
            ]
            blob = " ".join(_as_text(value) for value in action_values if value is not None).lower()
            action_url = _as_text(action.get("action")).lower()
            points = 0
            if file_form_name and file_form_name in blob:
                points += 120
            if file_form_id and file_form_id in blob:
                points += 120
            if file_action and (file_action == action_url or file_action in action_url or action_url in file_action):
                points += 90
            if "upload" in blob:
                points += 40
            if "conf" in blob:
                points += 15
            if "logout" in blob or "logoff" in blob:
                points -= 250
            if "cancel" in blob or "back" in blob or "bak" in blob:
                points -= 120
            if _as_text(action.get("locator")).lower().startswith("form"):
                points -= 25
            return points, _as_text(action.get("locator"))

        return max(actions, key=score)

    @staticmethod
    def _best_negative_trigger(profile: Dict[str, Any]) -> Dict[str, Any]:
        buckets = [
            profile.get("submit_actions") or [],
            profile.get("search_actions") or [],
            profile.get("navigation_links") or [],
            profile.get("create_actions") or [],
            profile.get("update_actions") or [],
        ]
        actions = [item for bucket in buckets for item in bucket if isinstance(item, dict)]
        if not actions:
            return {}

        def score(action: Dict[str, Any]) -> Tuple[int, str]:
            blob = " ".join(_as_text(action.get(key)) for key in ("label", "action", "onclick", "locator")).lower()
            points = 0
            if any(token in blob for token in ("submit", "confirm", "conf", "検索", "確認", "送信")):
                points += 100
            if any(token in blob for token in ("search", "list", "main")):
                points += 20
            if any(token in blob for token in ("logout", "logoff", "close", "window.close", "削除", "delete")):
                points -= 250
            if "download" in blob or "template" in blob:
                points -= 120
            return points, _as_text(action.get("locator"))

        return max(actions, key=score)

    @staticmethod
    def _route_pattern_for_action(action: Dict[str, Any]) -> str:
        values = [
            action.get("action"),
            action.get("onclick"),
            action.get("href"),
            action.get("locator"),
        ]
        for value in values:
            text = _as_text(value)
            if not text:
                continue
            candidates = re.findall(r"['\"]([^'\"]+\.(?:do|jsp|action)(?:\?[^'\"]*)?)['\"]", text, flags=re.IGNORECASE)
            if not candidates and re.search(r"\.(?:do|jsp|action)(?:\?|$)", text, flags=re.IGNORECASE):
                candidates = [text]
            for candidate in candidates:
                cleaned = candidate.strip().replace("\\", "/")
                cleaned = cleaned.split("?", 1)[0].strip()
                cleaned = cleaned.lstrip("./")
                if not cleaned:
                    continue
                leaf = cleaned.rsplit("/", 1)[-1]
                if leaf:
                    return f"**/{leaf}*"
        return ""

    @staticmethod
    def _stable_delete_locator(delete_item: Dict[str, Any]) -> str:
        blob = " ".join(_as_text(delete_item.get(key)) for key in ("label", "onclick", "locator")).lower()
        if "deletefile" in blob:
            return 'input[onclick^="deleteFile("]'
        if "削除" in blob:
            return 'input[type="button"][value="削除"]'
        return ""

    @staticmethod
    def _first(profile: Dict[str, Any], key: str) -> Dict[str, Any]:
        values = profile.get(key)
        if isinstance(values, list) and values:
            return values[0] if isinstance(values[0], dict) else {}
        return {}

    @staticmethod
    def _skip(profile: Dict[str, Any], template: Dict[str, Any], reason: str, missing: List[str]) -> Dict[str, Any]:
        matched = [name for name, enabled in (profile.get("capabilities") or {}).items() if enabled]
        return {
            "page_id": profile.get("page_id"),
            "template_id": template.get("template_id"),
            "case_type": template.get("case_type"),
            "status": "skipped",
            "reason": reason,
            "missing_capabilities": ",".join(missing),
            "matched_capabilities": ",".join(matched),
        }
