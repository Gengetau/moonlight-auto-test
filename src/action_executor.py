from playwright.sync_api import Frame, Page, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
import os
import json
import re
import time
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from PIL import Image, ImageDraw, ImageGrab

from src.assert_engine import compare_visual_screenshot


def _safe_name(value: Any) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown")).strip("_")
    return name[:120] or "unknown"


def _safe_download_filename(filename: str, browser_name: str) -> str:
    original = Path(str(filename or "download")).name
    name, ext = os.path.splitext(original)
    safe_base = _safe_name(name)
    safe_ext = re.sub(r"[^A-Za-z0-9.]+", "", ext)[:20]
    return f"{safe_base}_{_safe_name(browser_name)}{safe_ext}"


def _page_is_closed(page: Page) -> bool:
    try:
        return page.is_closed()
    except Exception:
        return True


def _is_target_closed_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "target page, context or browser has been closed" in message
        or "target closed" in message
        or "page has been closed" in message
        or "page closed" in message
    )


def _closed_page_state(output_dir: Path, name: str) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot = output_dir / f"{name}.png"
    image = Image.new("RGB", (640, 360), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 639, 359), outline=(210, 216, 224), width=2)
    draw.text((32, 32), "Page closed", fill=(20, 30, 42))
    draw.text((32, 64), "The action closed this browser page.", fill=(70, 84, 102))
    image.save(screenshot)
    return {
        "screenshot": str(screenshot),
        "page_closed": True,
        "url": "about:closed",
        "title": "",
        "text": "Page closed",
        "dom": "<page-closed />",
        "target_frame": {"name": "closed", "url": "about:closed"},
        "frame_candidates": [],
    }


def _capture_browser_screen(page: Page, screenshot: Path, timeout: int = 15000) -> Dict[str, Any]:
    """
    Capture the visible browser window including browser chrome.

    Playwright page screenshots cannot include the address bar. In headed
    Windows runs we bring the page to the front and capture the primary display
    at the migration-test resolution requirement: 1920x1080, scaling 100%.
    """
    try:
        page.bring_to_front()
        try:
            page.keyboard.press("Control+0", timeout=2000)
        except (PlaywrightTimeoutError, PlaywrightError):
            pass
        page.wait_for_timeout(250)
        image = ImageGrab.grab(bbox=(0, 0, 1920, 1080))
        image.save(screenshot)
        return {
            "ok": True,
            "screenshot_scope": "browser_screen_1920x1080",
            "screenshot_resolution": "1920x1080",
            "includes_browser_chrome": True,
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


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
    "clear": "clear",
    "set_value": "set_value",
    "setvalue": "set_value",
    "hidden": "set_value",
    "press": "press",
    "key": "press",
    "special_key": "press",
    "close_window": "click",
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

    if "set_value" in raw or "setvalue" in raw or "hidden" in raw:
        return "set_value"
    if "clear" in raw:
        return "clear"
    if "press" in raw or "special_key" in raw:
        return "press"
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
        "domcontentloaded": False,
        "settle_timeout_ms": 2000,
        "body_visible": False,
        "business_elements": 0,
    }
    if _page_is_closed(page):
        wait_state["page_closed"] = True
        return wait_state
    # 针对旧系统，优先使用 domcontentloaded
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
        wait_state["domcontentloaded"] = True
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        wait_state["domcontentloaded_error"] = str(exc)

    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout, 5000))
        wait_state["networkidle"] = True
    except (PlaywrightTimeoutError, PlaywrightError):
        # networkidle 对于旧系统经常超时，不作为硬性阻塞
        pass

    # 强制 settle 时间，给旧系统 JS 渲染留白
    try:
        page.wait_for_timeout(2000)
    except PlaywrightError as exc:
        if _is_target_closed_error(exc) or _page_is_closed(page):
            wait_state["page_closed"] = True
            wait_state["settle_error"] = str(exc)
            return wait_state
        raise

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
    if _page_is_closed(page):
        raise ValueError("Page is closed before action")
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


