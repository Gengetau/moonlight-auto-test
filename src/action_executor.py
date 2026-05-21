from playwright.sync_api import Frame, Page, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
import os
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from src.assert_engine import compare_visual_screenshot


def _safe_name(value: Any) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown")).strip("_")
    return name[:120] or "unknown"


ACTION_ALIASES = {
    "button": "click",
    "click": "click",
    "link": "navigate",
    "navigate": "navigate",
    "goto": "goto",
    "field": "fill",
    "fill": "fill",
    "input": "fill",
    "text": "fill",
    "textarea": "fill",
    "select": "select",
    "change": "select",
    "form": "submit",
    "submit": "submit",
    "file": "upload",
    "upload": "upload",
    "download": "download",
    "wait": "wait",
    "snapshot": "wait",
}


def infer_semantic_action(action_type: Optional[str], context: Optional[Dict[str, Any]] = None) -> str:
    """
    Normalize scanner/mapping hints into an executable semantic action.

    The mapping files mix historical action names with JSP scanner concepts such
    as kind=form and action_hint=submit. This function is intentionally
    conservative: explicit upload/select/form hints win, then aliases are used.
    """
    context = context or {}
    raw_values = [
        action_type,
        context.get("action_type"),
        context.get("action_hint"),
        context.get("kind"),
        context.get("tag"),
    ]
    raw = " ".join(str(value or "").lower() for value in raw_values)
    locator = str(context.get("locator") or context.get("selector") or "").lower()
    evidence = " ".join(str(context.get(key) or "").lower() for key in ("raw", "label", "semantic_key"))

    if "upload" in raw or "file" in raw or "type='file'" in evidence or 'type="file"' in evidence:
        return "upload"
    if "select" in raw or ":select" in evidence or " select" in evidence:
        return "select"
    if "form" in raw or "submit" in raw or locator.startswith("form") or "[name=" in locator and "form" in locator:
        return "submit"
    if "download" in raw or "download" in evidence:
        return "download"
    if "navigate" in raw or "link" in raw or "href" in evidence:
        return "navigate"
    if "input" in raw or "field" in raw or "textarea" in raw or ":input" in evidence:
        return "fill"
    for value in raw_values:
        alias = ACTION_ALIASES.get(str(value or "").lower())
        if alias:
            return alias
    return "click"


def _wait_for_semantic_ready(page: Page, timeout: int = 10000) -> Dict[str, Any]:
    wait_state: Dict[str, Any] = {
        "networkidle": False,
        "settle_timeout_ms": 2000,
        "body_visible": False,
        "business_elements": 0,
    }
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
        wait_state["networkidle"] = True
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        wait_state["networkidle_error"] = str(exc)

    try:
        page.wait_for_timeout(2000)
    except PlaywrightError as exc:
        wait_state["settle_error"] = str(exc)

    target, diagnostics = _find_business_frame(page, timeout=min(timeout, 3000))
    wait_state["target_frame"] = _frame_identity(target)
    wait_state["frame_candidates"] = diagnostics[:8]
    try:
        target.locator("body").wait_for(state="visible", timeout=min(timeout, 5000))
        wait_state["body_visible"] = True
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        wait_state["body_error"] = str(exc)

    try:
        target.locator("form, table").first.wait_for(state="attached", timeout=2000)
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        wait_state["business_elements_error"] = str(exc)

    try:
        wait_state["business_elements"] = target.locator("form, table, input, select, textarea, button, a").count()
    except PlaywrightError as exc:
        wait_state["business_elements_error"] = str(exc)

    return wait_state


def _frame_identity(frame: Frame) -> Dict[str, Any]:
    return {
        "name": frame.name,
        "url": frame.url,
    }


