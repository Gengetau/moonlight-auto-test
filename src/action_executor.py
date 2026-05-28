from playwright.sync_api import Frame, Page, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
import os
import json
import re
import time
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from PIL import Image, ImageDraw, ImageFont, ImageGrab

from src.assert_engine import compare_visual_screenshot
from src.config_parser import Config


def _safe_name(value: Any) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown")).strip("_")
    return name[:120] or "unknown"


def _safe_download_filename(filename: str, browser_name: str) -> str:
    original = Path(str(filename or "download")).name
    name, ext = os.path.splitext(original)
    safe_base = _safe_name(name)
    safe_ext = re.sub(r"[^A-Za-z0-9.]+", "", ext)[:20]
    return f"{safe_base}_{_safe_name(browser_name)}{safe_ext}"


def _original_download_filename(filename: str) -> str:
    text = str(filename or "download").strip() or "download"
    # Browsers should provide a filename, but strip path components defensively
    # without changing the actual basename.
    windows_name = PureWindowsPath(text).name
    return Path(windows_name).name or "download"


def _configured_download_dir() -> Path:
    raw_dir = os.getenv("DOWNLOAD_DIR") or getattr(Config, "DOWNLOAD_DIR", "") or "~/Downloads"
    expanded = os.path.expandvars(os.path.expanduser(str(raw_dir)))
    return Path(expanded)


def _download_save_path(suggested_filename: str) -> Path:
    base_path = _configured_download_dir() / _original_download_filename(suggested_filename)
    if not base_path.exists():
        return base_path

    stem = base_path.stem or "download"
    suffix = base_path.suffix
    for index in range(1, 10000):
        candidate = base_path.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
    return base_path.with_name(f"{stem} ({int(time.time() * 1000)}){suffix}")


def _record_download_result(result: Dict[str, Any], download: Any) -> None:
    suggested_filename = download.suggested_filename or "download"
    save_path = _download_save_path(suggested_filename)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    download.save_as(str(save_path))
    result["download_suggested_filename"] = suggested_filename
    result["download_filename"] = _original_download_filename(suggested_filename)
    result["saved_filename"] = save_path.name
    result["download_renamed"] = save_path.name != result["download_filename"]
    result["download_dir"] = str(save_path.parent)
    result["download_path"] = str(save_path)


def _console_font(size: int = 15):
    candidates = [
        r"C:\Windows\Fonts\consola.ttf",
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap_console_line(text: str, limit: int = 150) -> List[str]:
    raw = str(text or "")
    if len(raw) <= limit:
        return [raw]
    lines = []
    current = raw
    while len(current) > limit:
        split_at = current.rfind(" ", 0, limit)
        if split_at < 40:
            split_at = limit
        lines.append(current[:split_at].rstrip())
        current = current[split_at:].lstrip()
    if current:
        lines.append(current)
    return lines


def _render_console_evidence_image(events: List[Dict[str, Any]], output_dir: Path, name: str) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / f"{name}_console.png"
    font = _console_font(15)
    small_font = _console_font(13)
    width = 1280
    line_height = 24
    header_height = 54
    lines: List[Tuple[str, str]] = []

    if not events:
        lines.append(("info", "No console/pageerror/requestfailed/http error events captured."))
    for event in events:
        timestamp = event.get("time") or ""
        level = str(event.get("level") or event.get("type") or "info").lower()
        event_type = event.get("type") or "EVENT"
        detail = event.get("detail") or ""
        status = f" status={event.get('status')}" if event.get("status") else ""
        url = f" {event.get('url')}" if event.get("url") else ""
        text = f"{timestamp} [{event_type}]{status} {detail}{url}".strip()
        for wrapped in _wrap_console_line(text):
            lines.append((level, wrapped))

    height = max(240, header_height + (len(lines) + 1) * line_height + 24)
    image = Image.new("RGB", (width, height), (31, 31, 31))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, header_height), fill=(38, 38, 38))
    draw.text((18, 16), "Console evidence", fill=(232, 234, 237), font=font)
    draw.text(
        (220, 18),
        "Captured from Playwright console/pageerror/requestfailed/HTTP response events",
        fill=(154, 160, 166),
        font=small_font,
    )
    y = header_height + 14
    colors = {
        "error": (255, 128, 128),
        "pageerror": (255, 128, 128),
        "requestfailed": (255, 185, 117),
        "http_error": (255, 185, 117),
        "warning": (255, 214, 102),
        "warn": (255, 214, 102),
        "info": (207, 216, 220),
        "log": (207, 216, 220),
        "debug": (180, 190, 200),
    }
    for level, text in lines:
        draw.text((18, y), text, fill=colors.get(level, (207, 216, 220)), font=font)
        y += line_height
    image.save(image_path)
    return str(image_path)


