import json
import re
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Optional


PAGE_SPEC_SCHEMA = "moonlight.page_spec.v1"


PAGE_SPEC_PROMPT_TEMPLATE = """You are a senior migration-test planner for rendered legacy Japanese business web pages.
Return only one valid JSON object. Do not wrap it in Markdown.

Task:
Convert the provided page_evidence JSON into a comprehensive PageSpec JSON document.

Evidence priority:
1. validation_profile.validation_rules
2. static_dom_profile.search_item_categories
3. static_dom_profile.dynamic_input_mapping
4. operation_candidates
5. visible_snapshot / visible_controls

Important interpretation rules:
- page_evidence is not just a visible snapshot. Hidden DOM controls, display:none rows, tree items, checkbox-driven dynamic fields, and JavaScript validation arrays are first-class evidence.
- Do not generate one case per field. Group fields by business category and validation rule, then create representative checklist operations.
- For checkbox-driven dynamic fields, include the full operation path: expand category, check item checkbox, wait for/generated input, fill value, click search, assert validation message/result update.
- Use only locators present in evidence or deterministic locators derivable from evidence, such as input[name='itemId'][value='7040'] and input[name='i7040[0]'].
- If an operation depends on previous UI state, model that with requires/from_state/to_state.
- Browser alert/confirm/prompt dialogs are not DOM elements. Model the trigger as browser_dialog and expected.type browser_dialog; do not model the OK button.
- Browser print preview is outside page DOM. Model print as action_type print with expected.type print_invocation; do not model Edge/Chrome print preview controls.

Required JSON shape:
{
  "schema": "moonlight.page_spec.v1",
  "page_id": "...",
  "page_type": "display|search_page|upload_page|download_page|edit_page|menu|unknown",
  "business_summary": "...",
  "capabilities": {
    "initial_display": true,
    "search": false,
    "result_table": false,
    "file_upload": false,
    "upload_submit": false,
    "template_download": false,
    "file_download": false,
    "close_window": false,
    "popup": false,
    "browser_dialog": false,
    "print": false,
    "dynamic_search_fields": false,
    "frontend_validation": false
  },
  "states": [
    {"id": "initial", "description": "page loaded"}
  ],
  "operations": [
    {
      "id": "initial_display",
      "type": "initial_display",
      "title": "Initial display check",
      "automation_mode": "auto",
      "from_state": "route_ready",
      "to_state": "initial",
      "requires": [],
      "steps": [{"action_type": "snapshot", "locator": "__page__"}],
      "expected": {"type": "visual", "value": ""}
    }
  ],
  "notes": []
}

Allowed operation/action types:
- operation.type: initial_display, search, validation, result_table_verify, file_download, download_template, navigation, popup, browser_dialog, print, form_reset, upload, upload_submit, create_action, update_action, delete_action, close_window, helper
- step.action_type: snapshot, click, fill, select, check, uncheck, upload, submit, navigate, download, browser_dialog, print, wait, clear, set_value, press
- expected.type: visual, result_update, validation_message, page_or_message, download, browser_dialog, print_invocation, window_closed, page_or_popup, db_operation

Search-page planning rules:
- Build normal search operations from visible search controls and dynamic search items.
- Build representative validation operations from validation_profile, grouped by rule and field category.
- Date validation examples should use invalid date values such as "20261399".
- Word length / wildcard / IPC / number / bracket / neighbor rules should use values that trigger that rule.
- For dynamic item fields, steps should usually be:
  1. click the category toggle locator from search_item_categories.toggle_locator
  2. check the item checkbox locator
  3. wait for the dynamic input locator if evidence implies it is generated
  4. fill the dynamic input locator
  5. click the search submit locator from operation_candidates.search_actions
  6. expect validation_message or result_update

Output quality requirements:
- For a complex dynamic search page, produce enough operations to cover main search, dynamic field categories, representative validation groups, result/history, download/navigation/print/dialog actions when present.
- Do not collapse the page to only visible text boxes and buttons.
- Use automation_mode "auto" when all locators are deterministic; use "manual/assist" only when the evidence lacks a stable locator or prerequisite.

Page evidence JSON:
__EVIDENCE_JSON__
"""


class PageSpecError(RuntimeError):
    pass


def _safe_stem(value: Any) -> str:
    name = PureWindowsPath(str(value or "unknown").strip().replace("/", "\\")).name
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("_").lower() or "unknown"


def page_spec_cache_path(page_id: Any, output_dir: Path) -> Path:
    return output_dir / f"{_safe_stem(page_id)}.page_spec.json"


def page_evidence_cache_path(page_id: Any, output_dir: Path) -> Path:
    return output_dir / f"{_safe_stem(page_id)}.page_evidence.json"


def page_spec_prompt_cache_path(page_id: Any, output_dir: Path) -> Path:
    return output_dir / f"{_safe_stem(page_id)}.page_spec_prompt.md"


def load_cached_page_spec(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PageSpecError(f"Invalid PageSpec JSON: {path} ({exc})") from exc
    return validate_page_spec(payload)


def write_page_spec(path: Path, spec: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(validate_page_spec(spec), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_page_evidence(path: Path, evidence: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_page_spec_prompt(evidence: Dict[str, Any]) -> str:
    evidence_json = json.dumps(evidence, ensure_ascii=False, indent=2)
    return PAGE_SPEC_PROMPT_TEMPLATE.replace("__EVIDENCE_JSON__", evidence_json)


def write_page_spec_prompt(path: Path, evidence: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_page_spec_prompt(evidence), encoding="utf-8")


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json|JSON)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def parse_page_spec_json(text: str) -> Dict[str, Any]:
    stripped = _strip_code_fence(text)
    try:
        return validate_page_spec(json.loads(stripped))
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise PageSpecError("PageSpec response did not contain a JSON object")
        try:
            return validate_page_spec(json.loads(stripped[start : end + 1]))
        except json.JSONDecodeError as exc:
            raise PageSpecError(f"PageSpec JSON parse failed: {exc}") from exc


def validate_page_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(spec, dict):
        raise PageSpecError("PageSpec must be a JSON object")
    spec = dict(spec)
    spec["schema"] = str(spec.get("schema") or PAGE_SPEC_SCHEMA)
    spec["page_id"] = str(spec.get("page_id") or spec.get("target_page") or "<unknown>")
    if not isinstance(spec.get("capabilities"), dict):
        spec["capabilities"] = {}
    if not isinstance(spec.get("states"), list):
        spec["states"] = []
    if not isinstance(spec.get("operations"), list):
        spec["operations"] = []
    normalized_ops = []
    for index, operation in enumerate(spec["operations"], start=1):
        if not isinstance(operation, dict):
            continue
        item = dict(operation)
        item["id"] = str(item.get("id") or f"operation_{index:03d}")
        item["type"] = str(item.get("type") or item.get("case_type") or item.get("action_type") or "operation")
        if not isinstance(item.get("requires"), list):
            item["requires"] = []
        if not isinstance(item.get("steps"), list):
            item["steps"] = []
        normalized_ops.append(item)
    spec["operations"] = normalized_ops
    if not any(operation.get("type") == "initial_display" for operation in normalized_ops):
        spec["operations"].insert(
            0,
            {
                "id": "initial_display",
                "type": "initial_display",
                "title": "Initial display check",
                "automation_mode": "auto",
                "from_state": "route_ready",
                "to_state": "initial",
                "requires": [],
                "steps": [{"action_type": "snapshot", "locator": "__page__"}],
                "expected": {"type": "visual", "value": ""},
            },
        )
    return spec
