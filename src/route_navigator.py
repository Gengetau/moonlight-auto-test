import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from playwright.sync_api import Error as PlaywrightError, Page

from src.action_executor import _capture_state, execute_action
from src.route_runtime_verifier import (
    _action_context_for_locator,
    _find_reached_action_url,
    _iter_action_nodes,
    _page_is_closed,
    _pages_for_context,
    _safe_id,
    _takeover_page_after_action,
    _visible_controls,
    _wait_for_action_ready,
    find_runtime_action_locator,
)


class RouteMapCatalog:
    """Load verified route maps and select executable routes by target page."""

    def __init__(self, paths: Optional[Iterable[Path]] = None) -> None:
        self.paths = list(paths or self._discover_default_paths())
        self.routes = self._load_routes(self.paths)

    @staticmethod
    def _discover_default_paths() -> List[Path]:
        valid_dir = Path("generated/valid")
        if not valid_dir.exists():
            return []
        return sorted(valid_dir.rglob("usable_route_map*.json"))

    @staticmethod
    def _target_name(value: Any) -> str:
        return Path(str(value or "").replace("\\", "/")).name.lower()

    @classmethod
    def _load_routes(cls, paths: Iterable[Path]) -> List[Dict[str, Any]]:
        routes: List[Dict[str, Any]] = []
        seen = set()
        for path in paths:
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            for route in list(payload.get("verified") or []) + list(payload.get("manual_verified") or []):
                route_id = route.get("route_id") or json.dumps(route.get("source_route") or {}, sort_keys=True, default=str)
                seen_key = (route_id, route.get("run_side"), str(path))
                if seen_key in seen:
                    continue
                seen.add(seen_key)
                loaded = dict(route)
                loaded["route_map_path"] = str(path)
                routes.append(loaded)
        return routes

    def find_for_target(self, target_page: Any, *, side: Optional[str] = None) -> Optional[Dict[str, Any]]:
        target_name = self._target_name(target_page)
        if not target_name:
            return None

        candidates = [
            route
            for route in self.routes
            if self._target_name(route.get("target_page_name") or route.get("target_page")) == target_name
            or self._target_name((route.get("source_route") or {}).get("target_page_name")) == target_name
            or self._target_name((route.get("source_route") or {}).get("target_page")) == target_name
        ]
        if not candidates:
            return None

        def rank(route: Dict[str, Any]) -> Tuple[int, int, int]:
            side_rank = 0 if side and route.get("run_side") == side else 1
            status_rank = 0 if route.get("status") == "verified" else 1
            length = int((route.get("source_route") or {}).get("length") or len(route.get("steps") or []) or 999)
            return side_rank, status_rank, length

        return sorted(candidates, key=rank)[0]