def _needs_console_evidence(action_context: Optional[Dict[str, Any]], action_type: str) -> bool:
    context = action_context or {}
    blob = " ".join(
        str(value or "")
        for value in (
            action_type,
            context.get("case_type"),
            context.get("action_type"),
            context.get("expected_type"),
            context.get("test_title"),
            context.get("label"),
            context.get("objective"),
        )
    ).lower()
    return any(token in blob for token in ("js_error", "javascript", "console", "http_500", "http error", "network_abort"))


NEGATIVE_ACTIONS = {"negative_js_error", "negative_http_500", "negative_network_abort"}


def _negative_url_pattern(action_context: Optional[Dict[str, Any]], value: Any = None) -> str:
    context = action_context or {}
    for key in ("url_pattern", "expected_value", "test_data", "value"):
        candidate = context.get(key)
        if candidate:
            return str(candidate)
    if value:
        return str(value)
    return "**/*"


def _negative_visual_evidence_payload(
    action: str,
    *,
    detail: Any = "",
    url: Any = "",
    phase: str = "triggered",
) -> Dict[str, str]:
    labels = {
        "negative_js_error": "Simulated JavaScript Error",
        "negative_http_500": "Simulated HTTP 500",
        "negative_network_abort": "Simulated Network Abort",
    }
    title = labels.get(str(action or ""), "Simulated Negative Case")
    detail_text = str(detail or "").strip()
    url_text = str(url or "").strip()
    return {
        "title": title,
        "phase": phase,
        "detail": detail_text,
        "url": url_text,
    }


