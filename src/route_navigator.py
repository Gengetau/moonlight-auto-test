import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from playwright.sync_api import Page

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
        return sorted(valid_dir.glob("usable_route_map*.json"))

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
                if route_id in seen:
                    continue
                seen.add(route_id)
                loaded = dict(route)
                loaded["route_map_path"] = str(path)
                routes.append(loaded)
        return routes

    def find_for_target(self, target_page: Any) -> Optional[Dict[str, Any]]:
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

        def rank(route: Dict[str, Any]) -> Tuple[int, int]:
            status_rank = 0 if route.get("status") == "verified" else 1
            length = int((route.get("source_route") or {}).get("length") or len(route.get("steps") or []) or 999)
            return status_rank, length

        return sorted(candidates, key=rank)[0]


class RouteNavigator:
    """Replay a verified route from an environment entry page to a target page."""

    def __init__(self, catalog: RouteMapCatalog, *, timeout: int = 15000, browser_name: str = "chrome") -> None:
        self.catalog = catalog
        self.timeout = timeout
        self.browser_name = browser_name

    def route_for(self, target_page: Any) -> Optional[Dict[str, Any]]:
        return self.catalog.find_for_target(target_page)

    def navigate(
        self,
        page: Page,
        *,
        entry_url: str,
        target_page: Any,
        capture_dir: Path,
        side: str,
    ) -> Tuple[Page, Dict[str, Any]]:
        route = self.route_for(target_page)
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

        try:
            page.goto(entry_url, wait_until="domcontentloaded", timeout=max(self.timeout, 60000))
        except Exception as exc:
            result.update({"status": "BLOCKED", "reason": f"Failed to open route entry: {exc}"})
            result["state"] = _capture_state(page, route_capture_dir, "entry_blocked")
            return page, result

        action_nodes = list(_iter_action_nodes(source_route))
        for index, action_node in enumerate(action_nodes, start=1):
            action = action_node["name"]
            next_action = action_nodes[index]["name"] if index < len(action_nodes) else None
            step_id = f"{_safe_id(route_id)}_{index:02d}_{_safe_id(action)}"

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
