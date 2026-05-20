from playwright.sync_api import Page, Error as PlaywrightError
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

def execute_action(page: Page, action_type: str, selector: str, value: str = None, browser_name: str = "chrome"):
    """
    根据操作类型执行 Playwright UI 操作，包含多端适配容错。
    """
    # Firefox 渲染延迟容错
    if browser_name == "firefox":
        page.wait_for_load_state("networkidle", timeout=15000)
    else:
        page.wait_for_load_state("load", timeout=10000)

    if action_type == "click":
        # 增加元素可见性检查
        page.wait_for_selector(selector, state="visible", timeout=10000)
        page.click(selector)
    elif action_type == "fill":
        page.fill(selector, value)
    elif action_type == "upload":
        page.set_input_files(selector, value)
    elif action_type == "download":
        try:
            with page.expect_download(timeout=45000) as download_info:
                page.click(selector)
            download = download_info.value
            # 隔离三端下载文件
            original_filename = download.suggested_filename
            name, ext = os.path.splitext(original_filename)
            save_path = f"./output/downloads/{name}_{browser_name}{ext}"
            download.save_as(save_path)
            return save_path
        except PlaywrightError as e:
            # Edge/Windows 安全拦截补丁
            print(f"Download blocked or failed on {browser_name}: {str(e)}")
            raise e
    elif action_type == "wait":
        page.wait_for_selector(selector, timeout=20000)
    else:
        raise ValueError(f"Unsupported action type: {action_type}")


def _capture_state(page: Page, output_dir: Path, name: str) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot = output_dir / f"{name}.png"
    page.screenshot(path=str(screenshot), full_page=True)
    return {
        "url": page.url,
        "title": page.title(),
        "text": page.locator("body").inner_text(timeout=10000),
        "screenshot": str(screenshot),
    }


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
    return {
        "status": "PASS" if url_match and title_match and text_match else "DIFF",
        "url_match": url_match,
        "title_match": title_match,
        "text_match": text_match,
        "legacy_screenshot": legacy_state.get("screenshot"),
        "new_screenshot": new_state.get("screenshot"),
    }


def execute_consistency_flow(
    legacy_page: Page,
    new_page: Page,
    steps: List[Dict[str, Any]],
    *,
    output_dir: str = "./output/consistency",
    browser_name: str = "chrome",
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

        execute_action(
            legacy_page,
            step.get("action_type", "click"),
            legacy_locator,
            step.get("value"),
            browser_name=browser_name,
        )
        legacy_state = _capture_state(legacy_page, root, f"{index:04d}_legacy")

        execute_action(
            new_page,
            step.get("action_type", "click"),
            new_locator,
            step.get("value"),
            browser_name=browser_name,
        )
        new_state = _capture_state(new_page, root, f"{index:04d}_new")

        result = compare_captured_state(legacy_state, new_state)
        result.update(
            {
                "page_id": page_id,
                "action": action,
                "legacy_locator": legacy_locator,
                "new_locator": new_locator,
            }
        )
        results.append(result)

    return results