def _inject_negative_visual_evidence(
    page: Page,
    action: str,
    *,
    detail: Any = "",
    url: Any = "",
    phase: str = "triggered",
) -> List[Dict[str, Any]]:
    if _page_is_closed(page):
        return [{"injected": False, "reason": "page_closed"}]

    payload = _negative_visual_evidence_payload(action, detail=detail, url=url, phase=phase)
    script = """
    payload => {
      const doc = document;
      const root = doc.body || doc.documentElement;
      if (!root) return { injected: false, reason: 'no document root' };

      const id = 'moonlight-negative-visual-evidence';
      let panel = doc.getElementById(id);
      if (!panel) {
        panel = doc.createElement('div');
        panel.id = id;
        root.appendChild(panel);
      }

      panel.replaceChildren();
      const title = doc.createElement('div');
      title.textContent = payload.title || 'Simulated Negative Case';
      title.style.cssText = 'font-weight:700;font-size:16px;line-height:1.35;margin-bottom:8px;';

      const phase = doc.createElement('div');
      phase.textContent = 'Phase: ' + (payload.phase || 'triggered');
      phase.style.cssText = 'font-size:12px;line-height:1.35;margin-bottom:6px;opacity:.95;';

      const detail = doc.createElement('div');
      detail.textContent = payload.detail || 'A visible error evidence marker was injected for screenshot verification.';
      detail.style.cssText = 'font-size:13px;line-height:1.45;margin-bottom:6px;';

      const url = doc.createElement('div');
      url.textContent = payload.url ? ('Target: ' + payload.url) : 'Target: current page';
      url.style.cssText = 'font-size:11px;line-height:1.35;word-break:break-all;opacity:.9;';

      panel.appendChild(title);
      panel.appendChild(phase);
      panel.appendChild(detail);
      panel.appendChild(url);
      panel.setAttribute('data-moonlight-negative-evidence', 'true');
      panel.style.cssText = [
        'position:fixed',
        'top:14px',
        'right:14px',
        'width:min(440px, calc(100vw - 32px))',
        'box-sizing:border-box',
        'padding:14px 16px',
        'z-index:2147483647',
        'background:#7f1d1d',
        'color:#fff',
        'border:3px solid #fecaca',
        'box-shadow:0 12px 32px rgba(0,0,0,.38)',
        'font-family:Arial, Meiryo, sans-serif',
        'text-align:left',
        'letter-spacing:0',
        'pointer-events:none'
      ].join(';');

      const body = doc.body;
      if (body) {
        body.setAttribute('data-moonlight-negative-state', payload.title || 'negative');
        body.style.outline = '4px solid #ef4444';
        body.style.outlineOffset = '-4px';
      }

      return {
        injected: true,
        title: payload.title,
        phase: payload.phase,
        url: location.href,
        text: panel.innerText
      };
    }
    """

    evidence: List[Dict[str, Any]] = []
    for frame in page.frames:
        try:
            state = frame.evaluate(script, payload)
            if state:
                state["frame_url"] = str(frame.url or "")
                evidence.append(state)
        except PlaywrightError as exc:
            evidence.append({"injected": False, "frame_url": str(frame.url or ""), "reason": str(exc)})
    return evidence or [{"injected": False, "reason": "no frames"}]


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


def _should_accept_database_dialog(context: Optional[Dict[str, Any]]) -> bool:
    if not context:
        return False
    evidence = " ".join(
        str(context.get(key) or "")
        for key in (
            "case_type",
            "action_type",
            "label",
            "semantic_key",
            "locator",
            "onclick",
            "expected_type",
        )
    ).lower()
    return any(
        token in evidence
        for token in (
            "delete_action",
            "delete",
            "削除",
            "update",
            "更新",
            "register",
            "登録",
            "create",
            "追加",
            "保存",
            "upload_submit",
        )
    )


def _accept_dialog_safely(dialog) -> str:
    try:
        dialog.accept()
        return "accepted"
    except Exception as exc:
        message = str(exc)
        lowered = message.lower()
        if "already handled" in lowered or "already been handled" in lowered:
            return "already_handled"
        return f"accept_failed: {message}"


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


def _capture_native_browser_screen(page: Page, screenshot: Path, timeout: int = 15000) -> Dict[str, Any]:
    try:
        page.bring_to_front()
        try:
            page.keyboard.press("Control+0")
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


