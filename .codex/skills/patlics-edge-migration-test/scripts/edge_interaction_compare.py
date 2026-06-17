from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import re
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import urlparse
from urllib.request import urlopen

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from workspace import workspace_root

ROOT = workspace_root()
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from edge_popup_compare import (  # noqa: E402
    capture_page,
    compare_records,
    normalize_obj,
    normalize_text,
    rel,
    replace_hosts,
)
from screenshot_compare import compare_visual_screenshot  # noqa: E402


DANGEROUS_WORDS = [
    "delete",
    "remove",
    "submit",
    "send",
    "register",
    "update",
    "confirm",
    "execute",
    "save",
    "\u524a\u9664",
    "\u767b\u9332",
    "\u66f4\u65b0",
    "\u9001\u4fe1",
    "\u78ba\u5b9a",
    "\u5b9f\u884c",
    "\u4fdd\u5b58",
]

PDF_MINOR_SIZE_DELTA_BYTES = 4


def safe_filename(value: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "")).strip(" ._")
    return text or "artifact"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_artifact_filename(filename: str) -> str:
    return re.sub(r"\d{14}", "{TIMESTAMP}", str(filename or ""))


def zip_content_manifest(path: Path) -> Dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = []
            for info in archive.infolist():
                data = archive.read(info.filename)
                entries.append(
                    {
                        "filename": info.filename,
                        "file_size": info.file_size,
                        "crc": info.CRC,
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
            return {"ok": True, "entries": entries}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "entries": []}


def normalized_pdf_sha256(path: Path) -> str:
    data = path.read_bytes()
    data = re.sub(
        rb"/(CreationDate|ModDate) \(D:[^)]*\)",
        lambda match: b"/" + match.group(1) + b" (D:{PDF_DATE})",
        data,
    )
    data = re.sub(rb"/ID\s*\[[^\]]+\]", b"/ID [{PDF_ID}]", data)
    return hashlib.sha256(data).hexdigest()


def cdp_targets(cdp_base: str) -> List[Dict[str, Any]]:
    with urlopen(cdp_base.rstrip("/") + "/json/list", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def close_cdp_targets(cdp_base: str, prefixes: Tuple[str, ...]) -> List[str]:
    closed = []
    try:
        targets = cdp_targets(cdp_base)
    except Exception:
        return closed
    for target in targets:
        url = str(target.get("url") or "")
        target_id = target.get("id")
        if not target_id or not any(url.startswith(prefix) for prefix in prefixes):
            continue
        try:
            with urlopen(cdp_base.rstrip("/") + "/json/close/" + str(target_id), timeout=2):
                pass
            closed.append(url)
        except Exception:
            pass
    return closed


def drain_cdp_targets(cdp_base: str, prefixes: Tuple[str, ...], attempts: int = 6, delay_sec: float = 0.35) -> List[str]:
    closed: List[str] = []
    for _ in range(attempts):
        chunk = close_cdp_targets(cdp_base, prefixes)
        closed.extend(chunk)
        try:
            remaining = [
                item
                for item in cdp_targets(cdp_base)
                if any(str(item.get("url") or "").startswith(prefix) for prefix in prefixes)
            ]
        except Exception:
            remaining = []
        if not remaining:
            break
        time.sleep(delay_sec)
    return closed


def print_target_ids(cdp_base: str) -> set[str]:
    try:
        return {
            str(item.get("id"))
            for item in cdp_targets(cdp_base)
            if item.get("type") == "page" and str(item.get("url") or "").startswith("edge://print")
        }
    except Exception:
        return set()


def wait_new_print_targets(cdp_base: str, before: set[str], timeout_sec: float) -> List[Dict[str, Any]]:
    deadline = time.time() + timeout_sec
    latest: List[Dict[str, Any]] = []
    while time.time() < deadline:
        try:
            latest = [
                item
                for item in cdp_targets(cdp_base)
                if item.get("type") == "page"
                and str(item.get("url") or "").startswith("edge://print")
                and str(item.get("id")) not in before
            ]
            if latest:
                return latest
        except Exception:
            pass
        time.sleep(0.25)
    return latest


ENUMERATE_JS = r"""
() => {
  const norm = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
  const esc = (value) => {
    if (window.CSS && CSS.escape) return CSS.escape(String(value));
    return String(value).replace(/["\\]/g, "\\$&");
  };
  const cssPath = (el) => {
    if (!el || !el.tagName) return "";
    const tag = el.tagName.toLowerCase();
    const name = el.getAttribute("name");
    const type = el.getAttribute("type");
    if (name) {
      let selector = `${tag}[name="${esc(name)}"]`;
      if (type) selector += `[type="${esc(type)}"]`;
      return selector;
    }
    if (el.id) return `${tag}#${esc(el.id)}`;
    const parts = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE && parts.length < 9) {
      let part = node.tagName.toLowerCase();
      if (node.id) {
        part += "#" + esc(node.id);
        parts.unshift(part);
        break;
      }
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((x) => x.tagName === node.tagName);
        if (siblings.length > 1) {
          part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
        }
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(" > ");
  };
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  };
  const actionType = (el) => {
    const tag = el.tagName.toLowerCase();
    const type = String(el.getAttribute("type") || "").toLowerCase();
    if (tag === "select") return "select_option";
    if (tag === "textarea") return "set_text";
    if (tag === "input" && ["text", "search", "email", "number", "password", "tel", "url", ""].includes(type)) return "set_text";
    if (tag === "input" && type === "checkbox") return "toggle_checkbox";
    if (tag === "input" && type === "radio") return "select_radio";
    if (tag === "button" || tag === "a" || (tag === "input" && ["button", "submit"].includes(type))) return "click";
    return "";
  };
  const elements = Array.from(document.querySelectorAll(
    'button,input[type="button"],input[type="submit"],input[type="checkbox"],input[type="radio"],input[type="text"],input:not([type]),a[href],a[onclick],select,textarea'
  ));
  return elements.map((el, index) => {
    const tag = el.tagName.toLowerCase();
    const type = String(el.getAttribute("type") || "").toLowerCase();
    const options = tag === "select" ? Array.from(el.options || []).map((opt, optionIndex) => ({
      optionIndex,
      text: norm(opt.text),
      value: String(opt.value ?? ""),
      selected: Boolean(opt.selected),
    })) : [];
    const text = norm(el.innerText || el.textContent || el.getAttribute("title") || el.getAttribute("alt") || "");
    const value = "value" in el ? String(el.value ?? "") : String(el.getAttribute("value") || "");
    return {
      index,
      selector: cssPath(el),
      tag,
      type,
      id: el.getAttribute("id") || "",
      name: el.getAttribute("name") || "",
      text,
      value,
      href: el.getAttribute("href") || "",
      onclick: el.getAttribute("onclick") || "",
      action: actionType(el),
      visible: visible(el),
      disabled: Boolean(el.disabled),
      checked: Boolean(el.checked),
      selectedIndex: "selectedIndex" in el ? el.selectedIndex : null,
      options,
    };
  }).filter((item) => item.action && item.visible && !item.disabled);
}
"""


FORM_STATE_JS = r"""
() => Array.from(document.querySelectorAll("input,select,textarea")).map((el, index) => {
  const tag = el.tagName.toLowerCase();
  return {
    index,
    tag,
    type: String(el.getAttribute("type") || "").toLowerCase(),
    id: el.getAttribute("id") || "",
    name: el.getAttribute("name") || "",
    value: "value" in el ? String(el.value ?? "") : "",
    checked: Boolean(el.checked),
    selectedIndex: "selectedIndex" in el ? el.selectedIndex : null,
    disabled: Boolean(el.disabled),
    visible: Boolean(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
  };
})
"""


VISIBLE_CONTROL_JS = r"""
() => Array.from(document.querySelectorAll("button,input,select,textarea,a[href],a[onclick]")).map((el, index) => {
  const norm = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
  const rect = el.getBoundingClientRect();
  const style = getComputedStyle(el);
  return {
    index,
    tag: el.tagName.toLowerCase(),
    type: String(el.getAttribute("type") || "").toLowerCase(),
    id: el.getAttribute("id") || "",
    name: el.getAttribute("name") || "",
    text: norm(el.innerText || el.textContent || el.getAttribute("title") || el.getAttribute("alt") || ""),
    value: "value" in el ? String(el.value ?? "") : String(el.getAttribute("value") || ""),
    checked: Boolean(el.checked),
    disabled: Boolean(el.disabled),
    visible: rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden",
  };
}).filter((item) => item.visible)
"""


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "case"


def normalize_dynamic(value: Any, hosts: Iterable[str]) -> Any:
    if isinstance(value, str):
        text = replace_hosts(value, hosts)
        text = re.sub(
            r"([?&][^=&]*(?:session|token|userid|userId|timestamp|nonce|csrf)[^=]*=)[^&#]+",
            r"\1{DYNAMIC}",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"\b\d{14}\b", "{TIMESTAMP14}", text)
        return text
    if isinstance(value, list):
        return [normalize_dynamic(item, hosts) for item in value]
    if isinstance(value, dict):
        normalized: Dict[str, Any] = {}
        for key in sorted(value):
            key_text = str(key)
            if re.search(r"(session|token|userid|timestamp|nonce|csrf)", key_text, re.IGNORECASE):
                normalized[key] = "{DYNAMIC}"
            else:
                normalized[key] = normalize_dynamic(value[key], hosts)
        return normalized
    return value


def action_text(element: Dict[str, Any]) -> str:
    return " ".join(
        str(element.get(key) or "")
        for key in ["text", "value", "id", "name", "href", "onclick"]
    )


def is_dangerous(element: Dict[str, Any]) -> bool:
    text = action_text(element).lower()
    return any(word.lower() in text for word in DANGEROUS_WORDS)


def behavior_type(element: Dict[str, Any], action: str) -> str:
    text = action_text(element).lower()
    if any(word in text for word in ["download", "output", "\u30c0\u30a6\u30f3\u30ed\u30fc\u30c9", "\u51fa\u529b"]):
        return "download"
    if any(word in text for word in ["print", "\u5370\u5237"]):
        return "print"
    if any(word in text for word in ["help", "\u30d8\u30eb\u30d7"]):
        return "navigation"
    if any(word in text for word in ["focus", "filter", "\u7d5e\u8fbc"]):
        return "parent_list_update"
    if any(word in text for word in ["close", "window.close", "\u9589\u3058"]):
        return "close"
    if action in {"select_option", "toggle_checkbox", "select_radio", "set_text"}:
        return "state_change"
    return "same_page"


def frame_label(page: Any, frame: Any, index: int) -> str:
    if frame == page.main_frame:
        return "main"
    name = frame.name or ""
    parsed = urlparse(frame.url or "")
    suffix = parsed.path.rsplit("/", 1)[-1] or "frame"
    return f"frame[{index}] {name} {suffix}".strip()


def enumerate_cases(page: Any, max_options: int) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    for frame_index, frame in enumerate(page.frames):
        try:
            elements = frame.evaluate(ENUMERATE_JS)
        except PlaywrightError:
            continue
        for element in elements:
            base = {
                "frame_index": frame_index,
                "frame": frame_label(page, frame, frame_index),
                "selector": element.get("selector"),
                "element": element,
                "dangerous": is_dangerous(element),
            }
            action = element.get("action")
            base["behavior_type"] = behavior_type(element, action)
            if action == "select_option":
                for option in (element.get("options") or [])[:max_options]:
                    if option.get("selected"):
                        continue
                    case = dict(base)
                    case.update(
                        {
                            "action": "select_option",
                            "option_index": option.get("optionIndex"),
                            "option_value": option.get("value"),
                            "option_text": option.get("text"),
                        }
                    )
                    cases.append(case)
            else:
                case = dict(base)
                case["action"] = action
                cases.append(case)
    return cases


def frame_for_case(page: Any, case: Dict[str, Any]) -> Any:
    frames = page.frames
    index = int(case.get("frame_index") or 0)
    if index >= len(frames):
        raise RuntimeError(f"Frame index {index} is not available; page has {len(frames)} frames")
    return frames[index]


def collect_frame_state(page: Any, hosts: Tuple[str, str]) -> Dict[str, Any]:
    forms = []
    visible_controls = []
    for frame_index, frame in enumerate(page.frames):
        label = frame_label(page, frame, frame_index)
        try:
            form_values = frame.evaluate(FORM_STATE_JS)
            controls = frame.evaluate(VISIBLE_CONTROL_JS)
        except PlaywrightError as exc:
            forms.append({"frame_index": frame_index, "frame": label, "error": str(exc)})
            visible_controls.append({"frame_index": frame_index, "frame": label, "error": str(exc)})
            continue
        forms.append({"frame_index": frame_index, "frame": label, "values": form_values})
        visible_controls.append({"frame_index": frame_index, "frame": label, "controls": controls})
    return {
        "form_values": normalize_form_state(normalize_dynamic(normalize_obj(forms, hosts), hosts)),
        "visible_controls": normalize_form_state(normalize_dynamic(normalize_obj(visible_controls, hosts), hosts)),
    }


def normalize_form_state(value: Any) -> Any:
    if isinstance(value, list):
        return [normalize_form_state(item) for item in value]
    if isinstance(value, dict):
        item = {key: normalize_form_state(val) for key, val in value.items()}
        name = str(item.get("name") or "")
        item_id = str(item.get("id") or "")
        if name and item_id == name:
            item["id"] = ""
        if re.search(r"(session|token|userid|userId|timestamp|nonce|csrf)", name, re.IGNORECASE):
            item["value"] = "{DYNAMIC}"
        return item
    return value


def install_observers(page: Any, hosts: Tuple[str, str]) -> Tuple[Dict[str, Any], Any, Any, Any]:
    events: Dict[str, Any] = {"dialogs": [], "network": [], "console_errors": [], "new_pages": []}
    request_started: Dict[Any, Dict[str, str]] = {}
    context = page.context
    known_pages = set(context.pages)

    def on_dialog(dialog: Any) -> None:
        item = {
            "type": dialog.type,
            "message": normalize_dynamic(dialog.message, hosts),
            "default_value": normalize_dynamic(dialog.default_value, hosts),
        }
        try:
            if dialog.type == "alert":
                dialog.accept()
                item["handled"] = "accept"
            else:
                dialog.dismiss()
                item["handled"] = "dismiss"
        except PlaywrightError as exc:
            item["handled_error"] = str(exc)
        events["dialogs"].append(item)

    def on_request(request: Any) -> None:
        request_started[request] = {
            "url": normalize_dynamic(request.url, hosts),
            "method": request.method,
        }

    def on_response(response: Any) -> None:
        req = response.request
        item = dict(request_started.get(req) or {})
        item.update(
            {
                "url": normalize_dynamic(response.url, hosts),
                "method": item.get("method") or req.method,
                "status": response.status,
            }
        )
        events["network"].append(item)

    def on_console(message: Any) -> None:
        if message.type == "error":
            events["console_errors"].append(normalize_dynamic(message.text, hosts))

    page.on("dialog", on_dialog)
    page.on("request", on_request)
    page.on("response", on_response)
    page.on("console", on_console)

    def finalize(close_new_pages: bool) -> Dict[str, Any]:
        for opened in [p for p in context.pages if p not in known_pages]:
            try:
                url = normalize_dynamic(opened.url, hosts)
            except PlaywrightError:
                url = ""
            events["new_pages"].append({"url": url})
            if close_new_pages:
                try:
                    opened.close()
                except PlaywrightError:
                    pass
        try:
            page.remove_listener("dialog", on_dialog)
            page.remove_listener("request", on_request)
            page.remove_listener("response", on_response)
            page.remove_listener("console", on_console)
        except Exception:
            pass
        events["network"] = events["network"][:80]
        events["console_errors"] = events["console_errors"][:40]
        return normalize_dynamic(events, hosts)

    return events, finalize, context, known_pages


def perform_action(page: Any, case: Dict[str, Any], test_value: str, wait_ms: int) -> Dict[str, Any]:
    result: Dict[str, Any] = {"ok": False}
    frame = frame_for_case(page, case)
    selector = str(case.get("selector") or "")
    locator = frame.locator(selector).first
    try:
        locator.wait_for(state="attached", timeout=3000)
        locator.scroll_into_view_if_needed(timeout=3000)
        action = case.get("action")
        if action == "click":
            if case.get("behavior_type") in {"download", "print"}:
                locator.evaluate("el => { el.click(); return true; }", timeout=3000)
            else:
                locator.click(timeout=5000, no_wait_after=True)
        elif action == "set_text":
            locator.fill(test_value, timeout=5000)
            locator.dispatch_event("input")
            locator.dispatch_event("change")
            locator.evaluate("el => el.blur && el.blur()")
        elif action == "toggle_checkbox":
            before = bool(case.get("element", {}).get("checked"))
            locator.set_checked(not before, timeout=5000)
            locator.dispatch_event("change")
        elif action == "select_radio":
            locator.check(timeout=5000)
            locator.dispatch_event("change")
        elif action == "select_option":
            value = str(case.get("option_value") or "")
            locator.select_option(value=value, timeout=5000)
            locator.dispatch_event("change")
        else:
            raise RuntimeError(f"Unsupported action: {action}")
        try:
            if case.get("behavior_type") in {"download", "print"}:
                page.wait_for_timeout(min(wait_ms, 300))
            else:
                page.wait_for_timeout(wait_ms)
        except Exception as exc:
            if hasattr(page, "is_closed") and page.is_closed():
                result["closed"] = True
            else:
                raise exc
        result["ok"] = True
    except Exception as exc:
        if hasattr(page, "is_closed") and page.is_closed():
            result["ok"] = True
            result["closed"] = True
        else:
            result["error"] = str(exc)
    try:
        result["after_url"] = "{CLOSED}" if page.is_closed() else page.url
    except Exception:
        result["after_url"] = "{CLOSED}"
    return result


def click_case_element(page: Any, case: Dict[str, Any], timeout_ms: int = 5000) -> None:
    frame = frame_for_case(page, case)
    locator = frame.locator(str(case.get("selector") or "")).first
    locator.wait_for(state="attached", timeout=3000)
    locator.scroll_into_view_if_needed(timeout=3000)
    locator.click(timeout=timeout_ms, no_wait_after=True)


def capture_download_artifact(page: Any, case: Dict[str, Any], case_dir: Path, side: str, args: argparse.Namespace) -> Dict[str, Any]:
    artifact_dir = case_dir / "artifacts" / side / "download"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    dialogs: List[Dict[str, Any]] = []
    result: Dict[str, Any] = {
        "ok": False,
        "side": side,
        "behavior_type": "download",
        "source_url": page.url,
        "source_title": page.title(),
        "selector": case.get("selector"),
        "artifact_timeout_ms": args.artifact_timeout_ms,
    }

    def handle_dialog(dialog: Any) -> None:
        dialogs.append({"type": dialog.type, "message": normalize_dynamic(dialog.message, (args.legacy_host, args.new_host))})
        dialog.accept()

    page.on("dialog", handle_dialog)
    try:
        with page.context.expect_event("download", timeout=args.artifact_timeout_ms) as download_info:
            click_case_element(page, case)
        download = download_info.value
        suggested = download.suggested_filename
        save_path = artifact_dir / safe_filename(suggested)
        download.save_as(str(save_path))
        result.update(
            {
                "ok": True,
                "artifact_collected": True,
                "download_url": normalize_dynamic(download.url, (args.legacy_host, args.new_host)),
                "download_filename": suggested,
                "normalized_download_filename": normalized_artifact_filename(suggested),
                "saved_path": str(save_path),
                "saved_filename": save_path.name,
                "download_size": save_path.stat().st_size,
                "download_sha256": sha256_file(save_path),
                "zip_content": zip_content_manifest(save_path),
                "dialogs": dialogs,
            }
        )
    except PlaywrightTimeoutError as exc:
        result.update({"error": f"Download event timeout: {exc}", "dialogs": dialogs})
    except Exception as exc:
        result.update({"error": str(exc), "dialogs": dialogs})
    finally:
        try:
            page.remove_listener("dialog", handle_dialog)
        except PlaywrightError:
            pass
        result["closed_aux_targets"] = drain_cdp_targets(args.cdp, ("edge://downloads",))
    return result


def capture_pdf_artifact(page: Any, case: Dict[str, Any], case_dir: Path, side: str, args: argparse.Namespace) -> Dict[str, Any]:
    artifact_dir = case_dir / "artifacts" / side / "pdf"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    title = safe_filename(page.title() or "print")
    pdf_path = artifact_dir / f"{title}.pdf"
    result: Dict[str, Any] = {
        "ok": False,
        "side": side,
        "behavior_type": "print",
        "source_url": page.url,
        "source_title": page.title(),
        "selector": case.get("selector"),
        "artifact_timeout_ms": args.artifact_timeout_ms,
    }
    before_print = print_target_ids(args.cdp)
    click_error = ""
    try:
        click_case_element(page, case)
    except Exception as exc:
        click_error = str(exc)
    new_print_targets = wait_new_print_targets(args.cdp, before_print, timeout_sec=min(args.artifact_timeout_ms / 1000, 12))
    try:
        session = page.context.new_cdp_session(page)
        try:
            payload = session.send(
                "Page.printToPDF",
                {
                    "printBackground": True,
                    "preferCSSPageSize": True,
                    "landscape": True,
                },
            )
        finally:
            try:
                session.detach()
            except PlaywrightError:
                pass
        data = payload.get("data")
        if not data:
            raise RuntimeError("Page.printToPDF returned no data")
        pdf_path.write_bytes(base64.b64decode(str(data)))
        raw = pdf_path.read_bytes()
        result.update(
            {
                "ok": not bool(click_error),
                "artifact_collected": True,
                "click_error": click_error,
                "print_preview_opened": bool(new_print_targets),
                "new_print_targets": [
                    {"id": item.get("id"), "title": item.get("title"), "url": item.get("url")}
                    for item in new_print_targets
                ],
                "pdf_path": str(pdf_path),
                "pdf_filename": pdf_path.name,
                "normalized_pdf_filename": normalized_artifact_filename(pdf_path.name),
                "pdf_size": pdf_path.stat().st_size,
                "pdf_header": raw[:8].decode("latin-1", errors="replace"),
                "pdf_sha256": sha256_file(pdf_path),
                "pdf_normalized_sha256": normalized_pdf_sha256(pdf_path),
                "pdf_backend": "cdp_print_to_pdf",
            }
        )
    except Exception as exc:
        result.update({"error": str(exc), "click_error": click_error})
    finally:
        result["closed_aux_targets"] = drain_cdp_targets(args.cdp, ("edge://print",))
    return result


def compare_artifacts(legacy: Dict[str, Any], new: Dict[str, Any], behavior: str) -> Dict[str, Any]:
    accepted_differences: List[Dict[str, Any]] = []
    if behavior == "download":
        checks = {
            "artifact_collected": bool(legacy.get("artifact_collected")) and bool(new.get("artifact_collected")),
            "download_url_equal": legacy.get("download_url") == new.get("download_url"),
            "filename_normalized_equal": legacy.get("normalized_download_filename") == new.get("normalized_download_filename"),
            "size_equal": legacy.get("download_size") == new.get("download_size"),
            "zip_content_equal": (legacy.get("zip_content") or {}).get("entries") == (new.get("zip_content") or {}).get("entries"),
        }
        checks["raw_sha256_equal"] = legacy.get("download_sha256") == new.get("download_sha256")
    else:
        legacy_size = legacy.get("pdf_size")
        new_size = new.get("pdf_size")
        pdf_size_delta = None
        if isinstance(legacy_size, int) and isinstance(new_size, int):
            pdf_size_delta = abs(legacy_size - new_size)
        checks = {
            "artifact_collected": bool(legacy.get("artifact_collected")) and bool(new.get("artifact_collected")),
            "filename_normalized_equal": legacy.get("normalized_pdf_filename") == new.get("normalized_pdf_filename"),
            "size_equal": legacy_size == new_size,
            "size_delta_bytes": pdf_size_delta,
            "minor_size_delta_allowed": pdf_size_delta is not None and pdf_size_delta <= PDF_MINOR_SIZE_DELTA_BYTES,
            "header_equal": legacy.get("pdf_header") == new.get("pdf_header"),
            "normalized_sha256_equal": legacy.get("pdf_normalized_sha256") == new.get("pdf_normalized_sha256"),
            "print_preview_opened_equal": bool(legacy.get("print_preview_opened")) == bool(new.get("print_preview_opened")),
        }
        checks["raw_sha256_equal"] = legacy.get("pdf_sha256") == new.get("pdf_sha256")
        if (
            checks["artifact_collected"]
            and checks["filename_normalized_equal"]
            and checks["header_equal"]
            and checks["print_preview_opened_equal"]
            and not checks["size_equal"]
            and checks["minor_size_delta_allowed"]
        ):
            accepted_differences.append(
                {
                    "type": "pdf_generator_minor_size_hash_delta",
                    "reason": "Accepted biz-Stream PDF generator upgrade difference with a small byte-size delta.",
                    "threshold_bytes": PDF_MINOR_SIZE_DELTA_BYTES,
                    "size_delta_bytes": pdf_size_delta,
                    "legacy_size": legacy_size,
                    "new_size": new_size,
                    "legacy_sha256": legacy.get("pdf_sha256"),
                    "new_sha256": new.get("pdf_sha256"),
                    "legacy_normalized_sha256": legacy.get("pdf_normalized_sha256"),
                    "new_normalized_sha256": new.get("pdf_normalized_sha256"),
                }
            )
    material_checks = {
        key: value
        for key, value in checks.items()
        if key not in {"raw_sha256_equal", "size_delta_bytes", "minor_size_delta_allowed"}
    }
    if not checks.get("artifact_collected"):
        status = "BLOCKED"
        raw_status = "BLOCKED"
    else:
        raw_status = "PASS" if all(material_checks.values()) else "DIFF"
        status = "PASS" if raw_status == "PASS" or accepted_differences else "DIFF"
    return {
        "status": status,
        "raw_status": raw_status,
        "checks": checks,
        "accepted_differences": accepted_differences,
        "legacy_artifact": legacy,
        "new_artifact": new,
    }


def restore_after_case(page: Any, before_url: str, case: Dict[str, Any]) -> None:
    try:
        if page.url != before_url and page.url != "about:blank":
            page.go_back(wait_until="domcontentloaded", timeout=5000)
            page.wait_for_timeout(600)
    except PlaywrightError:
        pass
    try:
        frame = frame_for_case(page, case)
        element = case.get("element") or {}
        selector = str(case.get("selector") or "")
        locator = frame.locator(selector).first
        action = case.get("action")
        if action == "set_text":
            locator.fill(str(element.get("value") or ""), timeout=3000)
            locator.dispatch_event("input")
            locator.dispatch_event("change")
        elif action == "toggle_checkbox":
            locator.set_checked(bool(element.get("checked")), timeout=3000)
            locator.dispatch_event("change")
        elif action == "select_radio" and not element.get("checked"):
            return
        elif action == "select_option":
            selected_index = element.get("selectedIndex")
            options = element.get("options") or []
            if selected_index is not None and 0 <= int(selected_index) < len(options):
                locator.select_option(value=str(options[int(selected_index)].get("value") or ""), timeout=3000)
                locator.dispatch_event("change")
    except Exception:
        pass


def comparable_events(events: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "dialogs": events.get("dialogs") or [],
        "network": [
            {"url": item.get("url"), "method": item.get("method"), "status": item.get("status")}
            for item in (events.get("network") or [])
        ],
        "new_pages": events.get("new_pages") or [],
        "console_errors": events.get("console_errors") or [],
    }


def compare_interaction(
    legacy_after: Dict[str, Any],
    new_after: Dict[str, Any],
    legacy_state: Dict[str, Any],
    new_state: Dict[str, Any],
    legacy_events: Dict[str, Any],
    new_events: Dict[str, Any],
    after_compare: Dict[str, Any],
    behavior: str,
) -> Dict[str, Any]:
    legacy_url = urlparse(legacy_after.get("url") or "")
    new_url = urlparse(new_after.get("url") or "")
    event_left = comparable_events(legacy_events)
    event_right = comparable_events(new_events)
    checks = {
        "url_path_equal": legacy_url.path == new_url.path,
        "url_query_equal": legacy_url.query == new_url.query,
        "title_equal": legacy_after.get("title") == new_after.get("title"),
        "text_equal": legacy_after.get("normalized_text_sha256") == new_after.get("normalized_text_sha256"),
        "dom_after_allowed_equal": bool(
            ((after_compare.get("allowed_differences") or {}).get("checks") or {}).get(
                "dom_equal_after_allowed_server_differences"
            )
        ),
        "controls_after_allowed_equal": bool(
            ((after_compare.get("allowed_differences") or {}).get("checks") or {}).get(
                "controls_equal_after_allowed_server_differences"
            )
        ),
        "form_values_equal": legacy_state.get("form_values") == new_state.get("form_values"),
        "visible_controls_equal": legacy_state.get("visible_controls") == new_state.get("visible_controls"),
        "dialogs_equal": event_left.get("dialogs") == event_right.get("dialogs"),
        "network_equal": event_left.get("network") == event_right.get("network"),
        "new_window_equal": event_left.get("new_pages") == event_right.get("new_pages"),
        "console_errors_equal": event_left.get("console_errors") == event_right.get("console_errors"),
        "screenshot_equal": (after_compare.get("visual") or {}).get("status") == "PASS",
    }
    if behavior in {"navigation"}:
        checks = {
            "dialogs_equal": checks["dialogs_equal"],
            "network_equal": checks["network_equal"],
            "new_window_equal": checks["new_window_equal"],
            "console_errors_equal": checks["console_errors_equal"],
        }
    elif behavior in {"download", "print"}:
        checks = {
            "dialogs_equal": checks["dialogs_equal"],
            "network_equal": checks["network_equal"],
            "new_window_equal": checks["new_window_equal"],
            "console_errors_equal": checks["console_errors_equal"],
        }
    elif behavior in {"parent_list_update"}:
        checks = {
            "dialogs_equal": checks["dialogs_equal"],
            "network_equal": checks["network_equal"],
            "new_window_equal": checks["new_window_equal"],
            "console_errors_equal": checks["console_errors_equal"],
        }
    elif behavior in {"state_change"}:
        checks.pop("url_query_equal", None)
    return {
        "status": "PASS" if all(checks.values()) else "DIFF",
        "checks": checks,
        "legacy_events": event_left,
        "new_events": event_right,
    }


def run_case(
    case: Dict[str, Any],
    case_index: int,
    legacy_page: Any,
    new_page: Any,
    output_dir: Path,
    hosts: Tuple[str, str],
    args: argparse.Namespace,
) -> Dict[str, Any]:
    case_id = f"{case_index:03d}_{safe_name(case.get('action', 'action'))}_{safe_name(case.get('selector', 'selector'))}"
    case_dir = output_dir / "interactions" / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    item: Dict[str, Any] = {
        "case_id": case_id,
        "status": "BLOCKED",
        "selector": case.get("selector"),
        "frame": case.get("frame"),
        "action": case.get("action"),
        "behavior_type": case.get("behavior_type"),
        "element": case.get("element"),
        "option_value": case.get("option_value"),
        "option_text": case.get("option_text"),
        "dangerous": case.get("dangerous"),
        "output_dir": str(case_dir),
    }
    if case.get("dangerous") and not args.include_dangerous:
        item["status"] = "SKIPPED"
        item["skip_reason"] = "server-side data-mutating operation; dry-run only"
        return item

    before_legacy = capture_page(legacy_page, "legacy_before", case_dir, hosts)
    before_new = capture_page(new_page, "new_before", case_dir, hosts)
    before_visual = compare_visual_screenshot(
        before_legacy["screenshot"],
        before_new["screenshot"],
        str(case_dir / "before_visual_diff.png"),
        threshold_percent=0.0,
        ignore_top_px=0,
    )
    legacy_before_state = collect_frame_state(legacy_page, hosts)
    new_before_state = collect_frame_state(new_page, hosts)
    legacy_start_url = legacy_page.url
    new_start_url = new_page.url

    behavior = str(case.get("behavior_type") or "")
    if behavior in {"download", "print"}:
        if behavior == "download":
            legacy_artifact = capture_download_artifact(legacy_page, case, case_dir, "legacy_47", args)
            new_artifact = capture_download_artifact(new_page, case, case_dir, "new_192", args)
            comparison_target = "download_artifact"
        else:
            legacy_artifact = capture_pdf_artifact(legacy_page, case, case_dir, "legacy_47", args)
            new_artifact = capture_pdf_artifact(new_page, case, case_dir, "new_192", args)
            comparison_target = "pdf_artifact"
        artifact_compare = compare_artifacts(legacy_artifact, new_artifact, behavior)
        item.update(
            {
                "status": artifact_compare["status"],
                "comparison_target": comparison_target,
                "legacy_action": legacy_artifact,
                "new_action": new_artifact,
                "before": {
                    "legacy": summarize_snapshot(before_legacy),
                    "new": summarize_snapshot(before_new),
                    "legacy_state": legacy_before_state,
                    "new_state": new_before_state,
                },
                "compare": {
                    **artifact_compare,
                },
                "screenshots": {
                    "legacy_before": before_legacy.get("screenshot"),
                    "new_before": before_new.get("screenshot"),
                    "before_diff": before_visual.get("diff_screenshot"),
                },
            }
        )
        (case_dir / "interaction_result.json").write_text(
            json.dumps(item, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return item

    _, legacy_finalize, _, _ = install_observers(legacy_page, hosts)
    _, new_finalize, _, _ = install_observers(new_page, hosts)
    legacy_action = perform_action(legacy_page, case, args.test_value, args.wait_ms)
    new_action = perform_action(new_page, case, args.test_value, args.wait_ms)
    time.sleep(args.settle_ms / 1000)
    legacy_events = legacy_finalize(not args.keep_new_pages)
    new_events = new_finalize(not args.keep_new_pages)
    if legacy_action.get("closed") or new_action.get("closed"):
        closure_equal = bool(legacy_action.get("closed")) == bool(new_action.get("closed"))
        event_left = comparable_events(legacy_events)
        event_right = comparable_events(new_events)
        checks = {
            "closure_equal": closure_equal,
            "dialogs_equal": event_left.get("dialogs") == event_right.get("dialogs"),
            "network_equal": event_left.get("network") == event_right.get("network"),
            "new_window_equal": event_left.get("new_pages") == event_right.get("new_pages"),
            "console_errors_equal": event_left.get("console_errors") == event_right.get("console_errors"),
        }
        item.update(
            {
                "status": "PASS" if all(checks.values()) else "DIFF",
                "comparison_target": "closure_state" if behavior == "close" else "parent_or_closure_state",
                "legacy_action": legacy_action,
                "new_action": new_action,
                "before": {
                    "legacy": summarize_snapshot(before_legacy),
                    "new": summarize_snapshot(before_new),
                    "legacy_state": legacy_before_state,
                    "new_state": new_before_state,
                },
                "compare": {
                    "status": "PASS" if all(checks.values()) else "DIFF",
                    "checks": checks,
                    "legacy_events": event_left,
                    "new_events": event_right,
                },
                "screenshots": {
                    "legacy_before": before_legacy.get("screenshot"),
                    "new_before": before_new.get("screenshot"),
                    "before_diff": before_visual.get("diff_screenshot"),
                },
            }
        )
        (case_dir / "interaction_result.json").write_text(
            json.dumps(item, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return item

    after_legacy = capture_page(legacy_page, "legacy_after", case_dir, hosts)
    after_new = capture_page(new_page, "new_after", case_dir, hosts)
    legacy_after_state = collect_frame_state(legacy_page, hosts)
    new_after_state = collect_frame_state(new_page, hosts)
    after_compare = compare_records(after_legacy, after_new, case_dir)
    case_compare = compare_interaction(
        after_legacy,
        after_new,
        legacy_after_state,
        new_after_state,
        legacy_events,
        new_events,
        after_compare,
        behavior,
    )

    item.update(
        {
            "status": case_compare["status"] if legacy_action.get("ok") and new_action.get("ok") else "DIFF",
            "legacy_action": legacy_action,
            "new_action": new_action,
            "before": {
                "legacy": summarize_snapshot(before_legacy),
                "new": summarize_snapshot(before_new),
                "legacy_state": legacy_before_state,
                "new_state": new_before_state,
            },
            "after": {
                "legacy": summarize_snapshot(after_legacy),
                "new": summarize_snapshot(after_new),
                "legacy_state": legacy_after_state,
                "new_state": new_after_state,
            },
            "compare": case_compare,
            "comparison_target": behavior,
            "page_compare": {
                key: after_compare.get(key)
                for key in ["status", "raw_status", "visual", "comparisons", "allowed_differences"]
            },
            "before_visual": before_visual,
            "screenshots": {
                "legacy_before": before_legacy.get("screenshot"),
                "new_before": before_new.get("screenshot"),
                "before_diff": before_visual.get("diff_screenshot"),
                "legacy_after": after_legacy.get("screenshot"),
                "new_after": after_new.get("screenshot"),
                "after_diff": (after_compare.get("visual") or {}).get("diff_screenshot"),
            },
        }
    )
    restore_after_case(legacy_page, legacy_start_url, case)
    restore_after_case(new_page, new_start_url, case)
    (case_dir / "interaction_result.json").write_text(
        json.dumps(item, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return item


def summarize_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "url": snapshot.get("url"),
        "title": snapshot.get("title"),
        "readyState": snapshot.get("readyState"),
        "counts": snapshot.get("counts"),
        "normalized_text_sha256": snapshot.get("normalized_text_sha256"),
        "normalized_dom_sha256": snapshot.get("normalized_dom_sha256"),
        "normalized_controls_sha256": snapshot.get("normalized_controls_sha256"),
        "screenshot": snapshot.get("screenshot"),
        "json": snapshot.get("json"),
    }


def render_interaction_report(result: Dict[str, Any], output_dir: Path) -> str:
    def screenshot_gallery(screenshots: Dict[str, Any]) -> str:
        figures = []
        for label, path in [
            ("legacy before", screenshots.get("legacy_before")),
            ("new before", screenshots.get("new_before")),
            ("before diff", screenshots.get("before_diff")),
            ("legacy after", screenshots.get("legacy_after")),
            ("new after", screenshots.get("new_after")),
            ("after diff", screenshots.get("after_diff")),
        ]:
            if not path:
                continue
            href = html.escape(rel(str(path), output_dir))
            figures.append(
                "<figure>"
                f"<figcaption>{html.escape(label)}</figcaption>"
                f"<a href=\"{href}\"><img loading=\"lazy\" src=\"{href}\" alt=\"{html.escape(label)}\"></a>"
                "</figure>"
            )
        return "<div class=\"screenshots\">{}</div>".format("".join(figures)) if figures else ""

    rows = []
    for case in result.get("cases") or []:
        cls = "ok" if case.get("status") in {"PASS", "SKIPPED"} else "ng"
        compare = case.get("compare") or {}
        checks = compare.get("checks") or {}
        accepted = compare.get("accepted_differences") or []
        diff_lines = [f"{key}: {value}" for key, value in checks.items() if value is False]
        if compare.get("raw_status") and compare.get("raw_status") != compare.get("status"):
            diff_lines.append(f"raw_status: {compare.get('raw_status')}")
        if accepted:
            diff_lines.append("accepted_differences:")
            diff_lines.append(json.dumps(accepted, ensure_ascii=False, indent=2))
        diff = "\n".join(diff_lines) or "No diff"
        screenshots = case.get("screenshots") or {}
        screenshot_html = screenshot_gallery(screenshots)
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(case.get('selector')))}</code><br>{html.escape(str(case.get('frame')))}</td>"
            f"<td>{html.escape(str((case.get('element') or {}).get('text') or (case.get('element') or {}).get('value') or ''))}</td>"
            f"<td>{html.escape(str(case.get('behavior_type') or ''))}</td>"
            f"<td>{html.escape(str(case.get('action')))}"
            f"{'<br>option=' + html.escape(str(case.get('option_text'))) if case.get('option_text') is not None else ''}</td>"
            f"<td>{html.escape(str(case.get('comparison_target') or case.get('behavior_type') or ''))}</td>"
            f"<td><pre>{html.escape(json.dumps(case.get('legacy_action'), ensure_ascii=False, indent=2))}</pre></td>"
            f"<td><pre>{html.escape(json.dumps(case.get('new_action'), ensure_ascii=False, indent=2))}</pre></td>"
            f"<td><pre>{html.escape(diff)}</pre></td>"
            f"<td class=\"{cls}\">{html.escape(str(case.get('status')))}</td>"
            "</tr>"
            f"<tr class=\"screenshot-row\"><td colspan=\"9\">{screenshot_html}</td></tr>"
        )
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>Interaction Behavior - {html.escape(result.get('status') or '')}</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #172033; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d4d9e2; padding: 8px; vertical-align: top; }}
    th {{ background: #f4f6f9; text-align: left; }}
    pre {{ white-space: pre-wrap; max-height: 260px; overflow: auto; }}
    .ok {{ color: #137333; font-weight: 600; }}
    .ng {{ color: #b3261e; font-weight: 600; }}
    code {{ background: #f6f8fa; padding: 1px 4px; }}
    .screenshot-row td {{ background: #fbfcfe; padding: 12px; }}
    .screenshots {{ display: grid; grid-template-columns: repeat(2, minmax(320px, 1fr)); gap: 14px; }}
    figure {{ margin: 0; }}
    figcaption {{ font-size: 12px; color: #5f6b7a; margin-bottom: 4px; font-weight: 600; }}
    img {{ display: block; width: 100%; max-height: 420px; object-fit: contain; border: 1px solid #cfd6df; background: #fff; }}
  </style>
</head>
<body>
  <h1>Interaction Behavior <span class="{'ok' if result.get('status') == 'PASS' else 'ng'}">{html.escape(result.get('status') or '')}</span></h1>
  <p>Target: <code>{html.escape(result.get('target_path') or '')}</code> / legacy: <code>{html.escape(result.get('legacy_host') or '')}</code> / new: <code>{html.escape(result.get('new_host') or '')}</code></p>
  <p>Coverage: {html.escape(str(result.get('coverage')))}</p>
  <h2>Interaction Behavior</h2>
  <table>
    <tr><th>Element selector</th><th>Element text/value</th><th>Behavior type</th><th>Action type</th><th>Comparison target</th><th>Legacy result</th><th>New result</th><th>Diff</th><th>Pass/Fail</th></tr>
    {''.join(rows)}
  </table>
</body>
</html>
"""


def status_counts_for(cases: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for case in cases:
        status = case.get("status") or "UNKNOWN"
        counts[status] = counts.get(status, 0) + 1
    return counts


def build_result(args: argparse.Namespace, cases: List[Dict[str, Any]], legacy_count: int, new_count: int) -> Dict[str, Any]:
    counts = status_counts_for(cases)
    material_statuses = [case.get("status") for case in cases if case.get("status") not in {"PASS", "SKIPPED"}]
    coverage_status = "PASS"
    coverage_issues: List[str] = []
    if legacy_count != new_count:
        coverage_status = "DIFF"
        coverage_issues.append("legacy/new actionable element counts differ")
    if legacy_count == 0 or new_count == 0:
        coverage_status = "BLOCKED"
        coverage_issues.append("no actionable elements were enumerated")
    if not cases and (legacy_count or new_count):
        coverage_status = "BLOCKED"
        coverage_issues.append("actionable elements were enumerated but no cases executed")
    if material_statuses:
        overall_status = "DIFF"
    elif coverage_status != "PASS":
        overall_status = coverage_status
    else:
        overall_status = "PASS"
    return {
        "status": overall_status,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_path": args.path,
        "legacy_host": args.legacy_host,
        "new_host": args.new_host,
        "browser": "Microsoft Edge via CDP",
        "coverage": {
            "legacy_enumerated": legacy_count,
            "new_enumerated": new_count,
            "executed_or_skipped": len(cases),
            "max_cases": args.max_cases,
            "status_counts": counts,
            "coverage_status": coverage_status,
            "coverage_issues": coverage_issues,
        },
        "cases": cases,
    }


def write_result_files(result: Dict[str, Any], output_dir: Path) -> Tuple[Path, Path]:
    result_path = output_dir / "interaction_result.json"
    report_path = output_dir / "interaction_report.html"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(render_interaction_report(result, output_dir), encoding="utf-8")
    return result_path, report_path


def find_page_or_frame_container(browser: Any, host: str, path_fragment: str) -> Any:
    page_matches = []
    frame_matches = []
    seen = []
    for context in browser.contexts:
        for page in context.pages:
            try:
                seen.append(page.url)
                if host in page.url and path_fragment in page.url:
                    page_matches.append(page)
                    continue
                for frame in page.frames:
                    if host in frame.url and path_fragment in frame.url:
                        frame_matches.append(page)
                        break
            except PlaywrightError:
                continue
    if page_matches:
        return page_matches[-1]
    if frame_matches:
        return frame_matches[-1]
    raise RuntimeError(f"No page or frame found for host={host!r}, path={path_fragment!r}. Seen URLs: {seen}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare page interaction behavior between PATLICS environments.")
    parser.add_argument("--cdp", default="http://127.0.0.1:9222")
    parser.add_argument("--legacy-host", default="192.168.167.47")
    parser.add_argument("--new-host", default="192.168.167.192")
    parser.add_argument("--path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-cases", type=int, default=80)
    parser.add_argument("--max-options", type=int, default=50)
    parser.add_argument("--wait-ms", type=int, default=900)
    parser.add_argument("--settle-ms", type=int, default=500)
    parser.add_argument("--artifact-timeout-ms", type=int, default=60000)
    parser.add_argument("--test-value", default="codex-interaction-test")
    parser.add_argument("--include-dangerous", action="store_true")
    parser.add_argument("--keep-new-pages", action="store_true")
    args = parser.parse_args()

    output_dir = (ROOT / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    hosts = (args.legacy_host, args.new_host)

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp)
        legacy_page = find_page_or_frame_container(browser, args.legacy_host, args.path)
        new_page = find_page_or_frame_container(browser, args.new_host, args.path)
        legacy_cases = enumerate_cases(legacy_page, args.max_options)
        new_cases = enumerate_cases(new_page, args.max_options)
        selected = legacy_cases[: args.max_cases]
        results = []
        for index, case in enumerate(selected, start=1):
            try:
                results.append(run_case(case, index, legacy_page, new_page, output_dir, hosts, args))
            except Exception as exc:
                results.append(
                    {
                        "case_id": f"{index:03d}_blocked",
                        "status": "BLOCKED",
                        "selector": case.get("selector"),
                        "frame": case.get("frame"),
                        "action": case.get("action"),
                        "behavior_type": case.get("behavior_type"),
                        "element": case.get("element"),
                        "dangerous": case.get("dangerous"),
                        "error": str(exc),
                    }
                )
                if "Target page, context or browser has been closed" in str(exc):
                    break
            partial = build_result(args, results, len(legacy_cases), len(new_cases))
            write_result_files(partial, output_dir)
            print(
                f"case {index}/{len(selected)} {results[-1].get('status')} {results[-1].get('action')} {results[-1].get('selector')}",
                flush=True,
            )
        browser.close()

    result = build_result(args, results, len(legacy_cases), len(new_cases))
    result_path, report_path = write_result_files(result, output_dir)
    print(json.dumps({"status": result["status"], "report": str(report_path), "json": str(result_path), "coverage": result["coverage"]}, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