def _route_steps_by_index(route: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    steps: Dict[int, Dict[str, Any]] = {}
    for step in route.get("steps") or []:
        try:
            steps[int(step.get("index"))] = step
        except (TypeError, ValueError):
            continue
    return steps


def _manual_replay_action_type(replay: Dict[str, Any]) -> str:
    action_type = str(replay.get("action_type") or "click").lower()
    selector = str(replay.get("selector") or "").lower()
    replay_type = str(replay.get("type") or "").lower()
    if action_type == "click" and (replay_type in {"checkbox", "radio"} or "type=\"checkbox\"" in selector or "type='checkbox'" in selector):
        return "check"
    return action_type


def _mark_manual_replay_target(
    page: Page,
    replay: Dict[str, Any],
    *,
    replay_index: int,
    test_id: str,
) -> Tuple[str, Dict[str, Any]]:
    selector = str(replay.get("selector") or "")
    if not selector:
        return selector, {"marked": False, "reason": "empty selector"}

    value = str(replay.get("value") or "")
    text = str(replay.get("text") or "")
    action_type = _manual_replay_action_type(replay)
    marker = f"ml-{_safe_id(test_id)}-{replay_index}-{int(time.time() * 1000)}"
    marker_selector = f'[data-moonlight-manual-replay-id="{marker}"]'
    script = """
    ({ selector, marker, value, text, actionType, allowFallback }) => {
      const normalize = raw => String(raw || '').replace(/\\s+/g, ' ').trim();
      const wantedValue = normalize(value);
      const wantedText = normalize(text);
      const action = String(actionType || '').toLowerCase();
      const wantsChoice = ['check', 'uncheck'].includes(action);
      const wantsFill = ['fill', 'clear', 'set_value'].includes(action);
      const wantsUpload = ['upload', 'file', 'set_input_files'].includes(action);
      const isVisible = el => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width >= 0 && rect.height >= 0;
      };
      const isEditable = (el, tag, type) => {
        if (tag === 'textarea') return true;
        if (tag === 'input') return !['button', 'submit', 'reset', 'image', 'hidden', 'checkbox', 'radio', 'file'].includes(type);
        if (el.isContentEditable) return true;
        const role = (el.getAttribute('role') || '').toLowerCase();
        return ['textbox', 'searchbox', 'combobox'].includes(role);
      };
      const isCompatible = (el, tag, type) => {
        if (wantsChoice) return tag === 'input' && ['checkbox', 'radio'].includes(type);
        if (wantsUpload) return tag === 'input' && type === 'file';
        if (wantsFill) return isEditable(el, tag, type);
        return true;
      };
      const scoreElement = (el, fromSelector) => {
        const tag = (el.tagName || '').toLowerCase();
        const type = (el.getAttribute('type') || '').toLowerCase();
        if (!isCompatible(el, tag, type)) return null;
        const elementValue = normalize(el.value || el.getAttribute('value') || '');
        const elementText = normalize(el.innerText || el.textContent || el.getAttribute('title') || el.getAttribute('aria-label') || '');
        const valueExact = !!wantedValue && elementValue === wantedValue;
        const valuePartial = !!wantedValue && !valueExact && elementValue.includes(wantedValue);
        const textExact = !!wantedText && elementText === wantedText;
        const textPartial = !!wantedText && !textExact && elementText.includes(wantedText);
        if (!fromSelector && (wantedValue || wantedText) && !valueExact && !valuePartial && !textExact && !textPartial) {
          return null;
        }
        if (!fromSelector && !wantedValue && !wantedText) return null;
        let score = fromSelector ? 1 : 0;
        if (wantsChoice && tag === 'input' && ['checkbox', 'radio'].includes(type)) score += 80;
        if (valueExact) score += 240;
        else if (valuePartial) score += 90;
        if (textExact) score += 220;
        else if (textPartial) score += 80;
        if (isVisible(el)) score += 10;
        return { score, tag, type, value: elementValue, text: elementText.slice(0, 120), visible: isVisible(el) };
      };

      let selectorElements = [];
      try { selectorElements = Array.from(document.querySelectorAll(selector)); } catch (_) {}
      const fallbackElements = allowFallback && (wantedText || wantedValue || wantsChoice)
        ? Array.from(document.querySelectorAll('input,select,textarea,button,a,td,span,div,[onclick]'))
        : [];
      const candidates = selectorElements.map(el => ({ el, fromSelector: true }))
        .concat(selectorElements.length ? [] : fallbackElements.map(el => ({ el, fromSelector: false })));

      let best = null;
      for (const candidate of candidates) {
        const scored = scoreElement(candidate.el, candidate.fromSelector);
        if (!scored) continue;
        if (!best || scored.score > best.score) best = { ...scored, el: candidate.el, fromSelector: candidate.fromSelector };
      }
      if (!best || best.score <= 0) {
        return { marked: false, selector_count: selectorElements.length, reason: 'no scored target' };
      }
      best.el.setAttribute('data-moonlight-manual-replay-id', marker);
      return {
        marked: true,
        selector_count: selectorElements.length,
        score: best.score,
        from_selector: best.fromSelector,
        tag: best.tag,
        type: best.type,
        value: best.value,
        text: best.text,
        visible: best.visible,
      };
    }
    """

    diagnostics: List[Dict[str, Any]] = []

    def _try_mark(allow_fallback: bool) -> Tuple[Optional[str], Optional[Dict[str, Any]], bool]:
        saw_original_selector = False
        for frame in page.frames:
            try:
                marked = frame.evaluate(
                    script,
                    {
                        "selector": selector,
                        "marker": marker,
                        "value": value,
                        "text": text,
                        "actionType": action_type,
                        "allowFallback": allow_fallback,
                    },
                )
            except PlaywrightError as exc:
                diagnostics.append({"frame_url": getattr(frame, "url", ""), "error": str(exc), "allow_fallback": allow_fallback})
                continue
            if marked:
                marked["frame_url"] = getattr(frame, "url", "")
                marked["allow_fallback"] = allow_fallback
                if int(marked.get("selector_count") or 0) > 0:
                    saw_original_selector = True
            if marked and marked.get("marked"):
                marked["original_selector"] = selector
                marked["resolved_selector"] = marker_selector
                return marker_selector, marked, saw_original_selector
            if marked:
                diagnostics.append(marked)
        return None, None, saw_original_selector

    marked_selector, marked_state, saw_original_selector = _try_mark(False)
    if marked_selector and marked_state:
        return marked_selector, marked_state
    if saw_original_selector:
        return selector, {
            "marked": False,
            "original_selector": selector,
            "reason": "original selector exists but no compatible replay target was selected",
            "diagnostics": diagnostics[:8],
        }

    marked_selector, marked_state, _ = _try_mark(True)
    if marked_selector and marked_state:
        return marked_selector, marked_state

    return selector, {"marked": False, "original_selector": selector, "diagnostics": diagnostics[:8]}


def _selector_exists_in_any_frame(page: Page, selector: str) -> bool:
    if not selector:
        return False
    for frame in page.frames:
        try:
            if frame.locator(selector).count() > 0:
                return True
        except PlaywrightError:
            continue
    return False


def _manual_replay_start_offset(page: Page, replay_steps: List[Dict[str, Any]]) -> int:
    for index, replay in enumerate(replay_steps):
        selector = str(replay.get("selector") or "")
        if _selector_exists_in_any_frame(page, selector):
            return index
    return 0


def _execute_manual_replay(
    page: Page,
    replay_steps: List[Dict[str, Any]],
    *,
    capture_dir: Path,
    test_id: str,
    browser_name: str,
    timeout: int,
    upload_file: Optional[str] = None,
) -> Tuple[Page, Dict[str, Any]]:
    result: Dict[str, Any] = {"status": "PASS", "steps": []}
    for replay_index, replay in enumerate(replay_steps, start=1):
        recorded_selector = str(replay.get("selector") or "")
        selector = recorded_selector
        action_type = _manual_replay_action_type(replay)
        value = replay.get("value") or None
        if str(action_type).lower() in {"upload", "file", "set_input_files"} and upload_file:
            value = upload_file
        if not selector:
            result.update({"status": "BLOCKED", "reason": "Recorded manual replay step has no selector"})
            return page, result

        selector, selector_resolution = _mark_manual_replay_target(
            page,
            replay,
            replay_index=replay_index,
            test_id=test_id,
        )
        pages_before = _pages_for_context(page)
        action_result = execute_action(
            page,
            action_type,
            selector,
            value,
            browser_name=browser_name,
            capture_dir=capture_dir,
            test_id=f"{test_id}_manual_{replay_index:02d}",
            timeout=timeout,
            action_context={
                "label": f"manual replay {replay_index}",
                "action_type": action_type,
                "locator": selector,
                "manual_replay": True,
                "recorded_selector": recorded_selector,
                "recorded_text": replay.get("text") or "",
                "recorded_value": replay.get("value") or "",
                "keep_popup": True,
            },
        )
        takeover_page = _takeover_page_after_action(page, pages_before, action_result, timeout)
        if takeover_page is not None:
            page = takeover_page
        result["steps"].append(
            {
                "index": replay_index,
                "action_type": action_type,
                "selector": selector,
                "recorded_selector": recorded_selector,
                "selector_resolution": selector_resolution,
                "status": action_result.get("status"),
                "reason": action_result.get("reason"),
                "popup_taken_over": takeover_page is not None,
                "active_page_url": page.url if not _page_is_closed(page) else None,
            }
        )
        if action_result.get("status") != "PASS":
            result.update(
                {
                    "status": "BLOCKED",
                    "reason": action_result.get("reason") or f"Recorded manual replay failed: {selector}",
                    "state": action_result.get("state"),
                }
            )
            return page, result
    return page, result


class RouteNavigator:
    """Replay a verified route from an environment entry page to a target page."""

    def __init__(
        self,
        catalog: RouteMapCatalog,
        *,
        timeout: int = 15000,
        browser_name: str = "chrome",
        upload_file: Optional[str] = None,
    ) -> None:
        self.catalog = catalog
        self.timeout = timeout
        self.browser_name = browser_name
        self.upload_file = upload_file

    def route_for(self, target_page: Any, *, side: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return self.catalog.find_for_target(target_page, side=side)

    def navigate(
        self,
        page: Page,
        *,
        entry_url: str,
        target_page: Any,
        capture_dir: Path,
        side: str,
    ) -> Tuple[Page, Dict[str, Any]]:
        route = self.route_for(target_page, side=side)
        if not route:
            return page, {
                "status": "BLOCKED",
                "strategy": "route_map",
                "reason": f"No verified route found for target page: {target_page}",
            }

        source_route = route.get("source_route") or route
        route_id = route.get("route_id") or source_route.get("route_id") or "route"
        route_capture_dir = capture_dir / f"{side}_route_{_safe_id(route_id)}"
        route_capture_dir.mkdir(parents=True, exist_ok=True)

        result: Dict[str, Any] = {
            "status": "PASS",
            "strategy": "route_map",
            "route_id": route_id,
            "route_map_path": route.get("route_map_path"),
            "target_page": route.get("target_page") or source_route.get("target_page"),
            "steps": [],
        }

        result["entry_url"] = entry_url
        try:
            current_url = "" if _page_is_closed(page) else str(page.url or "")
        except Exception:
            current_url = ""
        result["start_url"] = current_url

        if not current_url or current_url == "about:blank":
            try:
                page.goto(entry_url, wait_until="domcontentloaded", timeout=max(self.timeout, 60000))
                result["entry_opened"] = True
            except Exception as exc:
                result.update({"status": "BLOCKED", "reason": f"Failed to open route entry: {exc}"})
                result["state"] = _capture_state(page, route_capture_dir, "entry_blocked")
                return page, result
        else:
            result["entry_reused"] = True
            try:
                page.bring_to_front()
                page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout, 5000))
            except Exception:
                pass

        action_nodes = list(_iter_action_nodes(source_route))
        full_route_replay = list(route.get("manual_replay") or [])
        if not full_route_replay:
            for recorded_step in route.get("steps") or []:
                if recorded_step.get("manual_replay_mode") == "full_route":
                    full_route_replay = list(recorded_step.get("manual_replay") or [])
                    break

        if route.get("manual_route") or (full_route_replay and not action_nodes):
            if not full_route_replay:
                result.update(
                    {
                        "status": "BLOCKED",
                        "blocked_at": 1,
                        "reason": "Manual full-route map has no replayable events",
                        "state": _capture_state(page, route_capture_dir, f"{_safe_id(route_id)}_manual_route_empty"),
                        "visible_controls": _visible_controls(page),
                    }
                )
                return page, result

            manual_replay_start_offset = _manual_replay_start_offset(page, full_route_replay)
            replay_steps = full_route_replay[manual_replay_start_offset:] if manual_replay_start_offset else full_route_replay
            page, replay_result = _execute_manual_replay(
                page,
                replay_steps,
                capture_dir=route_capture_dir,
                test_id=f"{_safe_id(route_id)}_manual_full_route",
                browser_name=self.browser_name,
                timeout=self.timeout,
                upload_file=self.upload_file,
            )
            result["steps"].append(
                {
                    "index": 1,
                    "action": "__manual_full_route__",
                    "locator": None,
                    "status": replay_result.get("status"),
                    "manual_replay_used": True,
                    "manual_replay_mode": "full_route",
                    "manual_replay_start_offset": manual_replay_start_offset,
                    "manual_replay_steps": replay_result.get("steps") or [],
                    "reason": replay_result.get("reason"),
                    "active_page_url": page.url if not _page_is_closed(page) else None,
                }
            )
            if replay_result.get("status") != "PASS":
                result.update(
                    {
                        "status": "BLOCKED",
                        "blocked_at": 1,
                        "reason": replay_result.get("reason") or "Manual full-route replay failed",
                        "state": replay_result.get("state") or _capture_state(page, route_capture_dir, f"{_safe_id(route_id)}_manual_full_route_failed"),
                        "visible_controls": _visible_controls(page),
                    }
                )
                return page, result

            result["state"] = _capture_state(page, route_capture_dir, f"{_safe_id(route_id)}_final")
            result["visible_controls"] = _visible_controls(page)
            result["url"] = (result.get("state") or {}).get("url") or (page.url if not _page_is_closed(page) else None)
            return page, result

        recorded_steps = _route_steps_by_index(route)
        for index, action_node in enumerate(action_nodes, start=1):
            action = action_node["name"]
            next_action = action_nodes[index]["name"] if index < len(action_nodes) else None
            step_id = f"{_safe_id(route_id)}_{index:02d}_{_safe_id(action)}"
            recorded_step = recorded_steps.get(index) or {}
            manual_replay = list(recorded_step.get("manual_replay") or [])
            manual_replay_mode = str(recorded_step.get("manual_replay_mode") or "")

            reached_url = _find_reached_action_url(page, action)
            if reached_url:
                result["steps"].append(
                    {
                        "index": index,
                        "action": action,
                        "locator": None,
                        "status": "PASS",
                        "reached": True,
                        "url": reached_url,
                    }
                )
                continue

            if manual_replay and manual_replay_mode == "current_action":
                page, replay_result = _execute_manual_replay(
                    page,
                    manual_replay,
                    capture_dir=route_capture_dir,
                    test_id=step_id,
                    browser_name=self.browser_name,
                    timeout=self.timeout,
                    upload_file=self.upload_file,
                )
                step = {
                    "index": index,
                    "action": action,
                    "locator": None,
                    "status": replay_result.get("status"),
                    "manual_replay_used": True,
                    "manual_replay_steps": replay_result.get("steps") or [],
                    "reason": replay_result.get("reason"),
                    "active_page_url": page.url if not _page_is_closed(page) else None,
                }
                result["steps"].append(step)
                if replay_result.get("status") != "PASS":
                    result.update(
                        {
                            "status": "BLOCKED",
                            "blocked_at": index,
                            "reason": replay_result.get("reason") or f"Recorded manual replay failed for action: {action}",
                            "state": replay_result.get("state") or _capture_state(page, route_capture_dir, f"{step_id}_manual_replay_failed"),
                            "visible_controls": _visible_controls(page),
                        }
                    )
                    return page, result
                if next_action:
                    next_ready = _wait_for_action_ready(page, next_action, timeout=min(self.timeout, 5000))
                    if next_ready.get("locator"):
                        step["next_action"] = next_action
                        step["next_action_locator"] = next_ready.get("locator")
                    elif next_ready.get("reached_url"):
                        step["next_action"] = next_action
                        step["next_action_reached_url"] = next_ready.get("reached_url")
                    else:
                        result.update(
                            {
                                "status": "BLOCKED",
                                "blocked_at": index + 1,
                                "reason": f"Recorded manual replay did not expose next action after {action}: {next_action}",
                                "state": _capture_state(page, route_capture_dir, f"{step_id}_manual_replay_next_missing"),
                                "visible_controls": _visible_controls(page),
                            }
                        )
                        return page, result
                continue

            locator = find_runtime_action_locator(page, action)
            if not locator:
                reason = f"Route map step is not available on current page: {action}"
                result.update(
                    {
                        "status": "BLOCKED",
                        "blocked_at": index,
                        "reason": reason,
                        "state": _capture_state(page, route_capture_dir, f"{step_id}_missing"),
                        "visible_controls": _visible_controls(page),
                    }
                )
                return page, result

            pages_before = _pages_for_context(page)
            action_result = execute_action(
                page,
                "click",
                locator,
                browser_name=self.browser_name,
                capture_dir=route_capture_dir,
                test_id=step_id,
                timeout=self.timeout,
                action_context=_action_context_for_locator(page, action, locator),
            )
            takeover_page = _takeover_page_after_action(page, pages_before, action_result, self.timeout)
            if takeover_page is not None:
                page = takeover_page

            step = {
                "index": index,
                "action": action,
                "locator": locator,
                "status": action_result.get("status"),
                "reason": action_result.get("reason"),
                "url": (action_result.get("state") or {}).get("url"),
                "popup_taken_over": takeover_page is not None,
                "active_page_url": page.url if not _page_is_closed(page) else None,
            }
            result["steps"].append(step)

            if action_result.get("status") != "PASS":
                result.update(
                    {
                        "status": "BLOCKED",
                        "blocked_at": index,
                        "reason": action_result.get("reason") or f"Route action failed: {action}",
                        "state": action_result.get("state"),
                        "visible_controls": _visible_controls(page),
                    }
                )
                return page, result

            if manual_replay and manual_replay_mode == "after_action":
                page, replay_result = _execute_manual_replay(
                    page,
                    manual_replay,
                    capture_dir=route_capture_dir,
                    test_id=step_id,
                    browser_name=self.browser_name,
                    timeout=self.timeout,
                    upload_file=self.upload_file,
                )
                step["manual_replay_used"] = True
                step["manual_replay_steps"] = replay_result.get("steps") or []
                if replay_result.get("status") != "PASS":
                    result.update(
                        {
                            "status": "BLOCKED",
                            "blocked_at": index,
                            "reason": replay_result.get("reason") or f"Recorded manual replay failed after action: {action}",
                            "state": replay_result.get("state") or _capture_state(page, route_capture_dir, f"{step_id}_manual_replay_failed"),
                            "visible_controls": _visible_controls(page),
                        }
                    )
                    return page, result

            if next_action:
                next_ready = _wait_for_action_ready(page, next_action, timeout=min(self.timeout, 5000))
                if next_ready.get("locator"):
                    step["next_action"] = next_action
                    step["next_action_locator"] = next_ready.get("locator")
                elif next_ready.get("reached_url"):
                    step["next_action"] = next_action
                    step["next_action_reached_url"] = next_ready.get("reached_url")
                else:
                    result.update(
                        {
                            "status": "BLOCKED",
                            "blocked_at": index + 1,
                            "reason": f"Route did not expose next action after {action}: {next_action}",
                            "state": _capture_state(page, route_capture_dir, f"{step_id}_next_missing"),
                            "visible_controls": _visible_controls(page),
                        }
                    )
                    return page, result

        result["state"] = _capture_state(page, route_capture_dir, f"{_safe_id(route_id)}_final")
        result["visible_controls"] = _visible_controls(page)
        result["url"] = (result.get("state") or {}).get("url") or (page.url if not _page_is_closed(page) else None)
        return page, result