def _capture_composited_browser_screen(page: Page, screenshot: Path, timeout: int = 15000) -> Dict[str, Any]:
    """
    Render a browser-like evidence image from the exact Playwright page.

    Native OS screen grabs can capture whichever browser window is foreground,
    which is fragile when legacy/new pages run side-by-side. This composited
    mode keeps the URL evidence while making the content come from the page
    object that is being compared.
    """
    temp_content = screenshot.with_name(f"{screenshot.stem}_content_tmp.png")
    try:
        page.bring_to_front()
        try:
            page.keyboard.press("Control+0")
        except (PlaywrightTimeoutError, PlaywrightError):
            pass
        page.wait_for_timeout(150)
        page.screenshot(path=str(temp_content), full_page=False, timeout=timeout)

        content = Image.open(temp_content).convert("RGB")
        width, height = 1920, 1080
        chrome_h = 86
        canvas = Image.new("RGB", (width, height), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)

        draw.rectangle((0, 0, width, chrome_h), fill=(240, 242, 245))
        draw.rectangle((0, 0, width, 34), fill=(229, 232, 237))
        draw.rounded_rectangle((14, 8, 360, 34), radius=8, fill=(255, 255, 255), outline=(206, 212, 220))
        draw.text((28, 16), "Moonlight Regression", fill=(48, 57, 70))
        draw.rounded_rectangle((74, 44, width - 120, 76), radius=14, fill=(255, 255, 255), outline=(196, 203, 213))
        draw.text((92, 53), _safe_page_url(page), fill=(30, 41, 59))
        draw.line((0, chrome_h - 1, width, chrome_h - 1), fill=(205, 211, 220))

        content_h = height - chrome_h
        scale = min(width / max(content.width, 1), content_h / max(content.height, 1))
        next_size = (max(1, int(content.width * scale)), max(1, int(content.height * scale)))
        resample_filter = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
        resized = content.resize(next_size, resample_filter)
        canvas.paste(resized, (0, chrome_h))
        canvas.save(screenshot)
        return {
            "ok": True,
            "screenshot_scope": "browser_screen_composited_1920x1080",
            "screenshot_resolution": "1920x1080",
            "includes_browser_chrome": True,
            "browser_chrome_source": "synthetic_url_bar",
            "url_bar": _safe_page_url(page),
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
    finally:
        try:
            temp_content.unlink(missing_ok=True)
        except Exception:
            pass


def _capture_browser_screen(page: Page, screenshot: Path, timeout: int = 15000) -> Dict[str, Any]:
    """
    Capture browser evidence with URL bar at 1920x1080.

    Default mode is composited to avoid legacy/new foreground-window cross
    contamination. Set MOONLIGHT_BROWSER_SCREEN_MODE=native to use a real OS
    screen grab instead.
    """
    mode = str(os.environ.get("MOONLIGHT_BROWSER_SCREEN_MODE") or "composited").strip().lower()
    if mode in {"native", "os", "imagegrab"}:
        return _capture_native_browser_screen(page, screenshot, timeout=timeout)
    return _capture_composited_browser_screen(page, screenshot, timeout=timeout)


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
    "check": "check",
    "checked": "check",
    "checkbox": "check",
    "radio": "check",
    "set_checked": "check",
    "uncheck": "uncheck",
    "unchecked": "uncheck",
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
    "negative_js_error": "negative_js_error",
    "negative_http_500": "negative_http_500",
    "negative_network_abort": "negative_network_abort",
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
    for negative_action in NEGATIVE_ACTIONS:
        if negative_action in raw or negative_action in evidence:
            return negative_action
    if "clear" in raw:
        return "clear"
    if "uncheck" in raw or "unchecked" in raw:
        return "uncheck"
    if "check" in raw or "checkbox" in raw or "radio" in raw:
        return "check"
    if "press" in raw or "special_key" in raw:
        return "press"
    if "file_download" in raw or "download_template" in raw or "download" in raw or "download" in evidence or "ダウンロード" in evidence:
        return "download"
    if "upload" in raw or "file" in raw or "type='file'" in evidence or 'type="file"' in evidence:
        return "upload"
    if "select" in raw or ":select" in evidence or " select" in evidence:
        return "select"
    if "form" in raw or "submit" in raw or locator.startswith("form") or "[name=" in locator and "form" in locator:
        return "submit"
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


def _upload_placeholder_env_names(raw: str) -> List[str]:
    token = re.sub(r"[^A-Za-z0-9_]+", "_", raw.strip("${}$ ").upper()).strip("_")
    names = []
    if token:
        names.append(f"MOONLIGHT_{token}")
    names.append("MOONLIGHT_UPLOAD_FILE")
    return list(dict.fromkeys(names))


def _is_upload_placeholder(value: Any) -> bool:
    raw = str(value or "").strip()
    lowered = raw.lower()
    return (
        not raw
        or lowered.startswith("${")
        or lowered.startswith("$upload")
        or lowered in {"upload_file", "upload_invalid_file", "upload_empty_file", "upload_large_file"}
        or "fakepath" in lowered
    )


def _env_upload_file(raw: str) -> Optional[str]:
    for env_name in _upload_placeholder_env_names(raw):
        env_value = str(os.environ.get(env_name) or "").strip()
        if not env_value:
            continue
        env_path = Path(env_value)
        if env_path.exists():
            return str(env_path)
    return None


def _resolve_single_upload_file_value(value: Any, capture_dir: Optional[Union[str, Path]] = None) -> str:
    raw = str(value or "").strip()

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

    if _is_upload_placeholder(raw):
        env_value = _env_upload_file(raw)
        if env_value:
            return env_value
        return _default_upload_file(capture_dir)

    return raw


def _resolve_upload_file_value(value: Any, capture_dir: Optional[Union[str, Path]] = None) -> Union[str, List[str]]:
    if isinstance(value, (list, tuple)):
        resolved = [
            _resolve_single_upload_file_value(item, capture_dir)
            for item in value
            if str(item or "").strip()
        ]
        return resolved or _default_upload_file(capture_dir)
    return _resolve_single_upload_file_value(value, capture_dir)




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


def _safe_page_url(page: Page) -> str:
    try:
        if _page_is_closed(page):
            return "about:closed"
        return page.url
    except Exception:
        return "about:closed"


def _safe_frame_urls(page: Page) -> List[str]:
    try:
        if _page_is_closed(page):
            return []
        return [str(frame.url or "") for frame in page.frames]
    except Exception:
        return []


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
    before_url = _safe_page_url(page)
    before_frame_urls = _safe_frame_urls(page)
    result["before_url"] = before_url
    result["before_frame_urls"] = before_frame_urls
    console_events: List[Dict[str, Any]] = []
    event_handlers: List[Tuple[str, Any]] = []
    temporary_routes: List[Tuple[str, Any]] = []

    def _record_event(event_type: str, details: Any, *, level: str = "info", url: str = "", status: Optional[int] = None):
        # 内部闭包用于记录 Playwright 事件
        event = {
            "time": time.strftime("%H:%M:%S"),
            "type": event_type,
            "level": level,
            "detail": str(details),
            "url": str(url or ""),
        }
        if status is not None:
            event["status"] = status
        console_events.append(event)
        test_id_str = _safe_name(test_id or "global")
        log_dir = Path(capture_dir) if capture_dir else Path("./output/logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / f"{test_id_str}_events.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {event_type}: {details}\n")

    def _console_location(message: Any) -> str:
        try:
            location = message.location
        except Exception:
            location = {}
        if isinstance(location, dict):
            return str(location.get("url") or "")
        return ""

    def _on_console(message: Any):
        try:
            message_type = str(message.type)
        except Exception:
            message_type = "log"
        try:
            message_text = message.text
        except Exception as exc:
            message_text = f"<unable to read console message: {exc}>"
        _record_event("CONSOLE", f"{message_type}: {message_text}", level=message_type, url=_console_location(message))

    def _on_pageerror(error: Any):
        _record_event("JS_ERROR", str(error), level="pageerror")

    def _on_requestfailed(request: Any):
        try:
            failure = request.failure
        except Exception:
            failure = ""
        try:
            request_url = request.url
        except Exception:
            request_url = ""
        _record_event("REQ_FAILED", failure or request_url, level="requestfailed", url=request_url)

    def _on_response(response: Any):
        try:
            status = int(response.status)
        except Exception:
            status = 0
        if status < 400:
            return
        try:
            response_url = response.url
        except Exception:
            response_url = ""
        _record_event("HTTP_ERROR", f"HTTP {status}", level="http_error", url=response_url, status=status)

    def _attach_event(event_name: str, handler: Any):
        page.on(event_name, handler)
        event_handlers.append((event_name, handler))

    try:
        # 注入 Runtime Debugger
        _attach_event("console", _on_console)
        _attach_event("pageerror", _on_pageerror)
        _attach_event("requestfailed", _on_requestfailed)
        _attach_event("response", _on_response)
        if _should_accept_database_dialog(action_context):
            def _accept_dialog(dialog):
                accept_status = _accept_dialog_safely(dialog)
                dialog_state = {
                    "type": getattr(dialog, "type", ""),
                    "message": getattr(dialog, "message", ""),
                    "accept_status": accept_status,
                }
                result.setdefault("dialogs", []).append(dialog_state)
                _record_event("DIALOG", f"{dialog_state['type']}: {dialog_state['message']} ({accept_status})", level="info")

            page.once("dialog", _accept_dialog)

        if _page_is_closed(page):
            raise ValueError("Page is closed before action")

        result["wait_state"] = _wait_for_semantic_ready(page, timeout=max(timeout, 15000 if browser_name == "firefox" else timeout))
        semantic_action = result["semantic_action"]
        navigated_directly = False

        if semantic_action in NEGATIVE_ACTIONS:
            action_dispatched = True
            result["negative_action"] = semantic_action
            result["status"] = "PASS"
            result["reason"] = f"Negative action injected: {semantic_action}"
            result.setdefault("negative_visual_evidence", []).extend(
                _inject_negative_visual_evidence(
                    page,
                    semantic_action,
                    detail="Preparing simulated negative scenario.",
                    phase="before trigger",
                )
            )

            if semantic_action == "negative_js_error":
                result.setdefault("negative_visual_evidence", []).extend(
                    _inject_negative_visual_evidence(
                        page,
                        semantic_action,
                        detail="console.error and runtime throw were injected.",
                        phase="error injected",
                    )
                )
                page.evaluate(
                    """() => {
                        console.error('MOONLIGHT_NEGATIVE: simulated console error before submit');
                        setTimeout(() => { throw new Error('MOONLIGHT_NEGATIVE: simulated JavaScript runtime error'); }, 0);
                    }"""
                )
                page.wait_for_timeout(300)
            elif semantic_action in {"negative_http_500", "negative_network_abort"}:
                pattern = _negative_url_pattern(action_context, value)

                is_http_500 = semantic_action == "negative_http_500"

                negative_label = (
                    "HTTP 500"
                    if is_http_500
                    else "network abort"
                )

                result["status"] = "PASS"
                result["negative_action"] = semantic_action
                result["negative_url_pattern"] = pattern
                result["reason"] = (
                    f"Negative {negative_label} route injected: {pattern}"
                )
                result.setdefault("negative_visual_evidence", []).extend(
                    _inject_negative_visual_evidence(
                        page,
                        semantic_action,
                        detail=f"{negative_label} route is active.",
                        url=pattern,
                        phase="route mocked",
                    )
                )

                if is_http_500:
                    def _mock_http_500(route):
                        route.fulfill(
                            status=500,
                            content_type="text/html; charset=utf-8",
                            body=(
                                "<html><body>"
                                "<h1>MOONLIGHT_NEGATIVE simulated HTTP 500</h1>"
                                "</body></html>"
                            ),
                        )

                    route_handler = _mock_http_500

                else:
                    def _mock_network_abort(route):
                        route.abort("failed")

                    route_handler = _mock_network_abort

                page.route(pattern, route_handler)
                temporary_routes.append((pattern, route_handler))

                trigger_url = (
                    str(action_context.get("trigger_url") or "")
                    if action_context
                    else ""
                ).strip()

                if not trigger_url:
                    trigger_url = (
                        pattern
                        .replace("**/", "")
                        .replace("**", "")
                        .replace("*", "")
                        .strip()
                    )

                if not trigger_url:
                    trigger_url = "/"

                page.evaluate(
                    """async ({ url, label }) => {
                        console.error(
                            "MOONLIGHT_NEGATIVE: triggering " + label + " request " + url
                        );

                        try {
                            await fetch(url, { cache: "no-store" });
                        } catch (e) {
                            console.error(
                                "MOONLIGHT_NEGATIVE: " + label + " fetch failed " + e.message
                            );
                        }
                    }""",
                    {
                        "url": trigger_url,
                        "label": negative_label,
                    },
                )

                page.wait_for_timeout(800)

            if selector and selector not in {"-", "__page__"}:
                frame, frame_state = _frame_for_selector(page, selector, timeout=min(timeout, 5000))
                result.update(frame_state)
                locator = frame.locator(selector).first
                locator.wait_for(state="attached", timeout=timeout)
                try:
                    locator.click(timeout=timeout)
                except PlaywrightError:
                    locator.evaluate(
                        """element => {
                            const tag = element.tagName && element.tagName.toLowerCase();
                            if (tag === 'form') {
                                if (element.requestSubmit) element.requestSubmit();
                                else element.submit();
                            } else {
                                element.click();
                            }
                        }"""
                    )
            else:
                result["selector_found"] = True
                if semantic_action in {"negative_http_500", "negative_network_abort"}:
                    page.evaluate(
                        """pattern => fetch(String(pattern).replace(/^\\*\\*\\//, '/').replace(/\\*$/, ''), { cache: 'no-store' }).catch(() => null)""",
                        result.get("negative_url_pattern") or "/",
                    )
            result.setdefault("negative_visual_evidence", []).extend(
                _inject_negative_visual_evidence(
                    page,
                    semantic_action,
                    detail="Negative scenario completed; screenshot should include this visual marker.",
                    url=result.get("negative_url_pattern") or "",
                    phase="after trigger",
                )
            )
            page.wait_for_timeout(800)
        elif semantic_action in ("goto", "navigate") and re.match(r"^https?://|^/", str(selector or "")):
            action_dispatched = True
            page.goto(selector, wait_until="domcontentloaded", timeout=max(timeout, 30000))
            navigated_directly = True
        else:
            frame, frame_state = _frame_for_selector(page, selector, timeout=min(timeout, 5000))
            result.update(frame_state)

        if semantic_action in NEGATIVE_ACTIONS or navigated_directly:
            pass
        elif semantic_action in ("click", "navigate"):
            # 增加元素可见性检查
            locator = frame.locator(selector).first
            action_dispatched = True

            def _manual_click_fallback() -> None:
                locator.wait_for(state="attached", timeout=timeout)
                try:
                    locator.click(timeout=timeout, force=True)
                    result["manual_click_fallback"] = "force_click"
                    return
                except PlaywrightError:
                    locator.evaluate(
                        """element => {
                            const options = { bubbles: true, cancelable: true, view: window };
                            element.dispatchEvent(new MouseEvent("mousedown", options));
                            element.dispatchEvent(new MouseEvent("mouseup", options));
                            if (typeof element.click === "function") element.click();
                            else element.dispatchEvent(new MouseEvent("click", options));
                        }"""
                    )
                    result["manual_click_fallback"] = "dom_mouse_events"

            try:
                locator.wait_for(state="visible", timeout=timeout)
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
            except (PlaywrightTimeoutError, PlaywrightError):
                if not (action_context or {}).get("manual_replay"):
                    raise
                _manual_click_fallback()
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
        elif semantic_action in ("check", "uncheck"):
            locator = frame.locator(selector).first
            locator.wait_for(state="attached", timeout=timeout)
            desired = semantic_action == "check"
            action_dispatched = True
            try:
                locator.set_checked(desired, timeout=timeout, force=True)
                result["check_dispatch"] = "set_checked"
            except PlaywrightError:
                locator.evaluate(
                    """(element, checked) => {
                        const current = !!element.checked;
                        if (current !== checked) {
                            const options = { bubbles: true, cancelable: true, view: window };
                            element.dispatchEvent(new MouseEvent("mousedown", options));
                            element.dispatchEvent(new MouseEvent("mouseup", options));
                            if (typeof element.click === "function") element.click();
                            else element.dispatchEvent(new MouseEvent("click", options));
                        }
                        element.checked = checked;
                        element.dispatchEvent(new Event("input", { bubbles: true }));
                        element.dispatchEvent(new Event("change", { bubbles: true }));
                    }""",
                    desired,
                )
                result["check_dispatch"] = "dom_checked_events"
            result["checked"] = desired
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
            result["submit_dispatch"] = locator.evaluate(
                """element => {
                    const tag = element.tagName && element.tagName.toLowerCase();
                    if (tag !== "form") {
                        element.click();
                        return "clicked_element";
                    }
                    const form = element;
                    if (form && form.requestSubmit) form.requestSubmit();
                    else if (form) form.submit();
                    return "submitted_form";
                }"""
            )
        elif semantic_action == "download":
            action_dispatched = True
            click_closed_error: Optional[BaseException] = None
            try:
                locator = frame.locator(selector).first
                locator.wait_for(state="attached", timeout=timeout)
                with page.context.expect_event("download", timeout=45000) as download_info:
                    try:
                        locator.click(timeout=timeout)
                    except (PlaywrightTimeoutError, PlaywrightError) as click_exc:
                        if _is_target_closed_error(click_exc) or _page_is_closed(page):
                            click_closed_error = click_exc
                        else:
                            raise
                _record_download_result(result, download_info.value)
                if click_closed_error or _page_is_closed(page):
                    result["page_closed_after_action"] = True
                    result["download_closed_page"] = True
                    result["reason"] = "Download event captured before/while the download window closed."
            except (PlaywrightTimeoutError, PlaywrightError) as e:
                if click_closed_error or _is_target_closed_error(e) or _page_is_closed(page):
                    result.update(
                        {
                            "status": "PASS",
                            "page_closed_after_action": True,
                            "download_closed_page": True,
                            "download_event_missing": True,
                            "reason": (
                                "Download action closed the page before Playwright exposed a download event; "
                                "treated as a normal closed download flow."
                            ),
                            "post_wait_state": {"page_closed": True, "settle_error": str(e)},
                        }
                    )
                else:
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
        closes_page = result.get("semantic_action") in ("click", "navigate", "submit", "goto", "download")
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
        after_url = _safe_page_url(capture_page)
        after_frame_urls = _safe_frame_urls(capture_page)
        page_closed_after = bool(result.get("page_closed_after_action")) or _page_is_closed(capture_page)
        popup_detected = bool(result.get("popup_opened"))
        navigation_detected = before_url != after_url
        frame_changed = before_frame_urls != after_frame_urls
        result.update(
            {
                "after_url": after_url,
                "after_frame_urls": after_frame_urls,
                "navigation_detected": navigation_detected,
                "popup_detected": popup_detected,
                "frame_changed": frame_changed,
                "validation_only": bool(
                    result.get("status") == "PASS"
                    and not navigation_detected
                    and not popup_detected
                    and not frame_changed
                    and not page_closed_after
                ),
            }
        )
        result["console_events"] = console_events
        result["console_error_count"] = sum(
            1
            for event in console_events
            if str(event.get("level") or "").lower() in {"error", "pageerror"}
            or str(event.get("type") or "").upper() == "JS_ERROR"
        )
        result["http_error_count"] = sum(1 for event in console_events if str(event.get("type") or "").upper() == "HTTP_ERROR")
        result["request_failed_count"] = sum(1 for event in console_events if str(event.get("type") or "").upper() == "REQ_FAILED")
        if capture_dir:
            name = _safe_name(test_id or f"{action_type}_{int(time.time() * 1000)}")
            result["state"] = _capture_state(capture_page, Path(capture_dir), name)
            if console_events or _needs_console_evidence(action_context, action_type):
                result["console_evidence_screenshot"] = _render_console_evidence_image(
                    console_events,
                    Path(capture_dir),
                    name,
                )
            if capture_page is not page and not keep_popup:
                try:
                    capture_page.close()
                except PlaywrightError:
                    pass
        for event_name, handler in event_handlers:
            try:
                page.remove_listener(event_name, handler)
            except Exception:
                pass
        for pattern, handler in temporary_routes:
            try:
                page.unroute(pattern, handler)
            except Exception:
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
