from playwright.sync_api import Page, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
import os
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from src.assert_engine import compare_visual_screenshot


def _safe_name(value: Any) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown")).strip("_")
    return name[:120] or "unknown"


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
        "selector": selector,
        "browser_name": browser_name,
    }

    try:
        # Firefox 渲染延迟容错
        if browser_name == "firefox":
            page.wait_for_load_state("networkidle", timeout=15000)
        else:
            page.wait_for_load_state("load", timeout=timeout)

        if action_type == "click":
            # 增加元素可见性检查
            page.wait_for_selector(selector, state="visible", timeout=timeout)
            page.click(selector, timeout=timeout)
        elif action_type == "fill":
            page.wait_for_selector(selector, state="visible", timeout=timeout)
            page.fill(selector, value or "", timeout=timeout)
        elif action_type == "upload":
            page.wait_for_selector(selector, timeout=timeout)
            page.set_input_files(selector, value, timeout=timeout)
        elif action_type == "download":
            try:
                with page.expect_download(timeout=45000) as download_info:
                    page.click(selector, timeout=timeout)
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
        elif action_type == "wait":
            page.wait_for_selector(selector, timeout=20000)
        elif action_type in ("goto", "navigate"):
            page.goto(selector, wait_until="load", timeout=max(timeout, 30000))
        else:
            result.update({"status": "BLOCKED", "reason": f"Unsupported action type: {action_type}"})
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
    try:
        page.screenshot(path=str(screenshot), full_page=True, timeout=15000)
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        state["screenshot_error"] = str(exc)

    for key, getter in (
        ("url", lambda: page.url),
        ("title", lambda: page.title()),
        ("text", lambda: page.locator("body").inner_text(timeout=10000)),
        ("dom", lambda: page.content()),
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
                        "action_type": "click",
                        "legacy_locator": item.get("legacy_locator"),
                        "new_locator": item.get("new_locator"),
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
        legacy_locator: Optional[str] = step.get("legacy_locator")
        new_locator: Optional[str] = step.get("new_locator")

        if not legacy_locator or not new_locator:
            results.append(
                {
                    "page_id": page_id,
                    "action": action,
                    "status": "BLOCKED",
                    "reason": "legacy_locator or new_locator is missing",
                }
            )
            continue

        legacy_action = execute_action(
            legacy_page,
            step.get("action_type", "click"),
            legacy_locator,
            step.get("value"),
            browser_name=browser_name,
            capture_dir=root,
            test_id=f"{index:04d}_{page_id}_legacy",
        )
        legacy_state = legacy_action.get("state") or _capture_state(legacy_page, root, f"{index:04d}_legacy")

        new_action = execute_action(
            new_page,
            step.get("action_type", "click"),
            new_locator,
            step.get("value"),
            browser_name=browser_name,
            capture_dir=root,
            test_id=f"{index:04d}_{page_id}_new",
        )

        new_state = new_action.get("state") or _capture_state(new_page, root, f"{index:04d}_new")

        if legacy_action.get("status") == "BLOCKED" or new_action.get("status") == "BLOCKED":
            results.append(
                {
                    "page_id": page_id,
                    "risk": step.get("risk"),
                    "action": action,
                    "status": "BLOCKED",
                    "legacy_action": legacy_action,
                    "new_action": new_action,
                    "legacy_screenshot": legacy_state.get("screenshot"),
                    "new_screenshot": new_state.get("screenshot"),
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
                "legacy_locator": legacy_locator,
                "new_locator": new_locator,
            }
        )
        results.append(result)

    return results
