from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page


RUNTIME_PROFILE_SCHEMA = "moonlight.runtime_page_profile.v1"


def _page_is_closed(page: Optional[Page]) -> bool:
    if page is None:
        return True
    try:
        return page.is_closed()
    except PlaywrightError:
        return True


def _control_counts(controls: List[Dict[str, Any]]) -> Dict[str, int]:
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
    for control in controls:
        tag = str(control.get("tag") or "").lower()
        control_type = str(control.get("type") or "").lower()
        if tag == "form":
            counts["form"] += 1
        elif tag == "table":
            counts["table"] += 1
        elif tag == "a":
            counts["link"] += 1
        elif tag == "button":
            counts["button"] += 1
        elif tag == "select":
            counts["select"] += 1
        elif tag == "textarea":
            counts["textarea"] += 1
        elif tag == "input":
            if control_type == "file":
                counts["file"] += 1
            elif control_type in {"button", "submit", "reset", "image"}:
                counts["button"] += 1
            elif control_type != "hidden":
                counts["input"] += 1
    return counts


def _capture_frame_controls(frame, frame_index: int) -> Dict[str, Any]:
    script = """
    () => {
      const attrEscape = value => String(value || '')
        .replace(/\\\\/g, '\\\\\\\\')
        .replace(/"/g, '\\\\"');
      const identEscape = window.CSS && window.CSS.escape
        ? window.CSS.escape.bind(window.CSS)
        : value => String(value || '').replace(/[^a-zA-Z0-9_-]/g, ch => '\\\\' + ch);
      const hasBox = el => {
        const rects = Array.from(el.getClientRects ? el.getClientRects() : []);
        return rects.some(rect => rect.width > 0 && rect.height > 0);
      };
      const isVisible = el => {
        if (!el || !el.tagName) return false;
        const style = window.getComputedStyle(el);
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && style.opacity !== '0'
          && hasBox(el);
      };
      const hasVisibleControl = el => Array.from(el.querySelectorAll('a,button,input,select,textarea,[onclick],[formaction]'))
        .some(child => isVisible(child));
      const includeElement = el => {
        const tag = el.tagName.toLowerCase();
        if (isVisible(el)) return true;
        return (tag === 'form' || tag === 'table') && hasVisibleControl(el);
      };
      const pathSelector = el => {
        const path = [];
        let node = el;
        while (node && node.nodeType === 1 && node !== document.body && path.length < 6) {
          const tag = node.tagName.toLowerCase();
          const siblings = Array.from(node.parentElement ? node.parentElement.children : [])
            .filter(sibling => sibling.tagName === node.tagName);
          const nth = siblings.length > 1 ? `:nth-of-type(${siblings.indexOf(node) + 1})` : '';
          path.unshift(`${tag}${nth}`);
          node = node.parentElement;
        }
        return path.length ? path.join(' > ') : el.tagName.toLowerCase();
      };
      const selectorFor = el => {
        const tag = el.tagName.toLowerCase();
        const id = el.getAttribute('id');
        const name = el.getAttribute('name');
        const type = el.getAttribute('type');
        const href = el.getAttribute('href');
        const action = el.getAttribute('action');
        const formAction = el.getAttribute('formaction');
        const onclick = el.getAttribute('onclick');
        const value = el.getAttribute('value');
        if (id) return `#${identEscape(id)}`;
        if (name && type) return `${tag}[name="${attrEscape(name)}"][type="${attrEscape(type)}"]`;
        if (name) return `${tag}[name="${attrEscape(name)}"]`;
        if (href && href !== '#') return `${tag}[href="${attrEscape(href)}"]`;
        if (formAction) return `${tag}[formaction="${attrEscape(formAction)}"]`;
        if (action) return `${tag}[action="${attrEscape(action)}"]`;
        if (onclick) return `${tag}[onclick*="${attrEscape(onclick.replace(/\\s+/g, ' ').trim().slice(0, 90))}"]`;
        if (value) return `${tag}[value="${attrEscape(value)}"]`;
        return pathSelector(el);
      };
      return Array.from(document.querySelectorAll('form,input,select,textarea,button,a,table,[onclick],[formaction]'))
        .filter(includeElement)
        .slice(0, 1000)
        .map(el => {
          const attrs = {};
          for (const attr of Array.from(el.attributes || [])) {
            attrs[attr.name] = attr.value;
          }
          const ownerForm = el.form || el.closest('form');
          const tag = el.tagName.toLowerCase();
          const directAction = el.getAttribute('action') || '';
          const formAction = el.getAttribute('formaction') || '';
          const ownerFormAction = ownerForm ? ownerForm.getAttribute('action') || '' : '';
          return {
            selector: selectorFor(el),
            tag,
            type: el.getAttribute('type') || '',
            text: (el.innerText || el.value || el.getAttribute('title') || el.getAttribute('aria-label') || '').trim().slice(0, 200),
            value: el.getAttribute('value') || '',
            name: el.getAttribute('name') || '',
            id: el.getAttribute('id') || '',
            href: el.getAttribute('href') || '',
            onclick: el.getAttribute('onclick') || '',
            action: directAction || formAction || ownerFormAction,
            formAction,
            ownerFormAction,
            ownerFormId: ownerForm ? ownerForm.getAttribute('id') || '' : '',
            ownerFormName: ownerForm ? ownerForm.getAttribute('name') || '' : '',
            method: el.getAttribute('method') || (ownerForm ? ownerForm.getAttribute('method') || '' : ''),
            enctype: el.getAttribute('enctype') || (ownerForm ? ownerForm.getAttribute('enctype') || '' : ''),
            target: el.getAttribute('target') || (ownerForm ? ownerForm.getAttribute('target') || '' : ''),
            disabled: Boolean(el.disabled) || el.getAttribute('disabled') !== null,
            visible: isVisible(el),
            attributes: attrs,
            raw: (el.outerHTML || '').slice(0, 1500)
          };
        });
    }
    """
    controls = frame.evaluate(script)
    if not isinstance(controls, list):
        controls = []
    return {
        "frame_index": frame_index,
        "frame_url": frame.url,
        "controls": controls,
    }


def capture_runtime_page_profile(
    page: Page,
    *,
    target_page: str = "",
    target_page_name: str = "",
    route_id: str = "",
    side: str = "",
    login_entry: str = "",
    limit: int = 1000,
) -> Dict[str, Any]:
    frames: List[Dict[str, Any]] = []
    controls: List[Dict[str, Any]] = []
    page_url = ""

    if not _page_is_closed(page):
        try:
            page_url = page.url
        except PlaywrightError:
            page_url = ""
        for frame_index, frame in enumerate(page.frames):
            try:
                frame_profile = _capture_frame_controls(frame, frame_index)
            except PlaywrightError:
                continue
            for control in frame_profile.get("controls") or []:
                if not isinstance(control, dict):
                    continue
                copied = dict(control)
                copied["frame_index"] = frame_profile["frame_index"]
                copied["frame_url"] = frame_profile["frame_url"]
                controls.append(copied)
                if len(controls) >= limit:
                    break
            frames.append(frame_profile)
            if len(controls) >= limit:
                break

    return {
        "schema": RUNTIME_PROFILE_SCHEMA,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "target_page": target_page,
        "target_page_name": target_page_name,
        "page_id": target_page_name or target_page,
        "route_id": route_id,
        "side": side,
        "login_entry": login_entry,
        "url": page_url,
        "counts": _control_counts(controls),
        "controls": controls[:limit],
        "frames": frames,
    }
