import json
import re
from typing import Any, Dict, List, Set, Tuple


HELPER_OPERATION_TYPES = {
    "fill",
    "input",
    "select",
    "check",
    "uncheck",
    "choose",
    "prepare",
    "row_select",
    "wait",
}

DB_OPERATION_TYPES = {"create_action", "create", "update_action", "update", "delete_action", "delete"}
NEGATIVE_OPERATION_TYPES = {"negative_js_error", "negative_http_500", "negative_network_abort", "negative_file_upload"}
BROWSER_DIALOG_OPERATION_TYPES = {"browser_dialog", "dialog", "alert", "confirm", "prompt"}
PRINT_OPERATION_TYPES = {"print", "print_output", "print_dialog", "print_invocation"}


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _safe_case_id(value: Any) -> str:
    text = _as_text(value, "page").lower()
    text = text.rsplit(".", 1)[0] if "." in text else text
    return re.sub(r"[^a-z0-9_.-]+", "-", text).strip("-") or "page"


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _truthy(value: Any) -> bool:
    return _as_text(value).strip().lower() in {"true", "1", "yes", "y", "on"}


def _json(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    return json.dumps(value, ensure_ascii=False)


def _operation_type(operation: Dict[str, Any]) -> str:
    return _as_text(operation.get("type") or operation.get("case_type") or operation.get("action_type"), "operation").strip().lower()


def _step_action(step: Dict[str, Any], fallback: str = "click") -> str:
    action = _as_text(step.get("action_type") or step.get("action") or step.get("type")).strip().lower()
    if action == "input":
        return "fill"
    if action in {"choose_file", "file"}:
        return "upload"
    return action or fallback


def _normalize_step(step: Dict[str, Any], *, fallback_action: str = "click") -> Dict[str, Any]:
    action_type = _step_action(step, fallback=fallback_action)
    locator = _as_text(step.get("locator") or step.get("selector") or step.get("target") or step.get("submit_locator"))
    normalized: Dict[str, Any] = {"action_type": action_type}
    if locator:
        normalized["locator"] = locator
    for src, dst in (
        ("value", "value"),
        ("test_data", "value"),
        ("expected_value", "expected_value"),
        ("frame", "frame"),
        ("target_frame", "target_frame"),
        ("label", "label"),
    ):
        value = step.get(src)
        if value not in (None, "", [], {}):
            normalized[dst] = value
    return normalized


def _steps_from_operation(operation: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps = [
        _normalize_step(step)
        for step in _as_list(operation.get("steps"))
        if isinstance(step, dict)
    ]
    for step in _as_list(operation.get("pre_steps")):
        if isinstance(step, dict):
            steps.append(_normalize_step(step))

    if isinstance(operation.get("main_step"), dict):
        steps.append(_normalize_step(operation["main_step"]))

    for key, fallback_action in (
        ("input_controls", "fill"),
        ("select_controls", "select"),
        ("row_selectors", "check"),
    ):
        for item in _as_list(operation.get(key)):
            if isinstance(item, dict):
                steps.append(_normalize_step(item, fallback_action=fallback_action))

    for key, fallback_action in (
        ("submit", "click"),
        ("trigger", "click"),
    ):
        item = operation.get(key)
        if isinstance(item, dict):
            steps.append(_normalize_step(item, fallback_action=fallback_action))

    if not steps and _operation_type(operation) == "initial_display":
        steps.append({"action_type": "snapshot", "locator": "__page__"})
    return [step for step in steps if step.get("action_type")]


def _dedupe_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Set[str] = set()
    unique: List[Dict[str, Any]] = []
    for step in steps:
        key = json.dumps(step, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        unique.append(step)
    return unique


def _dependency_steps(
    operation: Dict[str, Any],
    operations_by_id: Dict[str, Dict[str, Any]],
    *,
    stack: Tuple[str, ...] = (),
) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for dependency_id in _as_list(operation.get("requires")):
        dep_id = _as_text(dependency_id)
        if not dep_id or dep_id in stack:
            continue
        dependency = operations_by_id.get(dep_id)
        if not dependency:
            continue
        output.extend(_dependency_steps(dependency, operations_by_id, stack=(*stack, dep_id)))
        output.extend(_steps_from_operation(dependency))
    return _dedupe_steps(output)


def _expected(operation: Dict[str, Any], case_type: str) -> Tuple[str, str]:
    expected = operation.get("expected")
    if isinstance(expected, dict):
        expected_type = _as_text(expected.get("type") or expected.get("expected_type"))
        expected_value = _as_text(expected.get("value") or expected.get("expected_value"))
    else:
        expected_type = ""
        expected_value = _as_text(expected)
    normalized_expected_type = expected_type.strip().lower()
    if case_type == "browser_dialog" and normalized_expected_type in {"", "dialog", "alert", "confirm", "prompt"}:
        expected_type = "browser_dialog"
    elif case_type == "print_output" and normalized_expected_type in {"", "print", "print_output", "print_dialog"}:
        expected_type = "print_invocation"
    if not expected_type:
        if case_type in {"download_template", "file_download", "download"}:
            expected_type = "download"
        elif case_type == "browser_dialog":
            expected_type = "browser_dialog"
        elif case_type == "print_output":
            expected_type = "print_invocation"
        elif case_type == "close_window":
            expected_type = "window_closed"
        elif case_type == "result_table_verify":
            expected_type = "visual"
        elif case_type in DB_OPERATION_TYPES:
            expected_type = "db_operation"
        else:
            expected_type = "page_or_message"
    return expected_type, expected_value


def _case_type_for(operation: Dict[str, Any], main_step: Dict[str, Any]) -> str:
    op_type = _operation_type(operation)
    action_type = _step_action(main_step, fallback=op_type)
    expected = operation.get("expected") if isinstance(operation.get("expected"), dict) else {}
    expected_type = _as_text(expected.get("type") if isinstance(expected, dict) else "").lower()
    if op_type in {"initial", "initial_display", "snapshot"}:
        return "initial_display"
    if op_type in BROWSER_DIALOG_OPERATION_TYPES or action_type in BROWSER_DIALOG_OPERATION_TYPES or expected_type == "browser_dialog":
        return "browser_dialog"
    if op_type in PRINT_OPERATION_TYPES or action_type in PRINT_OPERATION_TYPES or expected_type in {"print", "print_invocation"}:
        return "print_output"
    if op_type in {"search", "search_normal"}:
        return "search_normal"
    if op_type in {"result", "result_table", "result_verify", "result_table_verify"}:
        return "result_table_verify"
    if op_type in {"template_download", "download_template"}:
        return "download_template"
    if op_type in {"file_download", "file_output"}:
        return "file_download"
    if op_type == "download" and expected_type not in {"download", ""} and action_type != "download":
        return "link_navigation"
    if op_type == "download" or action_type == "download" or expected_type == "download":
        return "file_download"
    if op_type in {"upload_submit", "upload"}:
        return "upload_submit" if op_type == "upload_submit" else "upload_select"
    if op_type in {"close", "close_window"}:
        return "close_window"
    if op_type in {"navigate", "navigation", "link_navigation", "popup"}:
        return "link_navigation"
    if op_type in {"form", "reset", "clear", "form_reset"}:
        return "form_action"
    if op_type in {"create", "create_action"}:
        return "create_action"
    if op_type in {"update", "update_action"}:
        return "update_action"
    if op_type in {"delete", "delete_action"}:
        return "delete_action"
    if op_type in NEGATIVE_OPERATION_TYPES:
        return op_type
    return op_type


def _action_type_for(case_type: str, main_step: Dict[str, Any]) -> str:
    if case_type == "initial_display":
        return "snapshot"
    if case_type == "search_normal":
        return "search"
    if case_type == "result_table_verify":
        return "verify"
    if case_type in {"download_template", "file_download"}:
        return "download"
    if case_type == "browser_dialog":
        return "browser_dialog"
    if case_type == "print_output":
        return "print"
    if case_type == "upload_select":
        return "upload"
    if case_type == "upload_submit":
        return "upload_submit"
    if case_type in {"create_action", "update_action", "delete_action"}:
        return "click"
    return _step_action(main_step, fallback=case_type)


def _automation_mode(operation: Dict[str, Any], case_type: str) -> str:
    explicit = _as_text(operation.get("automation_mode"))
    if explicit:
        return explicit
    if case_type in DB_OPERATION_TYPES:
        return "auto-db"
    if case_type in NEGATIVE_OPERATION_TYPES:
        return "auto-negative"
    return "auto"


def _is_helper_only(operation: Dict[str, Any], dependent_ids: Set[str]) -> bool:
    op_id = _as_text(operation.get("id"))
    op_type = _operation_type(operation)
    if op_type not in HELPER_OPERATION_TYPES:
        return False
    if op_id in dependent_ids:
        return True
    return not operation.get("expected")


def page_spec_to_cases(spec: Dict[str, Any], evidence: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    page_id = _as_text(spec.get("page_id") or (evidence or {}).get("page_id"), "<unknown>")
    operations = [item for item in _as_list(spec.get("operations")) if isinstance(item, dict)]
    operations_by_id = {_as_text(operation.get("id")): operation for operation in operations if operation.get("id")}
    dependent_ids = {
        _as_text(dependency)
        for operation in operations
        for dependency in _as_list(operation.get("requires"))
        if _as_text(dependency)
    }
    capabilities = spec.get("capabilities") if isinstance(spec.get("capabilities"), dict) else {}
    matched_capabilities = ",".join(name for name, enabled in capabilities.items() if enabled)

    cases: List[Dict[str, Any]] = []
    for index, operation in enumerate(operations, start=1):
        if operation.get("enabled") is False:
            continue
        if _is_helper_only(operation, dependent_ids):
            continue

        op_steps = _steps_from_operation(operation)
        if not op_steps:
            continue
        pre_steps = _dependency_steps(operation, operations_by_id)
        pre_steps.extend(op_steps[:-1])
        pre_steps = _dedupe_steps(pre_steps)
        main_step = op_steps[-1]
        case_type = _case_type_for(operation, main_step)
        action_type = _action_type_for(case_type, main_step)
        expected_type, expected_value = _expected(operation, case_type)
        locator = _as_text(main_step.get("locator"), "__page__")
        title = _as_text(operation.get("title") or operation.get("name"), case_type)
        objective = _as_text(operation.get("objective") or operation.get("description") or spec.get("business_summary"))
        requires = ",".join(_as_text(item) for item in _as_list(operation.get("requires")) if _as_text(item))
        evidence_parts = [
            f"page_spec_operation={_as_text(operation.get('id'))}",
            f"from_state={_as_text(operation.get('from_state'))}",
            f"to_state={_as_text(operation.get('to_state'))}",
        ]
        if requires:
            evidence_parts.append(f"requires={requires}")

        cases.append(
            {
                "case_id": f"{_safe_case_id(page_id)}-{_safe_case_id(operation.get('id') or case_type)}-{index:03d}",
                "page_id": page_id,
                "title": title,
                "objective": objective,
                "steps": _as_text(operation.get("steps_text") or operation.get("description") or title),
                "expected": _as_text(operation.get("expected_text") or expected_value or expected_type),
                "severity": _as_text(operation.get("severity"), "High" if case_type != "result_table_verify" else "Medium"),
                "risk": _as_text(operation.get("risk"), "High" if case_type != "result_table_verify" else "Medium"),
                "automation_mode": _automation_mode(operation, case_type),
                "enabled": "true",
                "case_type": case_type,
                "action_type": action_type,
                "locator": locator,
                "test_data": _as_text(main_step.get("value") or main_step.get("test_data")),
                "submit_locator": _as_text(operation.get("submit_locator") or main_step.get("submit_locator")),
                "expected_type": expected_type,
                "expected_value": expected_value,
                "pre_steps": _json(pre_steps),
                "main_step": _json(main_step),
                "generated_by": "ManualPageSpec",
                "profile_source": _as_text((evidence or {}).get("source")),
                "runtime_profile_path": _as_text((evidence or {}).get("runtime_profile_path")),
                "matched_capabilities": matched_capabilities,
                "template_id": _as_text(operation.get("id") or case_type),
                "priority": _as_text(operation.get("priority"), str(10 + index)),
                "destructive": "true" if _truthy(operation.get("destructive")) or case_type in {"delete_action"} else "false",
                "evidence": "\n".join(part for part in evidence_parts if part and not part.endswith("=")),
            }
        )
    return cases


def page_spec_to_profile(spec: Dict[str, Any], evidence: Dict[str, Any] | None = None, *, page_spec_path: str = "") -> Dict[str, Any]:
    evidence = evidence or {}
    base = evidence.get("page_profile") if isinstance(evidence.get("page_profile"), dict) else {}
    profile = dict(base)
    capabilities = spec.get("capabilities") if isinstance(spec.get("capabilities"), dict) else {}
    profile.update(
        {
            "page_id": _as_text(spec.get("page_id") or evidence.get("page_id"), "<unknown>"),
            "profile_source": "manual_page_spec",
            "runtime_profile_path": _as_text(evidence.get("runtime_profile_path")),
            "page_spec_path": page_spec_path,
            "page_spec_summary": _as_text(spec.get("business_summary")),
            "page_type": _as_text(spec.get("page_type")),
            "state_count": len(_as_list(spec.get("states"))),
            "operation_count": len(_as_list(spec.get("operations"))),
            "capabilities": capabilities,
            "counts": evidence.get("counts") or base.get("counts") or {},
        }
    )
    return profile
