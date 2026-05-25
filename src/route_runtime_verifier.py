import hashlib
import json
import re
import time
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable, List, Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from src.action_executor import _capture_state, execute_action


ACTION_CONTROL_SELECTORS = (
    "a[href*='{needle}']",
    "a[onclick*='{needle}']",
    "button[onclick*='{needle}']",
    "input[onclick*='{needle}']",
    "button[formaction*='{needle}']",
    "input[formaction*='{needle}']",
)


def _action_needles(action: str) -> List[str]:
    raw = str(action or "").strip()
    if not raw:
        return []

    cleaned = raw.replace("\\", "/").split("?", 1)[0].split("#", 1)[0].strip()
    cleaned = cleaned.strip("'\"")
    base = cleaned.lstrip("/")
    no_suffix = re.sub(r"\.do$", "", base, flags=re.IGNORECASE)
    leaf = no_suffix.rstrip("/").rsplit("/", 1)[-1]

    variants: List[str] = []

    def add(value: str) -> None:
        value = str(value or "").strip()
        if value and value not in variants:
            variants.append(value)

    for value in (cleaned, base, f"/{base}", no_suffix, f"/{no_suffix}", f"{no_suffix}.do", f"/{no_suffix}.do"):
        add(value)

    if leaf and leaf != no_suffix:
        for value in (leaf, f"{leaf}.do", f"/{leaf}", f"/{leaf}.do", f"./{leaf}.do"):
            add(value)

    return variants


def _safe_id(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "route"))
    return text[:120] or "route"


def _find_reached_action_url(page: Page, action: str) -> Optional[str]:
    needles = [needle.lower().lstrip("./") for needle in _action_needles(action)]
    needles = [needle for needle in needles if len(needle) >= 3]
    if not needles:
        return None

    for frame in page.frames:
        try:
            url = str(frame.url or "").replace("\\", "/").lower()
        except PlaywrightError:
            continue
        if url and any(needle in url for needle in needles):
            return frame.url
    return None