def _upload_filename(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.replace("\\", "/")
    return Path(normalized).name or PureWindowsPath(text).name


def _resolve_upload_file_value(value: Optional[str], capture_dir: Optional[Union[str, Path]] = None) -> str:
    raw = str(value or "").strip()
    env_value = str(os.environ.get("MOONLIGHT_UPLOAD_FILE") or "").strip()
    if env_value:
        env_path = Path(env_value)
        if env_path.exists():
            return str(env_path)

    if raw:
        raw_path = Path(raw)
        if raw_path.exists():
            return str(raw_path)

        filename = _upload_filename(raw)
        if "fakepath" in raw.lower() and filename:
            search_roots = [
                Path("test_data/upload"),
                Path("test_data"),
                Path("data/upload"),
                Path("data"),
            ]
            for root in search_roots:
                if not root.exists():
                    continue
                direct = root / filename
                if direct.exists():
                    return str(direct)
                for candidate in root.rglob(filename):
                    if candidate.is_file():
                        return str(candidate)

            return _default_upload_file(capture_dir)

        return raw

    return _default_upload_file(capture_dir)




def _resolve_upload_locator(frame: Frame, selector: str) -> Tuple[str, Dict[str, Any]]:
    """
    upload action 专用 selector 修正。

    历史 Struts JSP 中 <html:file name="FormBean" property="uploadFile" />
    容易被扫描成 [name='FormBean']，实际运行时该 locator 指向 <form>，
    不能执行 set_input_files()。这里在执行 upload 前确认目标是否为
    <input type="file">；如果不是，则自动 fallback 到页面内的 file input。
    """
    diagnostics: Dict[str, Any] = {
        "original_selector": selector,
        "resolved_selector": selector,
        "fallback_used": False,
    }

    def _is_file_input(candidate_selector: str) -> bool:
        try:
            locator = frame.locator(candidate_selector).first
            if locator.count() == 0:
                return False
            tag_name = locator.evaluate("el => (el.tagName || '').toLowerCase()")
            input_type = locator.evaluate("el => (el.getAttribute('type') || '').toLowerCase()")
            diagnostics["resolved_tag"] = tag_name
            diagnostics["resolved_type"] = input_type
            return tag_name == "input" and input_type == "file"
        except Exception as exc:
            diagnostics["validation_error"] = str(exc)
            return False

    if selector and _is_file_input(selector):
        return selector, diagnostics

    fallback_selectors = [
        "input[type='file']",
        "input[name='uploadFile']",
        "input[type='file'][name='uploadFile']",
    ]

    for fallback in fallback_selectors:
        try:
            count = frame.locator(fallback).count()
            if count > 0 and _is_file_input(fallback):
                diagnostics.update(
                    {
                        "resolved_selector": fallback,
                        "fallback_used": True,
                        "fallback_count": count,
                    }
                )
                return fallback, diagnostics
        except Exception as exc:
            diagnostics[f"fallback_error:{fallback}"] = str(exc)

    return selector, diagnostics


def _opens_popup_hint(context: Optional[Dict[str, Any]]) -> bool:
    context = context or {}
    attributes = {str(key).lower(): value for key, value in (context.get("attributes") or {}).items()}
    target = str(attributes.get("target") or "").strip().lower()
    evidence = " ".join(str(context.get(key) or "").lower() for key in ("raw", "label", "semantic_key"))
    return bool(target and target not in {"_self", "self"}) or "window.open" in evidence or "target=" in evidence


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
    action_dispatched = False
    capture_page = page
    keep_popup = bool((action_context or {}).get("keep_popup"))

    def _log_event(event_type: str, details: Any):
        # 内部闭包用于记录 Playwright 事件
        test_id_str = _safe_name(test_id or "global")
        log_dir = Path(capture_dir) if capture_dir else Path("./output/logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / f"{test_id_str}_events.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {event_type}: {details}\n")

    try:
        # 注入 Runtime Debugger
        page.on("console", lambda msg: _log_event("CONSOLE", f"{msg.type}: {msg.text}"))
        page.on("pageerror", lambda err: _log_event("JS_ERROR", str(err)))
        page.on("requestfailed", lambda req: _log_event("REQ_FAILED", f"{req.url} ({req.failure})"))

        if _page_is_closed(page):
            raise ValueError("Page is closed before action")

        result["wait_state"] = _wait_for_semantic_ready(page, timeout=max(timeout, 15000 if browser_name == "firefox" else timeout))
        semantic_action = result["semantic_action"]
        navigated_directly = False

        if semantic_action in ("goto", "navigate") and re.match(r"^https?://|^/", str(selector or "")):
            action_dispatched = True
            page.goto(selector, wait_until="domcontentloaded", timeout=max(timeout, 30000))
            navigated_directly = True
        else:
            frame, frame_state = _frame_for_selector(page, selector, timeout=min(timeout, 5000))
            result.update(frame_state)

        if navigated_directly:
            pass
        elif semantic_action in ("click", "navigate"):
            # 增加元素可见性检查
            locator = frame.locator(selector).first
            locator.wait_for(state="visible", timeout=timeout)
            action_dispatched = True
            if _opens_popup_hint(action_context):
                try:
                    with page.expect_popup(timeout=3000) as popup_info:
                        locator.click(timeout=timeout)
                    popup = popup_info.value
                    popup.wait_for_load_state("domcontentloaded", timeout=min(timeout, 10000))
                    capture_page = popup
                    result["popup_opened"] = True
                    result["popup_url"] = popup.url
                    if keep_popup:
                        result["popup_page"] = popup
                except PlaywrightTimeoutError as exc:
                    if "popup" not in str(exc).lower():
                        raise
                    result["popup_opened"] = False
            else:
                locator.click(timeout=timeout)
        elif semantic_action == "fill":
            frame.locator(selector).first.wait_for(state="visible", timeout=timeout)
            frame.locator(selector).first.fill(value or "moonlight-semantic-sample", timeout=timeout)
        elif semantic_action == "clear":
            frame.locator(selector).first.wait_for(state="visible", timeout=timeout)
            frame.locator(selector).first.fill("", timeout=timeout)
        elif semantic_action == "set_value":
            locator = frame.locator(selector).first
            locator.wait_for(state="attached", timeout=timeout)
            locator.evaluate(
                """(element, nextValue) => {
                    element.value = nextValue || "";
                    element.dispatchEvent(new Event("input", { bubbles: true }));
                    element.dispatchEvent(new Event("change", { bubbles: true }));
                }""",
                value or "moonlight-hidden-auto",
            )
        elif semantic_action == "press":
            key = value or "Enter"
            if selector and selector not in {"-", "__page__"}:
                frame.locator(selector).first.wait_for(state="attached", timeout=timeout)
                frame.locator(selector).first.focus(timeout=timeout)
            page.keyboard.press(key, timeout=timeout)
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
            resolved_selector, upload_locator_state = _resolve_upload_locator(frame, selector)
            result["upload_locator_state"] = upload_locator_state
            frame.locator(resolved_selector).first.wait_for(state="attached", timeout=timeout)
            upload_file = _resolve_upload_file_value(value, capture_dir)
            frame.locator(resolved_selector).first.set_input_files(upload_file, timeout=timeout)
            result["upload_file"] = upload_file
            result["resolved_selector"] = resolved_selector
        elif semantic_action == "submit":
            locator = frame.locator(selector).first
            locator.wait_for(state="attached", timeout=timeout)
            action_dispatched = True
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
                save_path = f"./output/downloads/{_safe_download_filename(download.suggested_filename, browser_name)}"
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
            if result["post_wait_state"].get("page_closed"):
                result["page_closed_after_action"] = True
    except (PlaywrightTimeoutError, PlaywrightError, ValueError) as exc:
        closes_page = result.get("semantic_action") in ("click", "navigate", "submit", "goto")
        if action_dispatched and closes_page and (_is_target_closed_error(exc) or _page_is_closed(page)):
            result.update(
                {
                    "status": "PASS",
                    "page_closed_after_action": True,
                    "post_wait_state": {"page_closed": True, "settle_error": str(exc)},
                }
            )
        else:
            result.update({"status": "BLOCKED", "reason": str(exc)})
    finally:
        if capture_dir:
            name = _safe_name(test_id or f"{action_type}_{int(time.time() * 1000)}")
            result["state"] = _capture_state(capture_page, Path(capture_dir), name)
            if capture_page is not page and not keep_popup:
                try:
                    capture_page.close()
                except PlaywrightError:
                    pass

    return result


def _capture_state(page: Page, output_dir: Path, name: str) -> Dict[str, Any]:
    if _page_is_closed(page):
        return _closed_page_state(output_dir, name)

    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot = output_dir / f"{name}.png"
    state: Dict[str, Any] = {"screenshot": str(screenshot)}
    try:
        target_frame, diagnostics = _find_business_frame(page, timeout=3000)
    except PlaywrightError as exc:
        if _is_target_closed_error(exc) or _page_is_closed(page):
            state = _closed_page_state(output_dir, name)
            state["capture_error"] = str(exc)
            return state
        raise
    state["target_frame"] = _frame_identity(target_frame)
    state["frame_candidates"] = diagnostics[:8]
    browser_screen = _capture_browser_screen(page, screenshot)
    if browser_screen.get("ok"):
        state.update({key: value for key, value in browser_screen.items() if key != "ok"})
    else:
        state["browser_screen_error"] = browser_screen.get("reason")
        try:
            page.screenshot(path=str(screenshot), full_page=True, timeout=15000)
            state["screenshot_scope"] = "page_full"
            state["includes_browser_chrome"] = False
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            state["screenshot_error"] = str(exc)
            try:
                target_frame.locator("body").screenshot(path=str(screenshot), timeout=15000)
                state["screenshot_fallback"] = "target_frame_body"
                state["includes_browser_chrome"] = False
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


DYNAMIC_URL_QUERY_KEYS = {
    "userId",
    "userid",
    "sessionId",
    "sessionid",
    "JSESSIONID",
    "jsessionid",
    "token",
    "csrf",
    "_csrf",
    "_",
    "timestamp",
    "ts",
    "r",
}


def normalize_url_for_compare(url: str) -> str:
    """
    Normalize Legacy/New URLs for migration comparison.

    We intentionally ignore scheme/host because Legacy and New run on different
    servers. We also ignore dynamic query parameters such as encrypted userId.
    """
    if not url:
        return ""

    parsed = urlparse(str(url))
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in DYNAMIC_URL_QUERY_KEYS
    ]

    return urlunparse(
        (
            "",
            "",
            parsed.path,
            "",
            urlencode(query, doseq=True),
            "",
        )
    )


COMPARE_POLICY = {
    "page_snapshot": {
        "url_required": True,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": True,
    },
    "wait": {
        "url_required": True,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": True,
    },
    "click": {
        "url_required": False,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": True,
    },
    "submit": {
        "url_required": False,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": True,
    },
    "upload": {
        "url_required": False,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": True,
    },
    "download": {
        "url_required": False,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": False,
    },
    "navigate": {
        "url_required": True,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": True,
    },
    "close_window": {
        "url_required": False,
        "title_required": False,
        "text_required": False,
        "dom_required": False,
        "visual_required": False,
    },
}


def compare_captured_state(
    legacy_state: Dict[str, Any],
    new_state: Dict[str, Any],
    *,
    action_type: str = "wait",
    visual_status: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compare stable page data captured after the same action on Legacy and New.

    Important migration-testing rule:
    - Full DOM equality is usually too strict for Struts -> Spring migration.
    - Dynamic URL query values such as encrypted userId must not fail the case.
    - If visual comparison passes and DOM is not required, return WARN instead
      of DIFF so pytest does not fail on harmless implementation differences.
    """
    policy = COMPARE_POLICY.get(action_type, COMPARE_POLICY["click"])

    legacy_url_normalized = normalize_url_for_compare(legacy_state.get("url", ""))
    new_url_normalized = normalize_url_for_compare(new_state.get("url", ""))
    url_match = legacy_url_normalized == new_url_normalized

    title_match = legacy_state.get("title") == new_state.get("title")

    legacy_text = _normalize_text(legacy_state.get("text", ""))
    new_text = _normalize_text(new_state.get("text", ""))
    text_match = legacy_text == new_text

    legacy_dom = _normalize_text(legacy_state.get("dom", ""))
    new_dom = _normalize_text(new_state.get("dom", ""))
    dom_match = legacy_dom == new_dom

    hard_failures: List[str] = []
    warnings: List[str] = []

    if policy.get("url_required") and not url_match:
        hard_failures.append("URL differs")
    elif not url_match:
        warnings.append("URL differs")

    if policy.get("title_required") and not title_match:
        hard_failures.append("Title differs")
    elif not title_match:
        warnings.append("Title differs")

    if policy.get("text_required") and not text_match:
        hard_failures.append("Text differs")
    elif not text_match:
        warnings.append("Text differs")

    if policy.get("dom_required") and not dom_match:
        hard_failures.append("DOM differs")
    elif not dom_match:
        warnings.append("DOM differs")

    if visual_status and policy.get("visual_required") and visual_status != "PASS":
        hard_failures.append(f"Visual comparison {visual_status}")

    if hard_failures:
        status = "DIFF"
    elif warnings:
        status = "WARN"
    else:
        status = "PASS"

    return {
        "status": status,
        "url_match": url_match,
        "url_required": policy.get("url_required", False),
        "legacy_url_normalized": legacy_url_normalized,
        "new_url_normalized": new_url_normalized,
        "title_match": title_match,
        "title_required": policy.get("title_required", False),
        "text_match": text_match,
        "text_required": policy.get("text_required", False),
        "dom_match": dom_match,
        "dom_required": policy.get("dom_required", False),
        "dom_warning": "DOM differs; visual comparison may still pass" if not dom_match and not policy.get("dom_required") else None,
        "warnings": warnings,
        "hard_failures": hard_failures,
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

        visual = {"status": "SKIPPED", "reason": "Visual comparison is disabled for this action type"}
        if COMPARE_POLICY.get(action_type, COMPARE_POLICY["click"]).get("visual_required", True):
            visual = compare_visual_screenshot(
                legacy_state.get("screenshot", ""),
                new_state.get("screenshot", ""),
                str(root / f"{index:04d}_{_safe_name(page_id)}_diff.png"),
                threshold_percent=visual_threshold_percent,
            )

        result = compare_captured_state(
            legacy_state,
            new_state,
            action_type=action_type,
            visual_status=visual.get("status"),
        )
        result["visual"] = visual
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