def _find_business_frame(page: Page, timeout: int = 3000) -> Tuple[Frame, List[Dict[str, Any]]]:
    diagnostics: List[Dict[str, Any]] = []
    best_frame = page.main_frame
    best_score = -1

    for frame in _walk_frames(page.main_frame):
        score = 0
        details: Dict[str, Any] = {
            "name": frame.name,
            "url": frame.url,
            "score": 0,
            "forms": 0,
            "tables": 0,
            "controls": 0,
            "body_visible": False,
        }
        try:
            if frame.url and frame.url not in ("about:blank", "about:srcdoc"):
                score += 20
            if re.search(r"\.(jsp|do|action)(?:[?#]|$)|/(admin|main|search|list|entry|edit|disp|download|upload)", frame.url, re.I):
                score += 30

            body = frame.locator("body")
            details["body_visible"] = body.is_visible(timeout=timeout)
            if details["body_visible"]:
                score += 10

            forms = frame.locator("form").count()
            tables = frame.locator("table").count()
            controls = frame.locator("input, select, textarea, button, a").count()
            text_len = min(len(body.inner_text(timeout=timeout)), 1000)
            details.update({"forms": forms, "tables": tables, "controls": controls, "text_len": text_len})
            score += forms * 15 + tables * 10 + min(controls, 20) * 2 + min(text_len // 80, 10)
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            details["error"] = str(exc)
        details["score"] = score
        diagnostics.append(details)
        if score > best_score:
            best_score = score
            best_frame = frame

    diagnostics.sort(key=lambda item: item.get("score", 0), reverse=True)
    return best_frame, diagnostics


def _walk_frames(frame: Frame) -> Iterable[Frame]:
    yield frame
    for child in frame.child_frames:
        yield from _walk_frames(child)


def _frame_for_selector(page: Page, selector: str, timeout: int = 3000) -> Tuple[Frame, Dict[str, Any]]:
    target, diagnostics = _find_business_frame(page, timeout=timeout)
    for frame in _walk_frames(page.main_frame):
        try:
            locator = frame.locator(selector)
            if locator.count() > 0:
                try:
                    locator.first.wait_for(state="visible", timeout=timeout)
                except (PlaywrightTimeoutError, PlaywrightError):
                    pass
                return frame, {"target_frame": _frame_identity(frame), "selector_found": True, "frame_candidates": diagnostics[:8]}
        except (PlaywrightTimeoutError, PlaywrightError):
            continue
    return target, {"target_frame": _frame_identity(target), "selector_found": False, "frame_candidates": diagnostics[:8]}


def _default_upload_file(capture_dir: Optional[Union[str, Path]] = None) -> str:
    root = Path(capture_dir or "./output/uploads")
    root.mkdir(parents=True, exist_ok=True)
    sample = root / "moonlight_cyber_sample.txt"
    if not sample.exists():
        sample.write_text(
            "Moonlight cyber sample file\n"
            "purpose=semantic-upload-pressure-test\n"
            "payload=legacy-new-regression\n",
            encoding="utf-8",
        )
    return str(sample)


def execute_action(
    page: Page,
    action_type: str,
    selector: str,
    value: str = None,
    browser_name: str = "chrome",
    *,
    capture_dir: Optional[Union[str, Path]] = None,
    test_id: Optional[str] = None,
    timeout: int = 10000,
    action_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    根据操作类型执行 Playwright UI 操作，包含多端适配容错。

    Returns a structured status and, when capture_dir is provided, an automatic
    post-action screenshot/state payload. Failures are converted to BLOCKED so
    higher-level regression flows can continue collecting evidence.
    """
    result: Dict[str, Any] = {
        "status": "PASS",
        "action_type": action_type,
        "semantic_action": infer_semantic_action(action_type, action_context),
        "selector": selector,
        "browser_name": browser_name,
    }

    try:
        result["wait_state"] = _wait_for_semantic_ready(page, timeout=max(timeout, 15000 if browser_name == "firefox" else timeout))
        semantic_action = result["semantic_action"]
        navigated_directly = False

        if semantic_action in ("goto", "navigate") and re.match(r"^https?://|^/", str(selector or "")):
            page.goto(selector, wait_until="domcontentloaded", timeout=max(timeout, 30000))
            navigated_directly = True
        else:
            frame, frame_state = _frame_for_selector(page, selector, timeout=min(timeout, 5000))
            result.update(frame_state)

        if navigated_directly:
            pass
        elif semantic_action in ("click", "navigate"):
            # 增加元素可见性检查
            frame.locator(selector).first.wait_for(state="visible", timeout=timeout)
            frame.locator(selector).first.click(timeout=timeout)
        elif semantic_action == "fill":
            frame.locator(selector).first.wait_for(state="visible", timeout=timeout)
            frame.locator(selector).first.fill(value or "moonlight-semantic-sample", timeout=timeout)
        elif semantic_action == "select":
            locator = frame.locator(selector).first
            locator.wait_for(state="visible", timeout=timeout)
            selected_value = value or locator.evaluate(
                """element => {
                    const options = Array.from(element.options || []).filter(option => !option.disabled);
                    return (options.find(option => option.value) || options[0] || {}).value || "";
                }"""
            )
            locator.select_option(selected_value, timeout=timeout)
            locator.dispatch_event("change", timeout=timeout)
            result["selected_value"] = selected_value
        elif semantic_action == "upload":
            frame.locator(selector).first.wait_for(state="attached", timeout=timeout)
            upload_file = value or _default_upload_file(capture_dir)
            frame.locator(selector).first.set_input_files(upload_file, timeout=timeout)
            result["upload_file"] = upload_file
        elif semantic_action == "submit":
            locator = frame.locator(selector).first
            locator.wait_for(state="attached", timeout=timeout)
            locator.evaluate(
                """element => {
                    const form = element.tagName && element.tagName.toLowerCase() === "form" ? element : element.closest("form");
                    if (form && form.requestSubmit) form.requestSubmit();
                    else if (form) form.submit();
                    else element.click();
                }"""
            )
        elif semantic_action == "download":
            try:
                with page.expect_download(timeout=45000) as download_info:
                    frame.locator(selector).first.click(timeout=timeout)
                download = download_info.value
                # 隔离三端下载文件
                original_filename = download.suggested_filename
                name, ext = os.path.splitext(original_filename)
                save_path = f"./output/downloads/{name}_{browser_name}{ext}"
                Path(save_path).parent.mkdir(parents=True, exist_ok=True)
                download.save_as(save_path)
                result["download_path"] = save_path
            except PlaywrightError as e:
                # Edge/Windows 安全拦截补丁
                result.update({"status": "BLOCKED", "reason": f"Download blocked or failed: {e}"})
        elif semantic_action == "wait":
            frame.locator(selector).first.wait_for(timeout=20000)
        else:
            result.update({"status": "BLOCKED", "reason": f"Unsupported semantic action: {semantic_action}"})

        if result["status"] == "PASS":
            result["post_wait_state"] = _wait_for_semantic_ready(page, timeout=min(timeout, 8000))
    except (PlaywrightTimeoutError, PlaywrightError, ValueError) as exc:
        result.update({"status": "BLOCKED", "reason": str(exc)})
    finally:
        if capture_dir:
            name = _safe_name(test_id or f"{action_type}_{int(time.time() * 1000)}")
            result["state"] = _capture_state(page, Path(capture_dir), name)

    return result


def _capture_state(page: Page, output_dir: Path, name: str) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot = output_dir / f"{name}.png"
    state: Dict[str, Any] = {"screenshot": str(screenshot)}
    target_frame, diagnostics = _find_business_frame(page, timeout=3000)
    state["target_frame"] = _frame_identity(target_frame)
    state["frame_candidates"] = diagnostics[:8]
    try:
        target_frame.locator("body").screenshot(path=str(screenshot), timeout=15000)
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        state["screenshot_error"] = str(exc)
        try:
            page.screenshot(path=str(screenshot), full_page=True, timeout=15000)
            state["screenshot_fallback"] = "page"
        except (PlaywrightTimeoutError, PlaywrightError) as fallback_exc:
            state["screenshot_fallback_error"] = str(fallback_exc)

    for key, getter in (
        ("url", lambda: target_frame.url or page.url),
        ("title", lambda: target_frame.evaluate("() => document.title") or page.title()),
        ("text", lambda: target_frame.locator("body").inner_text(timeout=10000)),
        ("dom", lambda: target_frame.content()),
    ):
        try:
            state[key] = getter()
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            state[f"{key}_error"] = str(exc)
            state[key] = ""
    return state


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def compare_captured_state(legacy_state: Dict[str, Any], new_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compare stable page data captured after the same action on Legacy and New.

    Pixel-level image diffing is intentionally left to a caller/plugin because this
    repository does not currently carry an image comparison dependency.
    """
    url_match = legacy_state.get("url") == new_state.get("url")
    title_match = legacy_state.get("title") == new_state.get("title")
    legacy_text = _normalize_text(legacy_state.get("text", ""))
    new_text = _normalize_text(new_state.get("text", ""))
    text_match = legacy_text == new_text
    dom_match = _normalize_text(legacy_state.get("dom", "")) == _normalize_text(new_state.get("dom", ""))
    return {
        "status": "PASS" if url_match and title_match and text_match and dom_match else "DIFF",
        "url_match": url_match,
        "title_match": title_match,
        "text_match": text_match,
        "dom_match": dom_match,
        "legacy_screenshot": legacy_state.get("screenshot"),
        "new_screenshot": new_state.get("screenshot"),
    }


def build_steps_from_page_mapping(
    mapping: Dict[str, Any],
    *,
    risk_levels: Optional[Iterable[str]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    risks = list(risk_levels or ["High", "Medium"])
    selected_risks = {risk.lower() for risk in risks}
    risk_rank = {risk.lower(): index for index, risk in enumerate(risks)}
    steps: List[Dict[str, Any]] = []

    pages = [
        page
        for page in mapping.get("page_mappings", [])
        if str(page.get("risk", "")).lower() in selected_risks
    ]
    pages.sort(key=lambda page: (risk_rank[str(page.get("risk", "")).lower()], page.get("page_id", "")))

    for page in pages:
        if str(page.get("risk", "")).lower() not in selected_risks:
            continue

        locator_changes = page.get("locator_changes") or []
        if locator_changes:
            for item in locator_changes:
                steps.append(
                    {
                        "page_id": page.get("page_id"),
                        "risk": page.get("risk"),
                        "action": item.get("label") or item.get("semantic_key"),
                        "action_type": infer_semantic_action(item.get("action_hint") or item.get("kind"), item),
                        "legacy_locator": item.get("legacy_locator"),
                        "new_locator": item.get("new_locator"),
                        "semantic_context": item,
                    }
                )
        else:
            steps.append(
                {
                    "page_id": page.get("page_id"),
                    "risk": page.get("risk"),
                    "action": "page_snapshot",
                    "action_type": "wait",
                    "legacy_locator": "body",
                    "new_locator": "body",
                }
            )

        if limit and len(steps) >= limit:
            return steps[:limit]

    return steps


def execute_consistency_flow(
    legacy_page: Page,
    new_page: Page,
    steps: Optional[List[Dict[str, Any]]] = None,
    *,
    page_mapping: Optional[Union[Dict[str, Any], str, Path]] = None,
    output_dir: str = "./output/consistency",
    browser_name: str = "chrome",
    risk_levels: Optional[Iterable[str]] = None,
    visual_threshold_percent: float = 0.1,
) -> List[Dict[str, Any]]:
    """
    Execute a Legacy-first/New-replay migration consistency flow.

    Expected step shape:
    {
        "page_id": "AbstListEdit.jsp",
        "action": "AbstListViewEntry",
        "action_type": "click",
        "legacy_locator": "[name='btAdd']",
        "new_locator": "[name='btAdd']",
        "value": None
    }
    """
    if steps is None:
        if page_mapping is None:
            raise ValueError("steps or page_mapping is required")
        if isinstance(page_mapping, (str, Path)):
            page_mapping = json.loads(Path(page_mapping).read_text(encoding="utf-8"))
        steps = build_steps_from_page_mapping(page_mapping, risk_levels=risk_levels)

    results: List[Dict[str, Any]] = []
    root = Path(output_dir)

    for index, step in enumerate(steps, start=1):
        page_id = step.get("page_id", "unknown")
        action = step.get("action") or step.get("action_type")
        action_type = infer_semantic_action(step.get("action_type", "click"), step)
        legacy_locator: Optional[str] = step.get("legacy_locator")
        new_locator: Optional[str] = step.get("new_locator")

        if not legacy_locator or not new_locator:
            results.append(
                {
                    "page_id": page_id,
                    "action": action,
                    "action_type": action_type,
                    "status": "BLOCKED",
                    "reason": "legacy_locator or new_locator is missing",
                }
            )
            continue

        legacy_action = execute_action(
            legacy_page,
            action_type,
            legacy_locator,
            step.get("value"),
            browser_name=browser_name,
            capture_dir=root,
            test_id=f"{index:04d}_{page_id}_legacy",
            action_context={**step, "locator": legacy_locator},
        )
        legacy_state = legacy_action.get("state") or _capture_state(legacy_page, root, f"{index:04d}_legacy")

        new_action = execute_action(
            new_page,
            action_type,
            new_locator,
            step.get("value"),
            browser_name=browser_name,
            capture_dir=root,
            test_id=f"{index:04d}_{page_id}_new",
            action_context={**step, "locator": new_locator},
        )

        new_state = new_action.get("state") or _capture_state(new_page, root, f"{index:04d}_new")

        if legacy_action.get("status") == "BLOCKED" or new_action.get("status") == "BLOCKED":
            results.append(
                {
                    "page_id": page_id,
                    "risk": step.get("risk"),
                    "action": action,
                    "action_type": action_type,
                    "status": "BLOCKED",
                    "legacy_action": legacy_action,
                    "new_action": new_action,
                    "legacy_screenshot": legacy_state.get("screenshot"),
                    "new_screenshot": new_state.get("screenshot"),
                    "legacy_frame": legacy_state.get("target_frame"),
                    "new_frame": new_state.get("target_frame"),
                }
            )
            continue

        result = compare_captured_state(legacy_state, new_state)
        visual = compare_visual_screenshot(
            legacy_state.get("screenshot", ""),
            new_state.get("screenshot", ""),
            str(root / f"{index:04d}_{_safe_name(page_id)}_diff.png"),
            threshold_percent=visual_threshold_percent,
        )
        result["visual"] = visual
        if result["status"] == "PASS" and visual.get("status") != "PASS":
            result["status"] = visual.get("status", "DIFF")
        result.update(
            {
                "page_id": page_id,
                "risk": step.get("risk"),
                "action": action,
                "action_type": action_type,
                "legacy_locator": legacy_locator,
                "new_locator": new_locator,
                "legacy_frame": legacy_state.get("target_frame"),
                "new_frame": new_state.get("target_frame"),
            }
        )
        results.append(result)

    return results