def _route_signature(route: Dict[str, Any]) -> str:
    raw = json.dumps(route.get("nodes") or [], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _iter_action_nodes(route: Dict[str, Any]) -> Iterable[Dict[str, str]]:
    for node in route.get("nodes") or []:
        if str(node.get("type") or "").lower() == "action":
            yield {"type": "Action", "name": str(node.get("name") or "")}


def _visible_controls(page: Page, limit: int = 200) -> List[Dict[str, str]]:
    script = """
    () => Array.from(document.querySelectorAll('a,button,input,select,textarea,form'))
      .filter(el => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && rect.width > 0
          && rect.height > 0;
      })
      .slice(0, 200)
      .map(el => ({
        tag: el.tagName.toLowerCase(),
        type: el.getAttribute('type') || '',
        text: (el.innerText || el.value || el.getAttribute('title') || '').trim().slice(0, 80),
        href: el.getAttribute('href') || '',
        onclick: el.getAttribute('onclick') || '',
        action: el.getAttribute('action') || '',
        name: el.getAttribute('name') || '',
        id: el.getAttribute('id') || ''
      }))
    """
    controls: List[Dict[str, str]] = []
    for frame in page.frames:
        try:
            controls.extend(frame.evaluate(script))
        except PlaywrightError:
            continue
        if len(controls) >= limit:
            break
    return controls[:limit]


def _page_is_closed(page: Optional[Page]) -> bool:
    if page is None:
        return True
    try:
        return page.is_closed()
    except PlaywrightError:
        return True


def _action_context_for_locator(page: Page, action: str, selector: str) -> Dict[str, Any]:
    context: Dict[str, Any] = {
        "label": action,
        "action_type": "route_step",
        "locator": selector,
        "keep_popup": True,
    }
    script = """
    element => {
      const attributes = {};
      for (const attr of Array.from(element.attributes || [])) {
        attributes[attr.name] = attr.value;
      }
      return {
        raw: (element.outerHTML || '').slice(0, 2000),
        text: (element.innerText || element.value || element.getAttribute('title') || '').trim().slice(0, 300),
        attributes,
      };
    }
    """
    for frame in page.frames:
        try:
            locator = frame.locator(selector).first
            if not locator.count():
                continue
            details = locator.evaluate(script)
            if isinstance(details, dict):
                context.update(details)
                context["frame_url"] = frame.url
                break
        except (PlaywrightTimeoutError, PlaywrightError):
            continue
    return context


def _pages_for_context(page: Page) -> List[Page]:
    try:
        return list(page.context.pages)
    except PlaywrightError:
        return []


def _takeover_page_after_action(
    current_page: Page,
    pages_before: List[Page],
    action_result: Dict[str, Any],
    timeout: int,
) -> Optional[Page]:
    popup_page = action_result.pop("popup_page", None)
    before_ids = {id(item) for item in pages_before}
    candidates: List[Page] = []

    if popup_page is not None:
        candidates.append(popup_page)

    for candidate in _pages_for_context(current_page):
        if id(candidate) not in before_ids:
            candidates.append(candidate)

    seen = set()
    for candidate in candidates:
        if id(candidate) in seen or _page_is_closed(candidate):
            continue
        seen.add(id(candidate))
        try:
            candidate.wait_for_load_state("domcontentloaded", timeout=min(timeout, 10000))
        except (PlaywrightTimeoutError, PlaywrightError):
            pass
        try:
            candidate.bring_to_front()
        except PlaywrightError:
            pass
        return candidate
    return None


def _install_manual_recorder(page: Page, *, route_id: str, index: int) -> Dict[str, Any]:
    events: List[Dict[str, Any]] = []
    binding_name = f"__moonlightManualRecord_{_safe_id(route_id)}_{index}_{int(time.time() * 1000)}"

    def record(source, payload):
        if not isinstance(payload, dict):
            return
        event = dict(payload)
        try:
            event["page_url"] = source["page"].url
        except Exception:
            pass
        try:
            event["frame_url"] = source["frame"].url
        except Exception:
            pass
        events.append(event)

    try:
        page.expose_binding(binding_name, record)
    except PlaywrightError:
        pass

    script = """
    bindingName => {
      if (window.__moonlightManualRecorderBinding === bindingName) return;
      window.__moonlightManualRecorderBinding = bindingName;
      const cssEscape = window.CSS && window.CSS.escape
        ? window.CSS.escape.bind(window.CSS)
        : value => String(value).replace(/[^a-zA-Z0-9_-]/g, ch => '\\\\' + ch);
      const attrEscape = value => String(value || '').replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"');
      const selectorFor = el => {
        if (!el || !el.tagName) return '';
        const tag = el.tagName.toLowerCase();
        const id = el.getAttribute('id');
        const name = el.getAttribute('name');
        const type = el.getAttribute('type');
        const href = el.getAttribute('href');
        const onclick = el.getAttribute('onclick');
        if (id) return `${tag}#${cssEscape(id)}`;
        if (name && type) return `${tag}[name="${attrEscape(name)}"][type="${attrEscape(type)}"]`;
        if (name) return `${tag}[name="${attrEscape(name)}"]`;
        if (href && href !== '#') return `${tag}[href="${attrEscape(href)}"]`;
        if (onclick) {
          const compact = onclick.replace(/\\s+/g, ' ').trim();
          if (compact.length >= 12) return `${tag}[onclick*="${attrEscape(compact.slice(0, 80))}"]`;
        }
        const path = [];
        let node = el;
        while (node && node.nodeType === 1 && node !== document.body && path.length < 5) {
          const nodeTag = node.tagName.toLowerCase();
          const siblings = Array.from(node.parentElement ? node.parentElement.children : [])
            .filter(sibling => sibling.tagName === node.tagName);
          const nth = siblings.length > 1 ? `:nth-of-type(${siblings.indexOf(node) + 1})` : '';
          path.unshift(`${nodeTag}${nth}`);
          node = node.parentElement;
        }
        return path.length ? path.join(' > ') : tag;
      };
      const payloadFor = (eventType, event) => {
        const target = eventType === 'submit'
          ? event.target
          : (event.target && event.target.nodeType === 1 ? event.target : event.target && event.target.parentElement);
        if (!target || !target.tagName) return null;
        const tag = target.tagName.toLowerCase();
        const type = (target.getAttribute('type') || '').toLowerCase();
        const fileNames = type === 'file'
          ? Array.from(target.files || []).map(file => file.name)
          : [];
        return {
          event_type: eventType,
          selector: selectorFor(target),
          tag,
          type,
          name: target.getAttribute('name') || '',
          id: target.getAttribute('id') || '',
          value: type === 'file'
            ? ''
            : tag === 'select'
            ? target.value
            : (type === 'password' ? '' : (target.value || '')),
          file_names: fileNames,
          file_count: fileNames.length,
          checked: !!target.checked,
          text: (type === 'file'
            ? fileNames.join(', ')
            : (target.innerText || target.value || target.getAttribute('title') || '')
          ).trim().slice(0, 120),
          href: target.getAttribute('href') || '',
          onclick: (target.getAttribute('onclick') || '').slice(0, 240),
          action: target.getAttribute('action') || (target.closest && target.closest('form') ? target.closest('form').getAttribute('action') || '' : ''),
          timestamp: Date.now()
        };
      };
      const send = payload => {
        if (!payload || !payload.selector || !window[bindingName]) return;
        try { window[bindingName](payload); } catch (_) {}
      };
      ['input', 'change'].forEach(type => {
        document.addEventListener(type, event => send(payloadFor(type, event)), true);
      });
      document.addEventListener('click', event => send(payloadFor('click', event)), true);
      document.addEventListener('submit', event => send(payloadFor('submit', event)), true);
    }
    """

    try:
        page.add_init_script(f"({script})({json.dumps(binding_name)})")
    except PlaywrightError:
        pass
    for frame in page.frames:
        try:
            frame.evaluate(script, binding_name)
        except PlaywrightError:
            continue
    return {"binding_name": binding_name, "events": events}


def _upload_filename(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.replace("\\", "/")
    return Path(normalized).name or PureWindowsPath(text).name


def _resolve_manual_upload_value(event: Dict[str, Any], upload_file: Optional[str] = None) -> str:
    if upload_file:
        return str(upload_file)

    raw_value = str(event.get("value") or "").strip()
    if raw_value and "fakepath" not in raw_value.lower() and Path(raw_value).exists():
        return raw_value

    names: List[str] = []
    for name in event.get("file_names") or []:
        leaf = _upload_filename(name)
        if leaf and leaf not in names:
            names.append(leaf)

    raw_leaf = _upload_filename(raw_value)
    if raw_leaf and raw_leaf not in names:
        names.append(raw_leaf)

    for name in names:
        for root in (Path("test_data/upload"), Path("test_data"), Path("data/upload"), Path("data")):
            if not root.exists():
                continue
            direct = root / name
            if direct.exists():
                return str(direct)
            for candidate in root.rglob(name):
                if candidate.is_file():
                    return str(candidate)

    return ""


def _manual_replay_from_events(events: List[Dict[str, Any]], *, upload_file: Optional[str] = None) -> List[Dict[str, Any]]:
    replay: List[Dict[str, Any]] = []
    pending: Dict[str, Dict[str, Any]] = {}

    def flush(selector: Optional[str] = None) -> None:
        selectors = [selector] if selector else list(pending)
        for item_selector in selectors:
            event = pending.pop(item_selector, None)
            if not event:
                continue
            tag = str(event.get("tag") or "").lower()
            input_type = str(event.get("type") or "").lower()
            action_type = "fill"
            if tag == "select":
                action_type = "select"
            elif input_type in {"checkbox", "radio"}:
                action_type = "click"
            elif input_type == "file":
                action_type = "upload"
            value = event.get("value") or ""
            if action_type == "upload":
                value = _resolve_manual_upload_value(event, upload_file)
            replay.append(
                {
                    "action_type": action_type,
                    "selector": item_selector,
                    "value": value,
                    "event_type": event.get("event_type"),
                    "file_names": event.get("file_names") or [],
                }
            )

    for event in events:
        selector = str(event.get("selector") or "")
        if not selector:
            continue
        event_type = str(event.get("event_type") or "").lower()
        tag = str(event.get("tag") or "").lower()
        input_type = str(event.get("type") or "").lower()

        if event_type in {"input", "change"} and tag in {"input", "textarea", "select"}:
            pending[selector] = event
            continue

        if event_type == "click":
            if tag in {"input", "textarea", "select"} and input_type not in {"button", "submit", "reset", "image"}:
                continue
            flush()
            replay.append(
                {
                    "action_type": "click",
                    "selector": selector,
                    "value": "",
                    "event_type": event_type,
                    "text": event.get("text") or "",
                }
            )
            continue

        if event_type == "submit":
            flush()
            if replay and replay[-1].get("action_type") == "click":
                continue
            replay.append(
                {
                    "action_type": "submit",
                    "selector": selector,
                    "value": "",
                    "event_type": event_type,
                }
            )

    flush()
    compact: List[Dict[str, Any]] = []
    for item in replay:
        if compact and compact[-1] == item:
            continue
        compact.append(item)
    return compact


def _css_string(value: str) -> str:
    return '"' + str(value or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _runtime_controls(page: Page, limit: int = 500) -> List[Dict[str, Any]]:
    script = """
    () => {
      const cssEscape = window.CSS && window.CSS.escape
        ? window.CSS.escape.bind(window.CSS)
        : value => String(value).replace(/[^a-zA-Z0-9_-]/g, ch => '\\\\' + ch);
      const selectorFor = el => {
        const tag = el.tagName.toLowerCase();
        const id = el.getAttribute('id');
        const name = el.getAttribute('name');
        const type = el.getAttribute('type');
        const href = el.getAttribute('href');
        const onclick = el.getAttribute('onclick');
        const action = el.getAttribute('action');
        const formAction = el.getAttribute('formaction');
        const ownerForm = el.form || el.closest('form');
        const ownerFormAction = ownerForm ? ownerForm.getAttribute('action') || '' : '';
        const ownerFormId = ownerForm ? ownerForm.getAttribute('id') || '' : '';
        const ownerFormName = ownerForm ? ownerForm.getAttribute('name') || '' : '';
        const value = el.getAttribute('value');
        if (id) return '#' + cssEscape(id);
        if (name && type) return `${tag}[name="${cssEscape(name)}"][type="${cssEscape(type)}"]`;
        if (name) return `${tag}[name="${cssEscape(name)}"]`;
        if (href) return `${tag}[href="${cssEscape(href)}"]`;
        if (formAction) return `${tag}[formaction="${cssEscape(formAction)}"]`;
        if (action) return `${tag}[action="${cssEscape(action)}"]`;
        if (onclick) return `${tag}[onclick="${cssEscape(onclick)}"]`;
        if (value) return `${tag}[value="${cssEscape(value)}"]`;
        return tag;
      };
      return Array.from(document.querySelectorAll('a,button,input,select,textarea,form,[onclick],[formaction]'))
        .filter(el => {
          const style = window.getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          return style.visibility !== 'hidden'
            && style.display !== 'none'
            && rect.width > 0
            && rect.height > 0;
        })
        .slice(0, 500)
        .map(el => ({
          selector: selectorFor(el),
          tag: el.tagName.toLowerCase(),
          type: el.getAttribute('type') || '',
          text: (el.innerText || el.value || el.getAttribute('title') || '').trim().slice(0, 120),
          href: el.getAttribute('href') || '',
          onclick: el.getAttribute('onclick') || '',
          action: el.getAttribute('action') || '',
          formAction: el.getAttribute('formaction') || '',
          ownerFormAction: (el.form || el.closest('form')) ? ((el.form || el.closest('form')).getAttribute('action') || '') : '',
          ownerFormId: (el.form || el.closest('form')) ? ((el.form || el.closest('form')).getAttribute('id') || '') : '',
          ownerFormName: (el.form || el.closest('form')) ? ((el.form || el.closest('form')).getAttribute('name') || '') : '',
          name: el.getAttribute('name') || '',
          id: el.getAttribute('id') || '',
          value: el.getAttribute('value') || '',
          title: el.getAttribute('title') || '',
          ariaLabel: el.getAttribute('aria-label') || ''
        }));
    }
    """
    controls: List[Dict[str, Any]] = []
    for frame_index, frame in enumerate(page.frames):
        try:
            frame_controls = frame.evaluate(script)
        except PlaywrightError:
            continue
        for control in frame_controls:
            control["frame_index"] = frame_index
            control["frame_url"] = frame.url
            controls.append(control)
        if len(controls) >= limit:
            break
    return controls[:limit]


def _is_submit_control(control: Dict[str, Any]) -> bool:
    tag = str(control.get("tag") or "").lower()
    control_type = str(control.get("type") or "").lower()
    if tag == "button":
        return True
    if tag == "input" and control_type in {"submit", "button", "image"}:
        return True
    return False


def _control_search_text(control: Dict[str, Any]) -> str:
    keys = [
        "selector",
        "href",
        "onclick",
        "action",
        "formAction",
        "text",
        "value",
        "title",
        "ariaLabel",
        "name",
        "id",
    ]
    if _is_submit_control(control):
        keys.extend(["ownerFormAction", "ownerFormId", "ownerFormName"])
    return " ".join(
        str(control.get(key) or "")
        for key in keys
    ).lower()


def _candidate_selectors_from_control(control: Dict[str, Any], needle: str) -> List[str]:
    selectors = []
    selector = str(control.get("selector") or "")
    tag = str(control.get("tag") or "")
    if tag == "form":
        return []
    if selector:
        selectors.append(selector)
    if control.get("href"):
        selectors.append(f"{tag}[href*={_css_string(needle)}]")
    if control.get("onclick"):
        selectors.append(f"{tag}[onclick*={_css_string(needle)}]")
    if control.get("action"):
        selectors.append(f"{tag}[action*={_css_string(needle)}]")
    if control.get("formAction"):
        selectors.append(f"{tag}[formaction*={_css_string(needle)}]")
    if control.get("ownerFormAction") and _is_submit_control(control):
        control_type = str(control.get("type") or "").lower()
        if control.get("id"):
            selectors.append(f"[id={_css_string(control['id'])}]")
        if control.get("name"):
            selectors.append(f"{tag}[name={_css_string(control['name'])}]")
        if control_type:
            selectors.append(f"{tag}[type={_css_string(control_type)}]")
        selectors.append(f"{tag}")
    if control.get("name"):
        selectors.append(f"{tag}[name={_css_string(control['name'])}]")
    if control.get("id"):
        selectors.append(f"[id={_css_string(control['id'])}]")
    return [item for item in selectors if item]


def _hidden_action_reveal_plan(frame, action: str) -> Optional[Dict[str, str]]:
    script = """
    (needles) => {
      const needlePairs = needles
        .map(value => ({ raw: String(value || ''), lower: String(value || '').toLowerCase() }))
        .filter(item => item.lower);
      if (!needlePairs.length) return null;

      const cssEscape = window.CSS && window.CSS.escape
        ? window.CSS.escape.bind(window.CSS)
        : value => String(value).replace(/[^a-zA-Z0-9_-]/g, ch => '\\\\' + ch);
      const cssString = value => String(value || '').replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"');
      const allControls = Array.from(document.querySelectorAll('a,button,input,[onclick],[formaction]'));

      const textFor = el => [
        el.getAttribute('href') || '',
        el.getAttribute('onclick') || '',
        el.getAttribute('action') || '',
        el.getAttribute('formaction') || '',
        el.textContent || '',
        el.getAttribute('value') || '',
        el.getAttribute('title') || '',
        el.getAttribute('aria-label') || '',
        el.getAttribute('name') || '',
        el.getAttribute('id') || '',
      ].join(' ').toLowerCase();

      const matchingNeedle = el => {
        const haystack = textFor(el);
        const pair = needlePairs.find(item => haystack.includes(item.lower));
        return pair ? pair.raw : '';
      };

      const isVisible = el => {
        if (!el || !el.getBoundingClientRect) return false;
        let current = el;
        while (current && current.nodeType === 1) {
          const style = window.getComputedStyle(current);
          if (style.display === 'none' || style.visibility === 'hidden') return false;
          current = current.parentElement;
        }
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      };

      const hiddenAncestors = el => {
        const ancestors = [];
        let current = el;
        while (current && current.nodeType === 1 && current !== document.documentElement) {
          const style = window.getComputedStyle(current);
          const rect = current.getBoundingClientRect();
          if (style.display === 'none' || style.visibility === 'hidden' || rect.width <= 0 || rect.height <= 0) {
            ancestors.push(current);
          }
          current = current.parentElement;
        }
        return ancestors;
      };

      const selectorFor = (el, preferredNeedle = '') => {
        const tag = el.tagName.toLowerCase();
        const id = el.getAttribute('id');
        const name = el.getAttribute('name');
        const type = el.getAttribute('type');
        const onclick = el.getAttribute('onclick') || '';
        const href = el.getAttribute('href') || '';
        const formAction = el.getAttribute('formaction') || '';
        const value = el.getAttribute('value') || '';

        if (id) return '#' + cssEscape(id);
        const preferredLower = String(preferredNeedle || '').toLowerCase();
        if (preferredNeedle && onclick.toLowerCase().includes(preferredLower)) {
          return `${tag}[onclick*="${cssString(preferredNeedle)}"]`;
        }
        if (preferredNeedle && href.toLowerCase().includes(preferredLower)) {
          return `${tag}[href*="${cssString(preferredNeedle)}"]`;
        }
        if (preferredNeedle && formAction.toLowerCase().includes(preferredLower)) {
          return `${tag}[formaction*="${cssString(preferredNeedle)}"]`;
        }
        if (onclick) return `${tag}[onclick="${cssString(onclick)}"]`;
        if (href && href !== '#') return `${tag}[href="${cssString(href)}"]`;
        if (name && type) return `${tag}[name="${cssString(name)}"][type="${cssString(type)}"]`;
        if (name) return `${tag}[name="${cssString(name)}"]`;
        if (value) return `${tag}[value="${cssString(value)}"]`;
        return tag;
      };

      for (const target of allControls) {
        const needle = matchingNeedle(target);
        if (!needle || isVisible(target)) continue;

        const ancestors = hiddenAncestors(target);
        const hiddenContainer =
          ancestors.find(item => item !== target && item.id) ||
          ancestors.find(item => item.id) ||
          ancestors[ancestors.length - 1] ||
          null;
        const hiddenId = hiddenContainer ? hiddenContainer.getAttribute('id') || '' : '';
        if (!hiddenId) continue;

        const reveal = allControls.find(el => {
          if (el === target || !isVisible(el)) return false;
          const onclick = (el.getAttribute('onclick') || '').toLowerCase();
          const href = (el.getAttribute('href') || '').toLowerCase();
          const haystack = `${onclick} ${href}`;
          return haystack.includes(`#${hiddenId.toLowerCase()}`) || haystack.includes(hiddenId.toLowerCase());
        });
        if (!reveal) continue;

        return {
          targetSelector: selectorFor(target, needle),
          revealSelector: selectorFor(reveal, hiddenId),
          hiddenContainerId: hiddenId,
          targetText: (target.textContent || target.getAttribute('value') || '').trim().slice(0, 80),
          revealText: (reveal.textContent || reveal.getAttribute('value') || reveal.getAttribute('title') || '').trim().slice(0, 80),
        };
      }
      return null;
    }
    """
    try:
        plan = frame.evaluate(script, _action_needles(action))
    except PlaywrightError:
        return None
    if not isinstance(plan, dict):
        return None
    if not plan.get("targetSelector") or not plan.get("revealSelector"):
        return None
    return {str(key): str(value or "") for key, value in plan.items()}


def _reveal_hidden_action_locator(page: Page, action: str, timeout: int = 1500) -> Optional[str]:
    for frame in page.frames:
        plan = _hidden_action_reveal_plan(frame, action)
        if not plan:
            continue

        try:
            reveal_locator = frame.locator(plan["revealSelector"]).first
            reveal_locator.wait_for(state="visible", timeout=timeout)
            reveal_locator.click(timeout=timeout)
        except (PlaywrightTimeoutError, PlaywrightError):
            continue

        deadline = time.monotonic() + max(timeout, 500) / 1000
        while time.monotonic() < deadline:
            try:
                target_locator = frame.locator(plan["targetSelector"]).first
                if target_locator.count() and target_locator.is_visible(timeout=500):
                    return plan["targetSelector"]
            except (PlaywrightTimeoutError, PlaywrightError):
                pass
            try:
                page.wait_for_timeout(200)
            except (PlaywrightTimeoutError, PlaywrightError):
                break
    return None


def _manual_checkpoint(
    page: Page,
    *,
    route_id: str,
    index: int,
    action: str,
    capture_dir: Path,
    reason: str,
    upload_file: Optional[str] = None,
) -> Dict[str, Any]:
    before = _capture_state(page, capture_dir, f"{_safe_id(route_id)}_{index:02d}_manual_before")
    recorder = _install_manual_recorder(page, route_id=route_id, index=index)
    while True:
        print()
        print("[路径建图] 自动路径验证被阻塞")
        print(f"  路径ID: {route_id}")
        print(f"  步骤:   {index}")
        print(f"  Action: {action}")
        print(f"  原因:   {reason}")
        print("  请查看浏览器当前页面后选择：")
        print("    [Enter/r] 重新尝试自动识别/执行当前 action")
        print("    [m] 我已手工完成当前 action，继续验证下一步")
        print("    [s] 这条路径不可达")
        print("    [q] 中止路径建图")
        print("  请选择 [Enter/r/m/s/q]:")
        raw = input("> ").strip().lower()
        if raw in {"", "r", "retry"}:
            return {
                "status": "RETRY",
                "manual": False,
                "reason": "用户要求重新尝试自动识别",
                "state_before": before,
                "visible_controls": _visible_controls(page),
            }
        if raw == "q":
            raise InterruptedError("用户中止路径建图")
        if raw == "s":
            return {
                "status": "UNREACHABLE_ROUTE",
                "manual": True,
                "reason": f"用户判定该路径不可达，阻塞 action: {action}",
                "state_before": before,
                "visible_controls": _visible_controls(page),
            }
        if raw == "m":
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
            except (PlaywrightTimeoutError, PlaywrightError):
                pass
            after = _capture_state(page, capture_dir, f"{_safe_id(route_id)}_{index:02d}_manual_after")
            manual_events = list(recorder.get("events") or [])
            manual_replay = _manual_replay_from_events(manual_events, upload_file=upload_file)
            print(f"  已记录人工事件 {len(manual_events)} 个，可回放动作 {len(manual_replay)} 个。")
            return {
                "status": "PASS",
                "manual": True,
                "reason": "人工确认当前 action 已手工完成",
                "state_before": before,
                "state": after,
                "visible_controls": _visible_controls(page),
                "manual_events": manual_events,
                "manual_replay": manual_replay,
            }
        print("  输入无效，请选择 Enter/r、m、s 或 q。")


def _manual_step_fields(manual_result: Dict[str, Any], *, replay_mode: str) -> Dict[str, Any]:
    return {
        "manual_events": manual_result.get("manual_events") or [],
        "manual_replay": manual_result.get("manual_replay") or [],
        "manual_replay_mode": replay_mode,
    }


def find_runtime_action_locator(page: Page, action: str, timeout: int = 1500) -> Optional[str]:
    for needle in _action_needles(action):
        escaped = needle.replace("\\", "\\\\").replace("'", "\\'")
        for template in ACTION_CONTROL_SELECTORS:
            selector = template.format(needle=escaped)
            for frame in page.frames:
                try:
                    locator = frame.locator(selector).first
                    if locator.count() and locator.is_visible(timeout=timeout):
                        return selector
                except (PlaywrightTimeoutError, PlaywrightError):
                    continue

    controls = _runtime_controls(page)
    for needle in _action_needles(action):
        needle_lower = needle.lower()
        for control in controls:
            if needle_lower not in _control_search_text(control):
                continue
            for selector in _candidate_selectors_from_control(control, needle):
                for frame in page.frames:
                    try:
                        locator = frame.locator(selector).first
                        if locator.count() and locator.is_visible(timeout=timeout):
                            return selector
                    except (PlaywrightTimeoutError, PlaywrightError):
                        continue
    return _reveal_hidden_action_locator(page, action, timeout=timeout)


def _wait_for_action_locator(page: Page, action: str, timeout: int = 5000) -> Optional[str]:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(timeout, 3000))
    except (PlaywrightTimeoutError, PlaywrightError):
        pass

    deadline = time.monotonic() + max(timeout, 500) / 1000
    while time.monotonic() < deadline:
        locator = find_runtime_action_locator(page, action, timeout=500)
        if locator:
            return locator
        try:
            page.wait_for_timeout(250)
        except (PlaywrightTimeoutError, PlaywrightError):
            break
    return None


def _wait_for_action_ready(page: Page, action: str, timeout: int = 5000) -> Dict[str, Optional[str]]:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(timeout, 3000))
    except (PlaywrightTimeoutError, PlaywrightError):
        pass

    deadline = time.monotonic() + max(timeout, 500) / 1000
    while time.monotonic() < deadline:
        reached_url = _find_reached_action_url(page, action)
        if reached_url:
            return {"reached_url": reached_url, "locator": None}

        locator = find_runtime_action_locator(page, action, timeout=500)
        if locator:
            return {"reached_url": None, "locator": locator}

        try:
            page.wait_for_timeout(250)
        except (PlaywrightTimeoutError, PlaywrightError):
            break
    return {"reached_url": None, "locator": None}


def verify_candidate_route(
    page: Page,
    route: Dict[str, Any],
    *,
    capture_dir: Path,
    browser_name: str = "chrome",
    timeout: int = 15000,
    manual_data: bool = False,
    upload_file: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute one candidate route from the current logged-in entry state.

    The caller is responsible for resetting the browser back to the entry state
    before calling this for the next route.

    manual_data=True 时，自动验证失败会进入人工判断点；适用于需要
    业务检索条件、文件上传或数据选择后才出现下一步入口的路径。
    """
    capture_dir.mkdir(parents=True, exist_ok=True)
    route_id = route.get("route_id") or _route_signature(route)
    result: Dict[str, Any] = {
        "route_id": route_id,
        "target_page": route.get("target_page"),
        "target_page_name": route.get("target_page_name"),
        "source_route": route,
        "steps": [],
        "status": "verified",
    }

    action_nodes = list(_iter_action_nodes(route))

    for index, action_node in enumerate(action_nodes, start=1):
        action = action_node["name"]
        next_action = action_nodes[index]["name"] if index < len(action_nodes) else None
        step_id = f"{_safe_id(route_id)}_{index:02d}_{_safe_id(action)}"

        while True:
            reached_url = _find_reached_action_url(page, action)
            if reached_url:
                result["steps"].append(
                    {
                        "index": index,
                        "action": action,
                        "locator": None,
                        "status": "PASS",
                        "reached": True,
                        "reason": "action 已在当前页面或 frame 中加载",
                        "url": reached_url,
                    }
                )
                break

            locator = find_runtime_action_locator(page, action)
            if not locator:
                reason = f"当前页面没有找到可触发该 action 的可见控件: {action}"
                if not manual_data:
                    result["status"] = "UNREACHABLE_ROUTE"
                    result["blocked_at"] = index
                    result["reason"] = reason
                    result["state"] = _capture_state(page, capture_dir, f"{step_id}_unreachable")
                    result["visible_controls"] = _visible_controls(page)
                    return result

                manual_pages_before = _pages_for_context(page)
                manual_result = _manual_checkpoint(
                    page,
                    route_id=str(route_id),
                    index=index,
                    action=action,
                    capture_dir=capture_dir,
                    reason=reason,
                    upload_file=upload_file,
                )
                if manual_result.get("status") == "RETRY":
                    continue
                manual_takeover_page = None
                if manual_result.get("status") == "PASS":
                    manual_takeover_page = _takeover_page_after_action(page, manual_pages_before, {}, timeout)
                    if manual_takeover_page is not None:
                        page = manual_takeover_page
                result["steps"].append(
                    {
                        "index": index,
                        "action": action,
                        "locator": None,
                        "status": manual_result.get("status"),
                        "manual": True,
                        "reason": manual_result.get("reason"),
                        "url": (manual_result.get("state") or {}).get("url"),
                        "popup_taken_over": manual_takeover_page is not None,
                        "active_page_url": page.url if not _page_is_closed(page) else None,
                        **_manual_step_fields(manual_result, replay_mode="current_action"),
                    }
                )
                if manual_result.get("status") == "PASS":
                    if next_action:
                        while True:
                            next_ready = _wait_for_action_ready(page, next_action, timeout=min(timeout, 5000))
                            if next_ready.get("locator") or next_ready.get("reached_url"):
                                result["steps"][-1]["next_action"] = next_action
                                if next_ready.get("locator"):
                                    result["steps"][-1]["next_action_locator"] = next_ready.get("locator")
                                if next_ready.get("reached_url"):
                                    result["steps"][-1]["next_action_reached_url"] = next_ready.get("reached_url")
                                break

                            reason = (
                                f"人工确认当前 action: {action} 后，页面仍没有出现下一步 action 的可见控件: {next_action}。"
                                "如果只是补了输入条件，请按 Enter/r 让工具重新识别；"
                                "只有已经手工点击并进入下一步页面时才选择 m。"
                            )
                            manual_next_pages_before = _pages_for_context(page)
                            manual_next_result = _manual_checkpoint(
                                page,
                                route_id=str(route_id),
                                index=index + 1,
                                action=next_action,
                                capture_dir=capture_dir,
                                reason=reason,
                                upload_file=upload_file,
                            )
                            if manual_next_result.get("status") == "RETRY":
                                continue
                            if manual_next_result.get("status") == "PASS":
                                manual_takeover_page = _takeover_page_after_action(page, manual_next_pages_before, {}, timeout)
                                if manual_takeover_page is not None:
                                    page = manual_takeover_page
                                result["steps"][-1]["manual_next_state_prepared"] = True
                                result["steps"][-1]["manual"] = True
                                result["steps"][-1]["reason"] = manual_next_result.get("reason")
                                result["steps"][-1]["popup_taken_over"] = manual_takeover_page is not None
                                result["steps"][-1]["active_page_url"] = page.url if not _page_is_closed(page) else None
                                result["steps"][-1].update(_manual_step_fields(manual_next_result, replay_mode="after_action"))
                                break

                            result["status"] = "UNREACHABLE_ROUTE"
                            result["blocked_at"] = index + 1
                            result["reason"] = manual_next_result.get("reason")
                            result["state"] = manual_next_result.get("state") or manual_next_result.get("state_before")
                            result["visible_controls"] = manual_next_result.get("visible_controls") or _visible_controls(page)
                            return result
                    break

                result["status"] = "UNREACHABLE_ROUTE"
                result["blocked_at"] = index
                result["reason"] = manual_result.get("reason")
                result["state"] = manual_result.get("state") or manual_result.get("state_before")
                result["visible_controls"] = manual_result.get("visible_controls") or _visible_controls(page)
                return result

            pages_before = _pages_for_context(page)
            action_context = _action_context_for_locator(page, action, locator)
            action_result = execute_action(
                page,
                "click",
                locator,
                browser_name=browser_name,
                capture_dir=capture_dir,
                test_id=step_id,
                timeout=timeout,
                action_context=action_context,
            )
            takeover_page = _takeover_page_after_action(page, pages_before, action_result, timeout)
            if takeover_page is not None:
                page = takeover_page
            result["steps"].append(
                {
                    "index": index,
                    "action": action,
                    "locator": locator,
                    "status": action_result.get("status"),
                    "reason": action_result.get("reason"),
                    "url": (action_result.get("state") or {}).get("url"),
                    "popup_taken_over": takeover_page is not None,
                    "active_page_url": page.url if not _page_is_closed(page) else None,
                }
            )
            if action_result.get("status") == "PASS":
                if next_action:
                    while True:
                        next_ready = _wait_for_action_ready(page, next_action, timeout=min(timeout, 5000))
                        if next_ready.get("locator") or next_ready.get("reached_url"):
                            result["steps"][-1]["next_action"] = next_action
                            if next_ready.get("locator"):
                                result["steps"][-1]["next_action_locator"] = next_ready.get("locator")
                            if next_ready.get("reached_url"):
                                result["steps"][-1]["next_action_reached_url"] = next_ready.get("reached_url")
                            break

                        reason = (
                            f"已点击当前 action: {action}，但页面没有出现下一步 action 的可见控件: {next_action}。"
                            "当前页面可能仍停留在上一页、登录失败、数据不足，或静态路径不可用。"
                        )
                        if not manual_data:
                            result["status"] = "UNREACHABLE_ROUTE"
                            result["blocked_at"] = index + 1
                            result["reason"] = reason
                            result["state"] = _capture_state(page, capture_dir, f"{step_id}_next_missing")
                            result["visible_controls"] = _visible_controls(page)
                            return result

                        manual_pages_before = _pages_for_context(page)
                        manual_result = _manual_checkpoint(
                            page,
                            route_id=str(route_id),
                            index=index + 1,
                            action=next_action,
                            capture_dir=capture_dir,
                            reason=reason,
                            upload_file=upload_file,
                        )
                        if manual_result.get("status") == "RETRY":
                            continue
                        if manual_result.get("status") == "PASS":
                            manual_takeover_page = _takeover_page_after_action(page, manual_pages_before, {}, timeout)
                            if manual_takeover_page is not None:
                                page = manual_takeover_page
                            result["steps"][-1]["manual_next_state_prepared"] = True
                            result["steps"][-1]["manual"] = True
                            result["steps"][-1]["reason"] = manual_result.get("reason")
                            result["steps"][-1]["popup_taken_over"] = manual_takeover_page is not None
                            result["steps"][-1]["active_page_url"] = page.url if not _page_is_closed(page) else None
                            result["steps"][-1].update(_manual_step_fields(manual_result, replay_mode="after_action"))
                            break

                        result["status"] = "UNREACHABLE_ROUTE"
                        result["blocked_at"] = index + 1
                        result["reason"] = manual_result.get("reason")
                        result["state"] = manual_result.get("state") or manual_result.get("state_before")
                        result["visible_controls"] = manual_result.get("visible_controls") or _visible_controls(page)
                        return result
                break

            reason = action_result.get("reason") or f"Action 未完成: {action}"
            if manual_data:
                manual_pages_before = _pages_for_context(page)
                manual_result = _manual_checkpoint(
                    page,
                    route_id=str(route_id),
                    index=index,
                    action=action,
                    capture_dir=capture_dir,
                    reason=reason,
                    upload_file=upload_file,
                )
                if manual_result.get("status") == "RETRY":
                    result["steps"][-1]["manual_retry_requested"] = True
                    continue
                manual_takeover_page = None
                if manual_result.get("status") == "PASS":
                    manual_takeover_page = _takeover_page_after_action(page, manual_pages_before, {}, timeout)
                    if manual_takeover_page is not None:
                        page = manual_takeover_page
                result["steps"][-1].update(
                    {
                        "status": manual_result.get("status"),
                        "manual": True,
                        "reason": manual_result.get("reason"),
                        "url": (manual_result.get("state") or {}).get("url"),
                        "popup_taken_over": manual_takeover_page is not None,
                        "active_page_url": page.url if not _page_is_closed(page) else None,
                        **_manual_step_fields(manual_result, replay_mode="current_action"),
                    }
                )
                if manual_result.get("status") == "PASS":
                    break

            result["status"] = "UNREACHABLE_ROUTE"
            result["blocked_at"] = index
            result["reason"] = reason
            result["state"] = action_result.get("state")
            result["visible_controls"] = _visible_controls(page)
            return result

    result["state"] = _capture_state(page, capture_dir, f"{_safe_id(route_id)}_final")
    result["visible_controls"] = _visible_controls(page)
    result["page_state_id"] = _page_state_id(result)
    result["manual_steps"] = sum(1 for step in result["steps"] if step.get("manual"))
    if result["manual_steps"]:
        result["status"] = "manual_verified"
    return result


def _page_state_id(verified: Dict[str, Any]) -> str:
    controls = verified.get("visible_controls") or []
    raw = json.dumps(
        {
            "target": verified.get("target_page_name"),
            "route": verified.get("route_id"),
            "controls": controls,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def write_usable_route_map(results: List[Dict[str, Any]], output_path: Path) -> Path:
    payload = {
        "schema": "moonlight.usable_route_map.v1",
        "verified": [item for item in results if item.get("status") in {"verified", "manual_verified"}],
        "manual_verified": [item for item in results if item.get("status") == "manual_verified"],
        "unreachable": [item for item in results if item.get("status") not in {"verified", "manual_verified"}],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path
