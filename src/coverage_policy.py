from collections import Counter
from typing import Any, Dict, Iterable, List, Set


CHECKLIST_SECTIONS = [
    {"id": "screen_layout", "title": "1-1 画面レイアウト", "mode": "AUTO", "expected": "Legacy/New screenshots are captured and compared."},
    {"id": "initial_display", "title": "1-2 画面初期表示", "mode": "AUTO", "expected": "Initial URL, DOM text, and visual state are compared."},
    {"id": "input_display", "title": "2-1 入力項目表示/初期値", "mode": "AUTO", "expected": "Visible controls, initial values, and generated field snapshots are compared automatically."},
    {"id": "input_operation", "title": "2-2 入力項目操作", "mode": "AUTO", "expected": "Fill, clear, select, hidden value, upload, and generated operation data are executed automatically when locators exist."},
    {"id": "error_message", "title": "2-3 エラー制御", "mode": "AUTO", "expected": "Required, empty, length, numeric, invalid-character, XSS, and SQL-like generated data are executed and compared when locators exist."},
    {"id": "search_event", "title": "3 検索/イベント/ボタン押下", "mode": "AUTO", "expected": "Buttons, links, submit controls, and action results are compared."},
    {"id": "browser_operation", "title": "4 イレギュラー操作", "mode": "AUTO", "expected": "Closed pages, popups, recovery/back behavior, and close-window buttons are recorded automatically when controls exist."},
    {"id": "screen_transition", "title": "5 画面遷移", "mode": "AUTO", "expected": "Navigation and post-action URLs/screenshots are compared."},
    {"id": "file_download", "title": "6 ファイル出力", "mode": "AUTO", "expected": "Download actions save files and compare resulting UI state."},
    {"id": "file_upload", "title": "7 ファイルアップロード", "mode": "AUTO", "expected": "File inputs receive sample files and post-upload state is compared."},
    {"id": "special_key", "title": "8 特殊キー", "mode": "AUTO", "expected": "Enter and generated key-press cases are executed automatically when a target control or page-level key action exists."},
    {"id": "print_output", "title": "9 帳票印刷", "mode": "MANUAL", "expected": "HTML/PDF/TXT/CSV layout and printed output require artifact inspection."},
    {"id": "mail_send", "title": "10 メール送信", "mode": "MANUAL", "expected": "Sender, recipient, subject, body, and attachment confirmation needs mail-server evidence."},
    {"id": "external_integration", "title": "11 外部連携方式", "mode": "MANUAL", "expected": "External system responses and generated interface files need environment evidence."},
    {"id": "permission", "title": "12 権限の確認", "mode": "MANUAL", "expected": "Requires role-specific login entries and expected access matrix."},
    {"id": "multi_browser", "title": "13 マルチブラウザ動作確認", "mode": "AUTO", "expected": "Covered when the same regression is run per browser/login entry."},
]

# checklist_generator.py reads this policy to expand element-level cases into viewpoint-level checklist rows.
# Depth is intentionally conservative enough for 1,600+ pages, but richer than simple one-click coverage.
CASE_DEPTH = {
    "High": "full",
    "Medium": "standard",
    "Low": "smoke",
}

ELEMENT_CASE_LIMIT_HINT = {
    "page": {"smoke": 3, "standard": 5, "full": 7},
    "form": {"smoke": 3, "standard": 5, "full": 7},
    "field": {"smoke": 2, "standard": 4, "full": 7},
    "hidden": {"smoke": 1, "standard": 2, "full": 3},
    "file": {"smoke": 3, "standard": 7, "full": 12},
    "button": {"smoke": 2, "standard": 5, "full": 8},
    "link": {"smoke": 2, "standard": 5, "full": 8},
}


def infer_result_coverage(item: Dict[str, Any]) -> List[str]:
    action = str(item.get("action") or "").lower()
    action_type = str(item.get("action_type") or _action_type_from_payload(item) or "").lower()
    text = " ".join(
        str(value or "").lower()
        for value in (
            action,
            action_type,
            item.get("legacy_locator"),
            item.get("new_locator"),
            (item.get("legacy_action") or {}).get("download_path"),
            (item.get("new_action") or {}).get("download_path"),
        )
    )
    coverage: Set[str] = set()

    if action == "page_snapshot":
        coverage.update({"screen_layout", "initial_display", "input_display"})

    if action_type in {"click", "submit", "navigate", "download", "close_window"}:
        coverage.add("search_event")

    if action_type in {"navigate", "submit"}:
        coverage.add("screen_transition")

    if action_type in {"close_window"} or _page_closed(item):
        coverage.add("browser_operation")

    if action_type == "upload" or "upload" in text or "input[type='file']" in text or "input[type=\"file\"]" in text:
        coverage.add("file_upload")

    if action_type == "download" or "download_path" in text or "download" in text:
        coverage.add("file_download")

    if action_type in {"fill", "select", "clear", "set_value"}:
        coverage.add("input_operation")
        coverage.add("error_message")

    if action_type == "press":
        coverage.add("special_key")

    if _popup_opened(item) or "recover" in text:
        coverage.add("browser_operation")

    return sorted(coverage)


def coverage_matrix(results: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Counter[str] = Counter()
    status_by_section: Dict[str, Counter[str]] = {}
    for item in results:
        for section_id in infer_result_coverage(item):
            counts[section_id] += 1
            status_by_section.setdefault(section_id, Counter())[str(item.get("status") or "UNKNOWN")] += 1

    rows: List[Dict[str, Any]] = []
    for section in CHECKLIST_SECTIONS:
        section_id = section["id"]
        hit_count = counts.get(section_id, 0)
        mode = section["mode"]
        if hit_count:
            state = "AUTO"
        elif mode == "AUTO":
            state = "GAP"
        elif mode == "PARTIAL":
            state = "MANUAL_GAP"
        else:
            state = "MANUAL"
        rows.append({**section, "state": state, "automated_cases": hit_count, "statuses": dict(status_by_section.get(section_id, Counter()))})
    return rows


def _action_type_from_payload(item: Dict[str, Any]) -> str:
    for key in ("legacy_action", "new_action"):
        value = item.get(key) or {}
        if value.get("semantic_action"):
            return str(value.get("semantic_action"))
    return ""


def _page_closed(item: Dict[str, Any]) -> bool:
    return any(bool((item.get(key) or {}).get("page_closed_after_action")) for key in ("legacy_action", "new_action")) or item.get("legacy_url") == "about:closed" or item.get("new_url") == "about:closed"


def _popup_opened(item: Dict[str, Any]) -> bool:
    return any(bool((item.get(key) or {}).get("popup_opened")) for key in ("legacy_action", "new_action"))
