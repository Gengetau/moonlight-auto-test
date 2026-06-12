import html
import hashlib
import json
import os
import re
import shutil
import time
import glob
import fnmatch
from collections import Counter
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urljoin

from playwright.sync_api import Page, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from src.action_executor import _capture_state, _wait_for_semantic_ready, execute_action, infer_semantic_action
from src.assert_engine import compare_visual_screenshot
from src.browser_print import install_print_suppression
from src.config_parser import Config
from src.page_aliases import page_aliases
from src.route_navigator import RouteMapCatalog, RouteNavigator


NEGATIVE_CASE_TYPES = {
    "negative_js_error",
    "negative_network_abort",
    "negative_http_500",
    "negative_invalid_input",
    "negative_file_upload",
}


DOWNLOAD_CASE_TYPES = {"download", "download_template", "file_download"}
BROWSER_DIALOG_CASE_TYPES = {"browser_dialog", "dialog", "alert", "confirm", "prompt"}
PRINT_CASE_TYPES = {"print", "print_output", "print_dialog", "print_invocation"}
PDF_SAVE_CASE_TYPES = {"save_pdf", "pdf_save", "saved_pdf", "print_to_pdf"}
UUID_DOWNLOAD_NAME_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
CHILD_NAVIGATION_CASE_TYPES = {
    "child_navigation",
    "child_page",
    "child_route",
    "link_navigation",
    "open_child",
    "open_child_page",
    "open_subpage",
    "popup_navigation",
    "popup_or_navigation",
    "subpage_navigation",
}
NON_VISUAL_ACTION_TYPES = (
    DOWNLOAD_CASE_TYPES
    | BROWSER_DIALOG_CASE_TYPES
    | PRINT_CASE_TYPES
    | PDF_SAVE_CASE_TYPES
    | CHILD_NAVIGATION_CASE_TYPES
    | {"close_window"}
)


def _judge_compare_status(
    *,
    action_type: str,
    visual_status: Optional[str],
    url_match: bool,
    legacy_action_status: Optional[str] = None,
    new_action_status: Optional[str] = None,
    legacy_nav_status: Optional[str] = None,
    new_nav_status: Optional[str] = None,
) -> str:
    """Return PASS/WARN/DIFF/BLOCKED using action-aware comparison policy.

    DOM comparison is intentionally disabled. The regression result is judged
    by action status, normalized URL policy, and visual comparison only.
    """
    if "BLOCKED" in {
        legacy_action_status,
        new_action_status,
        legacy_nav_status,
        new_nav_status,
    }:
        return "BLOCKED"

    normalized_action = (action_type or "page_snapshot").lower()
    visual_required = normalized_action not in NON_VISUAL_ACTION_TYPES
    url_required_actions = {"navigate", "page_snapshot"} | CHILD_NAVIGATION_CASE_TYPES
    url_required = normalized_action in url_required_actions

    if visual_status == "BLOCKED":
        return "BLOCKED"

    if visual_required and visual_status == "DIFF":
        return "DIFF"

    if url_required and not url_match:
        return "DIFF"

    if not url_match:
        return "WARN"

    return "PASS"


class RegressionEngine:
    """
    Risk-first Legacy/New regression runner backed by page_mapping.json.

    The engine opens equivalent pages in both environments, replays mapped
    locator-change actions, compares URL/visual state, and writes a
    side-by-side HTML report.
    """

    def __init__(
        self,
        mapping_path: str = "generated/valid/page_mapping.json",
        *,
        legacy_base_url: Optional[str] = None,
        new_base_url: Optional[str] = None,
        output_dir: str = "./output/regression",
        visual_threshold_percent: float = 0.1,
        timeout: int = 15000,
        checklist_path: Optional[str] = None,
        route_map_path: Optional[str] = None,
        force_route_map: bool = False,
        upload_file: Optional[str] = None,
        upload_profile_config: Optional[str] = None,
        include_semi_auto: bool = False,
        include_destructive: bool = False,
        include_negative: bool = False,
        negative_profile: Optional[str] = None,
    ) -> None:
        self.mapping_path = Path(mapping_path)
        self.checklist_path = self._resolve_checklist_path(checklist_path)
        self._last_checklist_debug: Dict[str, Any] = {}
        self._checklist_case_rows: Dict[str, List[Dict[str, Any]]] = {}
        self.legacy_base_url = legacy_base_url or Config.LEGACY_URL
        self.new_base_url = new_base_url or Config.NEW_URL
        self.output_dir = Path(output_dir)
        self.visual_threshold_percent = visual_threshold_percent
        self.timeout = timeout
        self.force_route_map = force_route_map
        self.upload_file = str(upload_file) if upload_file else None
        self.upload_profiles = self._load_upload_profiles(upload_profile_config)
        self.include_semi_auto = include_semi_auto
        self.include_destructive = include_destructive
        self.include_negative = include_negative
        self.negative_profiles = {
            item.strip().lower()
            for item in re.split(r"[\r\n,;]+", str(negative_profile or ""))
            if item.strip()
        }
        self.current_browser_name = ""
        self.mapping = self.load_mapping()
        self.route_map_catalog = RouteMapCatalog(self._route_map_paths(route_map_path))

    @staticmethod
    def _install_print_suppression(page: Optional[Page]) -> Dict[str, Any]:
        if page is None:
            return {"installed": False, "reason": "missing_page"}
        try:
            return install_print_suppression(page.context)
        except Exception as exc:
            return {"installed": False, "reason": str(exc)}

    @staticmethod
    def _resolve_checklist_path(checklist_path: Optional[str]) -> Optional[Path]:
        if checklist_path:
            return Path(checklist_path)

        default_path = Path("generated/valid/migration_checklist.xlsx")
        return default_path if default_path.exists() else None

    @staticmethod
    def _load_upload_profiles(path: Optional[str]) -> List[Dict[str, Any]]:
        if not path:
            return []
        profile_path = Path(path)
        if not profile_path.exists():
            return []
        try:
            payload = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if isinstance(payload, list):
            profiles = payload
        else:
            profiles = payload.get("upload_profiles") if isinstance(payload, dict) else []
        return [item for item in profiles or [] if isinstance(item, dict)]

    @classmethod
    def _json_safe(cls, value: Any, *, depth: int = 0) -> Any:
        if depth > 8:
            return str(value)
        if value is None or isinstance(value, (bool, int, float, str)):
            if isinstance(value, str) and len(value) > 20000:
                return value[:20000] + "...<truncated>"
            return value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {
                str(key): cls._json_safe(item, depth=depth + 1)
                for key, item in value.items()
                if str(key) not in {"popup_page"}
            }
        if isinstance(value, (list, tuple, set)):
            return [cls._json_safe(item, depth=depth + 1) for item in value]
        return str(value)

    def _page_debug_summary(self, page: Optional[Page]) -> Dict[str, Any]:
        if page is None:
            return {"missing": True}
        closed = self._page_is_closed(page)
        summary: Dict[str, Any] = {
            "closed": closed,
            "url": "about:closed" if closed else self._safe_page_url(page),
        }
        try:
            summary["frame_urls"] = [] if closed else [str(frame.url or "") for frame in page.frames[:20]]
        except Exception as exc:
            summary["frame_error"] = str(exc)
        try:
            context_pages = []
            for index, item in enumerate(list(page.context.pages)[:20]):
                item_closed = self._page_is_closed(item)
                context_pages.append(
                    {
                        "index": index,
                        "closed": item_closed,
                        "url": "about:closed" if item_closed else self._safe_page_url(item),
                    }
                )
            summary["context_pages"] = context_pages
        except Exception as exc:
            summary["context_pages_error"] = str(exc)
        return summary

    @staticmethod
    def _state_debug_summary(state: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(state, dict):
            return {}
        return {
            "url": state.get("url"),
            "screenshot": state.get("screenshot"),
            "page_closed": state.get("page_closed"),
            "target_frame": state.get("target_frame"),
            "screenshot_error": state.get("screenshot_error"),
            "capture_error": state.get("capture_error"),
            "browser_screen_error": state.get("browser_screen_error"),
            "text_sample": str(state.get("text") or "")[:500],
        }

    def _reset_full_test_log(self, page_dir: Path) -> None:
        log_path = page_dir / "full_test_log.jsonl"
        try:
            if log_path.exists():
                log_path.unlink()
        except OSError:
            pass

    @staticmethod
    def _path_is_within(path: Path, parent: Path) -> bool:
        try:
            path.resolve().relative_to(parent.resolve())
            return True
        except (OSError, ValueError):
            return False

    @staticmethod
    def _remove_output_child(path: Path) -> str:
        if path.is_symlink() or path.is_file():
            path.unlink()
            return "file"
        if path.is_dir():
            shutil.rmtree(path)
            return "dir"
        path.unlink(missing_ok=True)
        return "other"

    def _prepare_page_output_dir(self, page_dir: Path) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "status": "PASS",
            "page_dir": str(page_dir),
            "removed_files": 0,
            "removed_dirs": 0,
            "errors": [],
        }
        if not self._path_is_within(page_dir, self.output_dir):
            summary.update(
                {
                    "status": "SKIPPED",
                    "reason": f"Refusing to clean outside output_dir: {page_dir}",
                }
            )
            page_dir.mkdir(parents=True, exist_ok=True)
            return summary

        if page_dir.exists():
            for child in list(page_dir.iterdir()):
                try:
                    removed_type = self._remove_output_child(child)
                    if removed_type == "dir":
                        summary["removed_dirs"] += 1
                    else:
                        summary["removed_files"] += 1
                except OSError as exc:
                    summary["errors"].append({"path": str(child), "error": str(exc)})
        page_dir.mkdir(parents=True, exist_ok=True)
        if summary["errors"]:
            summary["status"] = "WARN"
        return summary

    def _write_full_test_log(self, page_dir: Path, page_id: str, event: str, payload: Dict[str, Any]) -> None:
        page_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": event,
            "page_id": page_id,
            **payload,
        }
        with open(page_dir / "full_test_log.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(self._json_safe(record), ensure_ascii=False, default=str) + "\n")

    def load_mapping(self) -> Dict[str, Any]:
        if not self.mapping_path.exists():
            raise FileNotFoundError(f"page_mapping.json not found: {self.mapping_path}")
        return json.loads(self.mapping_path.read_text(encoding="utf-8"))

    def select_pages(
        self,
        *,
        risk_only: bool = True,
        risk_levels: Optional[Iterable[str]] = None,
        limit: Optional[int] = None,
        target_page: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Select pages for regression by risk level or target_page.

        Priority:
        1. When target_page is specified, return only the matching page.
        2. Otherwise filter and sort by risk_level.
        """

        # ------------------------------------------------------------------
        # Single-page execution.
        # ------------------------------------------------------------------
        if target_page:
            normalized_target = self._target_page_name(target_page)
            target_aliases = self._target_page_aliases(target_page)

            pages = [
                page
                for page in self.mapping.get("page_mappings", [])
                if (
                    self._target_page_name(page.get("page_id")) == normalized_target
                    or bool(self._target_page_aliases(page.get("page_id")) & target_aliases)
                )
            ]

            if not pages:
                available = sorted(
                    self._target_page_name(page.get("page_id"))
                    for page in self.mapping.get("page_mappings", [])
                    if page.get("page_id")
                )

                sample = ", ".join(available[:10])

                suffix = (
                    f" Available pages include: {sample}"
                    if sample
                    else " Mapping contains no pages."
                )

                raise ValueError(
                    f"Target JSP page not found in mapping file "
                    f"{self.mapping_path}: {target_page}.{suffix}"
                )

            return pages[:1]

        # ------------------------------------------------------------------
        # Risk-level filtering.
        # ------------------------------------------------------------------
        levels = list(
            risk_levels
            or (
                ["High", "Medium"]
                if risk_only
                else ["High", "Medium", "Low"]
            )
        )

        rank = {
            level.lower(): index
            for index, level in enumerate(levels)
        }

        pages = [
            page
            for page in self.mapping.get("page_mappings", [])
            if str(page.get("risk", "")).lower() in rank
        ]

        # ------------------------------------------------------------------
        # Sort by risk level, then page_id.
        # ------------------------------------------------------------------
        pages.sort(
            key=lambda page: (
                rank[str(page.get("risk", "")).lower()],
                page.get("page_id", ""),
            )
        )

        # ------------------------------------------------------------------
        # Apply optional limit.
        # ------------------------------------------------------------------
        return pages[:limit] if limit else pages


    def run(
        self,
        legacy_page: Page,
        new_page: Page,
        *,
        risk_only: bool = True,
        risk_levels: Optional[Iterable[str]] = None,
        limit: Optional[int] = None,
        target_page: Optional[str] = None,
        browser_name: str = "chrome",
        manual: bool = False,
    ) -> Dict[str, Any]:
        self.current_browser_name = browser_name
        self.output_dir.mkdir(parents=True, exist_ok=True)
        results: List[Dict[str, Any]] = []
        for page_index, mapping in enumerate(
            self.select_pages(
                risk_only=risk_only,
                risk_levels=risk_levels,
                limit=limit,
                target_page=target_page,
            ),
            start=1,
        ):
            results.extend(
                self._run_page_pair(
                    legacy_page,
                    new_page,
                    mapping,
                    page_index=page_index,
                    browser_name=browser_name,
                    manual=manual,
                )
            )

        report_path = self.render_report(results)
        overall_status = "PASS"
        if any(item["status"] == "BLOCKED" for item in results):
            overall_status = "BLOCKED"
        elif any(item["status"] == "DIFF" for item in results):
            overall_status = "DIFF"

        return {
            "status": overall_status,
            "results": results,
            "report_path": str(report_path),
            "debug_log_path": str(Path(report_path).parent / "full_test_log.jsonl"),
            "results_json_path": str(Path(report_path).parent / "regression_results.json"),
            "summary": dict(Counter(item["status"] for item in results)),
        }

    def _run_page_pair(
        self,
        legacy_page: Page,
        new_page: Page,
        mapping: Dict[str, Any],
        *,
        page_index: int,
        browser_name: str,
        manual: bool = False,
    ) -> List[Dict[str, Any]]:
        page_id = mapping.get("page_id") or f"page_{page_index}"

        page_dir = (
            self.output_dir
            / f"{page_index:04d}_{self._safe_name(page_id)}"
        )
        page_output_cleanup = self._prepare_page_output_dir(page_dir)
        self._reset_full_test_log(page_dir)
        print_suppression = {
            "legacy": self._install_print_suppression(legacy_page),
            "new": self._install_print_suppression(new_page),
        }

        entry_url = (
            mapping.get("entry_url")
            or mapping.get("resolved_entry_url")
            or page_id
        )

        legacy_url = self._page_url(
            self.legacy_base_url,
            entry_url,
        )

        new_url = self._page_url(
            self.new_base_url,
            entry_url,
        )
        self._write_full_test_log(
            page_dir,
            page_id,
            "page_start",
            {
                "page_index": page_index,
                "browser": browser_name,
                "manual": manual,
                "entry_url": entry_url,
                "legacy_target_url": legacy_url,
                "new_target_url": new_url,
                "print_suppression": print_suppression,
                "page_output_cleanup": page_output_cleanup,
                "mapping_keys": sorted(str(key) for key in mapping.keys()),
                "legacy_page": self._page_debug_summary(legacy_page),
                "new_page": self._page_debug_summary(new_page),
            },
        )

        # ============================================================
        # MANUAL MODE
        # ============================================================
        if manual:
            print("\n[MANUAL MODE] Operate the browser manually and navigate to the target page:")
            print(f" - Expected target page: {page_id}")
            print(f" - Legacy entry: {legacy_url}")
            print(f" - New entry:    {new_url}")

            def _interactive_takeover(
                page: Page,
                label: str,
            ) -> Page:
                print(
                    f"\n >>> [{label}] Press Enter here when the page is ready for takeover..."
                )

                print(
                    "     (If browser clicks do not respond, press Enter to activate the sync lock.)"
                )

                while True:
                    # ------------------------------------------------
                    # Keep Playwright protocol messages flowing.
                    # ------------------------------------------------
                    try:
                        page.wait_for_timeout(200)
                    except Exception:
                        pass

                    input_signal = input(
                        f" [{label} READY?] "
                        f"Press Enter to scan pages, or enter 'q' to cancel: "
                    ).strip().lower()

                    if input_signal == "q":
                        raise InterruptedError(
                            "User cancelled manual takeover"
                        )

                    all_pages = page.context.pages

                    print(
                        f" [{label}] Detected {len(all_pages)} page object(s):"
                    )

                    match_idx = -1

                    # ------------------------------------------------
                    # Match priority:
                    # 1. URL contains the JSP name.
                    # 2. URL contains the corresponding Struts action.
                    # ------------------------------------------------
                    target_action = str(
                        mapping.get("entry_url")
                        or mapping.get("resolved_entry_url")
                        or page_id
                    ).lower()
                    target_aliases = self._target_page_aliases(page_id) | self._target_page_aliases(target_action)

                    for idx, page_item in enumerate(all_pages):
                        url_lower = page_item.url.lower()
                        url_aliases = self._target_page_aliases(page_item.url)
                        try:
                            for frame in page_item.frames:
                                url_aliases.update(self._target_page_aliases(frame.url))
                        except PlaywrightError:
                            pass

                        print(
                            f"    [{idx}] "
                            f"{page_item.url[:120]}"
                        )

                        if (
                            page_id.lower() in url_lower
                            or target_action in url_lower
                            or bool(target_aliases & url_aliases)
                        ):
                            match_idx = idx

                    # ------------------------------------------------
                    # Auto-detected matching page.
                    # ------------------------------------------------
                    if match_idx != -1:
                        print(
                            f" [SUCCESS] Potential matching page found: "
                            f"[{match_idx}]"
                        )

                        choice_idx = match_idx

                    # ------------------------------------------------
                    # Allow manual selection when auto-match fails.
                    # ------------------------------------------------
                    else:
                        print(
                            f" [WARN] No page contains "
                            f"'{page_id}' or action "
                            f"'{target_action}'."
                        )

                        raw_choice = input(
                            f" >>> Enter page index "
                            f"[0-{len(all_pages)-1}] "
                            f"to select manually, or press Enter to retry: "
                        ).strip()

                        if (
                            raw_choice.isdigit()
                            and 0 <= int(raw_choice) < len(all_pages)
                        ):
                            choice_idx = int(raw_choice)
                        else:
                            continue

                    # ------------------------------------------------
                    # Take over the selected target page.
                    # ------------------------------------------------
                    target = all_pages[choice_idx]

                    try:
                        target.bring_to_front()

                        target.wait_for_load_state(
                            "domcontentloaded",
                            timeout=3000,
                        )

                        return target

                    except Exception as exc:
                        print(
                            f" [ERROR] Takeover failed ({exc}); retry."
                        )

            # ========================================================
            # Legacy/New manual takeover.
            # ========================================================
            legacy_page = _interactive_takeover(
                legacy_page,
                "Legacy",
            )

            new_page = _interactive_takeover(
                new_page,
                "New",
            )

            print(
                f" [INFO] Takeover targets redirected: "
                f"Legacy({legacy_page.url}) | "
                f"New({new_page.url})"
            )

            legacy_nav = {
                "status": "PASS",
                "url": legacy_page.url,
                "manual": True,
            }

            new_nav = {
                "status": "PASS",
                "url": new_page.url,
                "manual": True,
            }

            # ========================================================
            # Manual mode runs directly against the taken-over pages.
            # ========================================================
            return self._run_captured_page_pair(
                legacy_page,
                new_page,
                mapping,
                page_dir,
                browser_name,
                legacy_nav,
                new_nav,
                manual=True,
            )

        # ============================================================
        # AUTO MODE
        # ============================================================
        route = self.route_map_catalog.find_for_target(page_id)
        if self.force_route_map and route:
            navigator = RouteNavigator(
                self.route_map_catalog,
                timeout=self.timeout,
                browser_name=browser_name,
                upload_file=self.upload_file,
            )
            print(
                f"[{page_id}] FORCE ROUTE MAP navigation: "
                + json.dumps(
                    {
                        "route_id": route.get("route_id"),
                        "route_map_path": route.get("route_map_path"),
                    },
                    ensure_ascii=False,
                )
            )
            legacy_page, legacy_nav = navigator.navigate(
                legacy_page,
                entry_url=self.legacy_base_url,
                target_page=page_id,
                capture_dir=page_dir,
                side="legacy",
            )
            new_page, new_nav = navigator.navigate(
                new_page,
                entry_url=self.new_base_url,
                target_page=page_id,
                capture_dir=page_dir,
                side="new",
            )
            self._write_full_test_log(
                page_dir,
                page_id,
                "initial_navigation",
                {
                    "strategy": "force_route_map",
                    "route_id": route.get("route_id"),
                    "legacy_nav": legacy_nav,
                    "new_nav": new_nav,
                    "legacy_page": self._page_debug_summary(legacy_page),
                    "new_page": self._page_debug_summary(new_page),
                },
            )
            return self._run_captured_page_pair(
                legacy_page,
                new_page,
                mapping,
                page_dir,
                browser_name,
                legacy_nav,
                new_nav,
                manual=False,
            )

        legacy_nav = self._goto(
            legacy_page,
            legacy_url,
        )

        new_nav = self._goto(
            new_page,
            new_url,
        )
        self._write_full_test_log(
            page_dir,
            page_id,
            "direct_navigation_attempt",
            {
                "legacy_nav": legacy_nav,
                "new_nav": new_nav,
                "legacy_reached": self._page_matches_mapping(legacy_page, mapping) if legacy_nav.get("status") == "PASS" else False,
                "new_reached": self._page_matches_mapping(new_page, mapping) if new_nav.get("status") == "PASS" else False,
                "legacy_page": self._page_debug_summary(legacy_page),
                "new_page": self._page_debug_summary(new_page),
            },
        )

        direct_legacy_reached = legacy_nav.get("status") == "PASS" and self._page_matches_mapping(legacy_page, mapping)
        direct_new_reached = new_nav.get("status") == "PASS" and self._page_matches_mapping(new_page, mapping)

        if not direct_legacy_reached or not direct_new_reached:
            if route:
                navigator = RouteNavigator(
                    self.route_map_catalog,
                    timeout=self.timeout,
                    browser_name=browser_name,
                    upload_file=self.upload_file,
                )
                print(
                    f"[{page_id}] DIRECT NAVIGATION fallback to route map: "
                    + json.dumps(
                        {
                            "legacy_direct_reached": direct_legacy_reached,
                            "new_direct_reached": direct_new_reached,
                            "route_id": route.get("route_id"),
                            "route_map_path": route.get("route_map_path"),
                        },
                        ensure_ascii=False,
                    )
                )

                if not direct_legacy_reached:
                    legacy_page, legacy_nav = navigator.navigate(
                        legacy_page,
                        entry_url=self.legacy_base_url,
                        target_page=page_id,
                        capture_dir=page_dir,
                        side="legacy",
                    )
                    # Keep the page stable before later element lookup.
                    if legacy_nav.get("status") == "PASS":
                        try:
                            legacy_page.wait_for_load_state("networkidle", timeout=5000)
                        except:
                            pass
                else:
                    legacy_nav.update({"strategy": "direct_url", "target_reached": True})

                if not direct_new_reached:
                    new_page, new_nav = navigator.navigate(
                        new_page,
                        entry_url=self.new_base_url,
                        target_page=page_id,
                        capture_dir=page_dir,
                        side="new",
                    )
                    # Keep the page stable before later element lookup.
                    if new_nav.get("status") == "PASS":
                        try:
                            new_page.wait_for_load_state("networkidle", timeout=5000)
                        except:
                            pass
                else:
                    new_nav.update({"strategy": "direct_url", "target_reached": True})
                self._write_full_test_log(
                    page_dir,
                    page_id,
                    "route_map_fallback_navigation",
                    {
                        "route_id": route.get("route_id"),
                        "legacy_direct_reached": direct_legacy_reached,
                        "new_direct_reached": direct_new_reached,
                        "legacy_nav": legacy_nav,
                        "new_nav": new_nav,
                        "legacy_page": self._page_debug_summary(legacy_page),
                        "new_page": self._page_debug_summary(new_page),
                    },
                )
            else:
                if not direct_legacy_reached:
                    legacy_nav.update(
                        {
                            "status": "BLOCKED",
                            "strategy": "direct_url",
                            "target_reached": False,
                            "reason": legacy_nav.get("reason") or f"Direct navigation did not reach target page and no route map exists: {page_id}",
                        }
                    )
                if not direct_new_reached:
                    new_nav.update(
                        {
                            "status": "BLOCKED",
                            "strategy": "direct_url",
                            "target_reached": False,
                            "reason": new_nav.get("reason") or f"Direct navigation did not reach target page and no route map exists: {page_id}",
                        }
                    )
        else:
            legacy_nav.update({"strategy": "direct_url", "target_reached": True})
            new_nav.update({"strategy": "direct_url", "target_reached": True})

        return self._run_captured_page_pair(
            legacy_page,
            new_page,
            mapping,
            page_dir,
            browser_name,
            legacy_nav,
            new_nav,
            manual=False,
        )


    @staticmethod
    def _skip_mapping_fallback_action(action: Dict[str, Any]) -> bool:
        kind = str(action.get("kind") or "").lower()
        action_hint = str(action.get("action_hint") or action.get("action_type") or "").lower()
        automation_mode = str(action.get("automation_mode") or "").lower()
        semantic_action = infer_semantic_action(action_hint or kind, action)

        # Source-only fallback does not know the business scenario. Bare forms
        # and terminal close-window buttons are only safe when provided by an
        # explicit checklist/executable case.
        if kind == "form" or automation_mode == "scenario_only" or semantic_action == "submit":
            return True
        if action_hint in {"close_window", "window_close"} or kind == "close_window":
            return True
        return False

    def _build_action_plan(self, page_id: str, mapping: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str]:
        """
        Build executable runtime action plan.

        Priority:
        1. Checklist Excel cases with automation_mode=auto
        2. page_mapping.json executable_cases
        3. locator_changes + full_action_steps fallback

        Form-only submit steps are intentionally skipped in fallback mode because
        they must be combined with related inputs/files into a scenario such as
        upload_submit.
        """
        checklist_cases = self._load_checklist_cases(page_id, mapping)
        if checklist_cases:
            return checklist_cases, "checklist"

        executable_cases = mapping.get("executable_cases") or []
        if executable_cases:
            return [self._normalize_action_case(case) for case in executable_cases], "executable_cases"

        actions: List[Dict[str, Any]] = []
        for change in mapping.get("locator_changes", []) or []:
            if self._skip_mapping_fallback_action(change):
                continue
            actions.append(self._normalize_action_case(change))

        for step in mapping.get("full_action_steps", []) or []:
            action_hint = str(step.get("action_hint") or "").lower()

            if self._skip_mapping_fallback_action(step):
                continue

            legacy_loc = step.get("legacy_locator") or step.get("locator")
            new_loc = step.get("new_locator") or legacy_loc
            merged = {
                "legacy_locator": legacy_loc,
                "new_locator": new_loc,
                "label": step.get("label"),
                "semantic_key": step.get("semantic_key"),
                "kind": step.get("kind"),
                "action_hint": step.get("action_hint"),
                "action_type": step.get("action_type") or action_hint,
                "raw": step.get("raw"),
                "attributes": step.get("attributes", {}),
                "line": step.get("line"),
                "value": step.get("value") or step.get("test_data"),
                "source": "full_action_steps",
            }
            actions.append(self._normalize_action_case(merged))

        return self._dedupe_runtime_actions(actions), "mapping_fallback"

    def _load_checklist_cases(self, page_id: str, mapping: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Load executable cases from generated migration checklist.

        This is intentionally tolerant. If the Excel does not yet contain
        machine-readable columns, it returns [] and the engine falls back to
        mapping executable_cases/full_action_steps.
        """
        target_name = self._target_page_name(page_id)
        target_aliases = self._target_page_aliases(page_id)
        debug: Dict[str, Any] = {
            "target": target_name,
            "target_aliases": sorted(target_aliases),
            "path": str(self.checklist_path) if self.checklist_path else None,
            "status": "not_configured",
        }
        self._last_checklist_debug = debug
        coverage_rows: List[Dict[str, Any]] = []
        self._checklist_case_rows[target_name] = coverage_rows

        if not self.checklist_path:
            return []

        if not self.checklist_path.exists():
            debug["status"] = "missing_file"
            return []

        if self.checklist_path.suffix.lower() == ".json":
            return self._load_guided_json_checklist_cases(
                page_id=page_id,
                target_name=target_name,
                target_aliases=target_aliases,
                debug=debug,
                coverage_rows=coverage_rows,
            )

        try:
            from openpyxl import load_workbook
        except Exception as exc:
            debug["status"] = "openpyxl_unavailable"
            debug["error"] = str(exc)
            return []

        try:
            wb = load_workbook(self.checklist_path, data_only=True, read_only=True)
            sheet = wb["Checklist"] if "Checklist" in wb.sheetnames else wb[wb.sheetnames[0]]
            rows = list(sheet.iter_rows(values_only=True))
        except Exception as exc:
            debug["status"] = "read_error"
            debug["error"] = str(exc)
            return []

        if not rows:
            debug["status"] = "empty_sheet"
            return []

        headers = [str(value or "").strip() for value in rows[0]]
        header_map = {header.lower(): idx for idx, header in enumerate(headers) if header}

        def col(*names: str) -> Optional[int]:
            for name in names:
                key = name.strip().lower()
                if key in header_map:
                    return header_map[key]
            return None

        page_col = col("page_id", "page", "screen", "画面", "ページ", "対象画面")
        mode_col = col("automation_mode", "自動化モード", "自動化", "automation")
        case_type_col = col("case_type", "ケース種別", "case")
        action_type_col = col("action_type", "action", "アクション")
        locator_col = col("locator", "selector", "セレクタ")
        legacy_locator_col = col("legacy_locator", "legacy selector", "移行前locator")
        new_locator_col = col("new_locator", "new selector", "移行後locator")
        submit_locator_col = col("submit_locator", "submit selector")
        test_data_col = col("test_data", "value", "テストデータ")
        operation_col = col("operation", "操作", "操作内容")
        pre_steps_col = col("pre_steps", "pre steps")
        main_step_col = col("main_step", "main step")
        expected_type_col = col("expected_type", "期待種別")
        expected_value_col = col("expected_value", "期待値")
        destructive_col = col("destructive", "破壊的", "destructive?")
        generated_by_col = col("generated_by")
        enabled_col = col("enabled", "enable", "有効")
        case_id_col = col("case_id", "id", "no", "項目no")
        title_col = col("title", "test_title", "test_viewpoint", "テスト観点")

        debug["sheet"] = sheet.title
        debug["columns"] = {
            "page_col": page_col,
            "mode_col": mode_col,
            "case_type_col": case_type_col,
            "action_type_col": action_type_col,
            "locator_col": locator_col,
            "submit_locator_col": submit_locator_col,
            "operation_col": operation_col,
            "pre_steps_col": pre_steps_col,
            "main_step_col": main_step_col,
            "expected_type_col": expected_type_col,
            "expected_value_col": expected_value_col,
            "destructive_col": destructive_col,
            "enabled_col": enabled_col,
        }

        if page_col is None or mode_col is None:
            debug["status"] = "missing_required_columns"
            return []

        cases: List[Dict[str, Any]] = []
        stats: Counter[str] = Counter()
        samples: List[Dict[str, str]] = []
        allowed_modes = {"auto", "automated", "true", "yes", "y", "1", "自動"}
        semi_auto_modes = {"semi-auto", "semiauto", "semi auto", "半自動"}

        def add_sample(kind: str, **values: str) -> None:
            if len(samples) < 5:
                samples.append({"kind": kind, **values})

        def coverage_row(row_page: str, case_id: str, test_title: str, automation_mode: str, destructive_value: str) -> Dict[str, Any]:
            row_info = {
                "page_id": row_page,
                "case_id": case_id,
                "test_title": test_title,
                "automation_mode": automation_mode,
                "destructive": destructive_value or "false",
                "excluded_reason": "",
            }
            coverage_rows.append(row_info)
            return row_info

        for row in rows[1:]:
            def cell(idx: Optional[int]) -> str:
                if idx is None or idx >= len(row):
                    return ""
                value = row[idx]
                return "" if value is None else str(value).strip()

            row_page = cell(page_col)
            row_aliases = self._target_page_aliases(row_page)
            if self._target_page_name(row_page) != target_name and not (row_aliases & target_aliases):
                stats["page_not_matched"] += 1
                continue

            stats["page_matched"] += 1
            case_id = cell(case_id_col)
            test_title = cell(title_col)
            mode = cell(mode_col)
            destructive_value = cell(destructive_col) or "false"
            checklist_coverage = coverage_row(row_page, case_id, test_title, mode, destructive_value)

            enabled = cell(enabled_col).lower()
            if enabled in {"false", "0", "no", "n", "disabled", "off", "無効", "否"}:
                stats["disabled"] += 1
                add_sample("disabled", page=row_page, enabled=enabled)
                checklist_coverage["excluded_reason"] = f"enabled={enabled or '<blank>'}"
                continue

            mode = mode.lower()
            mode_allowed = mode in allowed_modes or mode.startswith("auto")
            if not mode_allowed and self.include_semi_auto:
                mode_allowed = mode in semi_auto_modes or mode.startswith("semi")
            if not mode_allowed:
                stats[f"mode_rejected:{mode or '<blank>'}"] += 1
                add_sample("mode_rejected", page=row_page, mode=mode, case_id=case_id)
                if mode in semi_auto_modes or mode.startswith("semi"):
                    checklist_coverage["excluded_reason"] = f"automation_mode={mode or '<blank>'}; requires --include-semi-auto"
                else:
                    checklist_coverage["excluded_reason"] = f"automation_mode={mode or '<blank>'} is not executable"
                continue

            stats["auto_matched" if mode.startswith("auto") or mode in allowed_modes else "semi_auto_matched"] += 1

            case_type = cell(case_type_col) or cell(action_type_col) or "click"
            action_type = cell(action_type_col) or case_type
            action_lower = str(action_type or case_type).strip().lower()
            case_lower = str(case_type or action_type).strip().lower()
            locator = cell(locator_col)
            legacy_locator = cell(legacy_locator_col) or locator
            new_locator = cell(new_locator_col) or locator or legacy_locator
            submit_locator = cell(submit_locator_col)
            test_data = cell(test_data_col)
            label = test_title or case_id or case_type
            operation = cell(operation_col)
            pre_steps_text = cell(pre_steps_col)
            main_step_text = cell(main_step_col)
            expected_type = cell(expected_type_col)
            expected_value = cell(expected_value_col)
            destructive = self._truthy(destructive_value)
            negative_case = self._is_negative_case(case_type, action_type)
            download_case = self._is_download_checklist_case(
                case_id=case_id,
                case_type=case_type,
                action_type=action_type,
                expected_type=expected_type,
                label=label,
                operation=operation,
            )

            if destructive and not self.include_destructive:
                stats["destructive_rejected"] += 1
                add_sample("destructive_rejected", page=row_page, case_id=case_id, case_type=case_type)
                checklist_coverage["excluded_reason"] = "destructive=true; requires --include-destructive"
                continue

            if negative_case and not self.include_negative:
                stats["negative_rejected"] += 1
                add_sample("negative_rejected", page=row_page, case_id=case_id, case_type=case_type)
                checklist_coverage["excluded_reason"] = "negative case; requires --include-negative"
                continue
            if negative_case and self.include_negative and not self._negative_profile_enabled(case_type, action_type, cell(generated_by_col)):
                stats["negative_profile_rejected"] += 1
                add_sample("negative_profile_rejected", page=row_page, case_id=case_id, case_type=case_type)
                checklist_coverage["excluded_reason"] = "negative case; --negative-profile did not match"
                continue

            is_upload_submit = self._is_upload_submit_case(
                case_type=case_type,
                action_type=action_type,
                label=label,
                operation=operation,
                submit_locator=submit_locator,
                main_step_text=main_step_text,
                expected_type=expected_type,
                case_id=case_id,
                locator=locator or legacy_locator,
            )

            if download_case:
                parsed_main_step = self._parse_step_json(main_step_text)
                parsed_pre_steps = self._parse_steps_json(pre_steps_text)
                download_locator = locator or legacy_locator
                if self._is_download_step(parsed_main_step):
                    download_locator = (
                        parsed_main_step.get("legacy_locator")
                        or parsed_main_step.get("locator")
                        or parsed_main_step.get("new_locator")
                        or download_locator
                    )
                if not download_locator:
                    for step in parsed_pre_steps + ([parsed_main_step] if parsed_main_step else []):
                        if not self._is_download_step(step):
                            continue
                        step_locator = step.get("legacy_locator") or step.get("locator") or step.get("new_locator")
                        if step_locator:
                            download_locator = step_locator
                            break
                if not download_locator:
                    stats["locator_missing_download"] += 1
                    add_sample("locator_missing_download", page=row_page, case_id=case_id, mode=mode)
                    checklist_coverage["excluded_reason"] = "download case has no locator"
                    continue
                text_for_kind = " ".join(str(value or "").lower() for value in (case_id, case_type, action_type, label))
                download_kind = "download_template" if any(token in text_for_kind for token in ("download_template", "template", "テンプレート")) else "file_download"
                case_payload = {
                    "case_id": case_id,
                    "case_type": download_kind,
                    "action_type": download_kind,
                    "label": label,
                    "test_title": test_title,
                    "page_id": row_page,
                    "legacy_locator": legacy_locator or download_locator,
                    "new_locator": new_locator or download_locator,
                    "locator": download_locator,
                    "value": test_data,
                    "test_data": test_data,
                    "expected_type": expected_type or "download",
                    "expected_value": expected_value,
                    "destructive": str(destructive).lower(),
                    "source": "checklist",
                }
                option_steps = self._download_condition_steps(parsed_pre_steps)
                if option_steps:
                    case_payload["pre_steps"] = option_steps
                    case_payload["main_step"] = {
                        "action_type": "download",
                        "legacy_locator": legacy_locator or download_locator,
                        "new_locator": new_locator or download_locator,
                        "locator": download_locator,
                    }
                cases.append(self._normalize_action_case(case_payload))
                stats["loadable_download"] += 1
            elif is_upload_submit:
                if not locator and not legacy_locator:
                    stats["locator_missing_upload"] += 1
                    add_sample("locator_missing_upload", page=row_page, case_id=case_id, mode=mode)
                    checklist_coverage["excluded_reason"] = "upload_submit case has no upload locator"
                    continue
                main_step = self._parse_step_json(main_step_text)
                main_locator = submit_locator or main_step.get("submit_locator") or main_step.get("locator") or self._submit_locator_from_mapping(mapping, upload_locator=locator)
                main_action_type = main_step.get("action_type") or ("click" if main_locator and not str(main_locator).strip().lower().startswith("form") else "submit")
                case: Dict[str, Any] = {
                    "case_id": case_id,
                    "case_type": "upload_submit",
                    "action_type": "upload_submit",
                    "label": label,
                    "test_title": test_title,
                    "page_id": row_page,
                    "source": "checklist",
                    "expected_type": expected_type,
                    "destructive": str(destructive).lower(),
                    "pre_steps": [
                        {
                            "action_type": "upload",
                            "legacy_locator": legacy_locator or locator,
                            "new_locator": new_locator or locator,
                            "locator": locator or legacy_locator,
                            "value": test_data,
                        }
                    ],
                    "main_step": {
                        "action_type": main_action_type,
                        "legacy_locator": main_locator,
                        "new_locator": main_locator,
                        "locator": main_locator,
                        "submit_locator": submit_locator,
                    },
                }
                cases.append(self._normalize_action_case(case))
                stats["loadable_upload_submit"] += 1
            else:
                parsed_pre_steps = self._parse_steps_json(pre_steps_text)
                parsed_main_step = self._parse_step_json(main_step_text)
                if action_lower in {"snapshot", "page_snapshot", "visual_check", "wait", "initial_display"}:
                    locator = locator or "__page__"
                    legacy_locator = legacy_locator or locator
                    new_locator = new_locator or locator
                elif parsed_pre_steps or parsed_main_step:
                    locator = locator or parsed_main_step.get("locator") or "__page__"
                    legacy_locator = legacy_locator or parsed_main_step.get("legacy_locator") or locator
                    new_locator = new_locator or parsed_main_step.get("new_locator") or locator or legacy_locator
                elif not legacy_locator and not new_locator:
                    stats["locator_missing"] += 1
                    add_sample("locator_missing", page=row_page, case_id=case_id, mode=mode)
                    checklist_coverage["excluded_reason"] = "locator is missing"
                    continue
                case_payload = {
                    "case_id": case_id,
                    "case_type": case_type,
                    "action_type": action_type,
                    "label": label,
                    "test_title": test_title,
                    "page_id": row_page,
                    "legacy_locator": legacy_locator,
                    "new_locator": new_locator or legacy_locator,
                    "locator": locator,
                    "value": test_data,
                    "test_data": test_data,
                    "expected_type": expected_type,
                    "expected_value": expected_value,
                    "destructive": str(destructive).lower(),
                    "source": "checklist",
                }
                if parsed_pre_steps:
                    case_payload["pre_steps"] = parsed_pre_steps
                if parsed_main_step:
                    case_payload["main_step"] = parsed_main_step
                cases.append(self._normalize_action_case(case_payload))
                stats["loadable_action"] += 1

        debug["status"] = "loaded" if cases else "no_cases"
        debug["loaded"] = len(cases)
        debug["stats"] = dict(stats)
        debug["samples"] = samples
        return cases

    def _load_guided_json_checklist_cases(
        self,
        *,
        page_id: str,
        target_name: str,
        target_aliases: set[str],
        debug: Dict[str, Any],
        coverage_rows: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Load AI/manual guided checklist JSON directly as executable cases."""
        try:
            payload = json.loads(self.checklist_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            debug["status"] = "json_read_error"
            debug["error"] = str(exc)
            return []

        if isinstance(payload, list):
            raw_cases = payload
            default_page = page_id
            schema = "list"
        elif isinstance(payload, dict):
            raw_cases = (
                payload.get("cases")
                or payload.get("checklist_cases")
                or payload.get("checklist")
                or payload.get("items")
                or []
            )
            default_page = payload.get("page_id") or payload.get("target_page") or page_id
            schema = str(payload.get("schema") or "moonlight.guided_checklist.v1")
        else:
            raw_cases = []
            default_page = page_id
            schema = "invalid"

        debug["format"] = "guided_json"
        debug["schema"] = schema
        if not isinstance(raw_cases, list):
            debug["status"] = "json_cases_not_list"
            return []

        allowed_modes = {"auto", "automated", "true", "yes", "y", "1"}
        semi_auto_modes = {"semi-auto", "semiauto", "semi auto"}
        cases: List[Dict[str, Any]] = []
        stats: Counter[str] = Counter()
        samples: List[Dict[str, str]] = []

        def add_sample(kind: str, **values: str) -> None:
            if len(samples) < 5:
                samples.append({"kind": kind, **values})

        def as_text(value: Any, default: str = "") -> str:
            if value is None:
                return default
            if isinstance(value, bool):
                return "true" if value else "false"
            return str(value)

        def as_steps(value: Any) -> List[Dict[str, Any]]:
            if not isinstance(value, list):
                return []
            return [dict(item) for item in value if isinstance(item, dict)]

        def expected_parts(item: Dict[str, Any]) -> Tuple[str, str]:
            expected = item.get("expected")
            expected_type = as_text(item.get("expected_type"))
            expected_value = as_text(item.get("expected_value"))
            if isinstance(expected, dict):
                expected_type = expected_type or as_text(expected.get("type") or expected.get("expected_type"))
                expected_value = expected_value or as_text(expected.get("value") or expected.get("expected_value"))
            elif isinstance(expected, list):
                expected_value = expected_value or "\n".join(as_text(part) for part in expected if as_text(part))
            elif expected not in (None, ""):
                expected_value = expected_value or as_text(expected)
            return expected_type, expected_value

        for index, item in enumerate(raw_cases, start=1):
            if not isinstance(item, dict):
                stats["invalid_case"] += 1
                continue

            row_page = as_text(item.get("page_id") or item.get("page") or default_page or page_id)
            row_aliases = self._target_page_aliases(row_page)
            if self._target_page_name(row_page) != target_name and not (row_aliases & target_aliases):
                stats["page_not_matched"] += 1
                continue

            stats["page_matched"] += 1
            case_id = as_text(item.get("case_id") or item.get("id") or f"guided-{index:03d}")
            title = as_text(item.get("title") or item.get("test_title") or case_id)
            risk_level = as_text(item.get("risk_level") or item.get("risk"), "safe").strip().lower()
            destructive = self._truthy(item.get("destructive")) or risk_level in {"destructive", "danger", "dangerous"}
            mode = as_text(item.get("automation_mode")).strip().lower()
            if not mode:
                mode = "manual" if destructive or risk_level == "manual" else "auto"
            destructive_value = "true" if destructive else "false"
            row_info = {
                "page_id": row_page,
                "case_id": case_id,
                "test_title": title,
                "automation_mode": mode,
                "destructive": destructive_value,
                "excluded_reason": "",
            }
            coverage_rows.append(row_info)

            if item.get("enabled") is False or as_text(item.get("enabled")).lower() in {"false", "0", "no", "n", "disabled", "off"}:
                stats["disabled"] += 1
                row_info["excluded_reason"] = "enabled=false"
                continue

            mode_allowed = mode in allowed_modes or mode.startswith("auto")
            if not mode_allowed and self.include_semi_auto:
                mode_allowed = mode in semi_auto_modes or mode.startswith("semi")
            if not mode_allowed:
                stats[f"mode_rejected:{mode or '<blank>'}"] += 1
                if mode in semi_auto_modes or mode.startswith("semi"):
                    row_info["excluded_reason"] = f"automation_mode={mode}; requires --include-semi-auto"
                else:
                    row_info["excluded_reason"] = f"automation_mode={mode or '<blank>'} is not executable"
                add_sample("mode_rejected", page=row_page, mode=mode, case_id=case_id)
                continue

            if destructive and not self.include_destructive:
                stats["destructive_rejected"] += 1
                row_info["excluded_reason"] = "destructive=true; requires --include-destructive"
                add_sample("destructive_rejected", page=row_page, case_id=case_id)
                continue

            pre_steps = as_steps(item.get("pre_steps"))
            main_step = dict(item.get("main_step")) if isinstance(item.get("main_step"), dict) else {}
            steps = as_steps(item.get("steps"))
            if steps and not main_step:
                pre_steps.extend(steps[:-1])
                main_step = dict(steps[-1])
            if not main_step:
                main_step = {
                    "action_type": item.get("action_type") or "snapshot",
                    "locator": item.get("locator") or "__page__",
                }

            expected_type, expected_value = expected_parts(item)
            for key in (
                "expected_url",
                "expected_url_fragment",
                "target_url",
                "url_pattern",
                "expected_page",
                "target_page",
                "target_jsp",
                "opens_popup",
                "popup",
                "keep_popup",
                "close_after",
                "capture_opener_after_child",
            ):
                if key in item and key not in main_step:
                    main_step[key] = item.get(key)
            if expected_value and not main_step.get("value") and str(main_step.get("action_type") or "").lower() in {
                "assert_text",
                "expect_text",
                "verify_text",
                "assert_value",
                "expect_value",
                "verify_value",
                "assert_url",
                "expect_url",
            }:
                main_step["value"] = expected_value

            action_type = as_text(item.get("action_type") or main_step.get("action_type") or item.get("case_type") or "scenario")
            case_type = as_text(item.get("case_type") or action_type or "scenario")
            locator = as_text(item.get("locator") or main_step.get("locator") or "__page__")

            negative_case = self._is_negative_case(case_type, action_type)
            if negative_case and not self.include_negative:
                stats["negative_rejected"] += 1
                row_info["excluded_reason"] = "negative case; requires --include-negative"
                add_sample("negative_rejected", page=row_page, case_id=case_id, case_type=case_type)
                continue
            if negative_case and self.include_negative and not self._negative_profile_enabled(
                case_type,
                action_type,
                as_text(item.get("generated_by")),
            ):
                stats["negative_profile_rejected"] += 1
                row_info["excluded_reason"] = "negative case; --negative-profile did not match"
                add_sample("negative_profile_rejected", page=row_page, case_id=case_id, case_type=case_type)
                continue

            main_step.setdefault("locator", locator)
            main_step.setdefault("legacy_locator", item.get("legacy_locator") or locator)
            main_step.setdefault("new_locator", item.get("new_locator") or locator)

            case_payload = {
                "case_id": case_id,
                "case_type": case_type,
                "action_type": action_type,
                "label": title,
                "test_title": title,
                "page_id": row_page,
                "legacy_locator": as_text(item.get("legacy_locator") or locator),
                "new_locator": as_text(item.get("new_locator") or locator),
                "locator": locator,
                "value": as_text(item.get("value") or item.get("test_data")),
                "test_data": as_text(item.get("test_data") or item.get("value")),
                "expected_type": expected_type,
                "expected_value": expected_value,
                "expected_url": as_text(item.get("expected_url") or item.get("expected_url_fragment") or item.get("target_url") or item.get("url_pattern")),
                "expected_page": as_text(item.get("expected_page") or item.get("target_page") or item.get("target_jsp")),
                "opens_popup": item.get("opens_popup") if "opens_popup" in item else item.get("popup"),
                "keep_popup": item.get("keep_popup"),
                "close_after": item.get("close_after"),
                "capture_opener_after_child": item.get("capture_opener_after_child"),
                "database_operation": item.get("database_operation") or item.get("db_operation"),
                "risk_level": risk_level,
                "destructive": destructive_value,
                "source": "guided_json_checklist",
                "pre_steps": pre_steps,
                "main_step": main_step,
            }
            cases.append(self._normalize_action_case(case_payload))
            stats["loadable_action"] += 1

        debug["status"] = "loaded" if cases else "no_cases"
        debug["loaded"] = len(cases)
        debug["stats"] = dict(stats)
        debug["samples"] = samples
        return cases

    @staticmethod
    def _truthy(value: Any) -> bool:
        return str(value or "").strip().lower() in {"true", "1", "yes", "y", "on", "破壊", "対象"}

    @staticmethod
    def _is_negative_case(case_type: Any, action_type: Any) -> bool:
        text = " ".join(str(value or "").lower() for value in (case_type, action_type))
        return "negative" in text or text.startswith("error_") or any(case in text for case in NEGATIVE_CASE_TYPES)

    def _negative_profile_enabled(self, case_type: Any, action_type: Any, generated_by: Any = "") -> bool:
        if not self.negative_profiles:
            return False
        haystack = " ".join(str(value or "").lower() for value in (case_type, action_type, generated_by))
        return any(profile in haystack for profile in self.negative_profiles)

    @staticmethod
    def _is_download_checklist_case(
        *,
        case_id: Any,
        case_type: Any,
        action_type: Any,
        expected_type: Any,
        label: Any,
        operation: Any,
    ) -> bool:
        text = " ".join(str(value or "").lower() for value in (case_id, case_type, action_type, expected_type, label, operation))
        return (
            "download" in text
            or "ダウンロード" in str(label or "")
            or "出力ファイル" in str(label or "")
            or str(expected_type or "").strip().lower() == "download"
        )

    @staticmethod
    def _is_upload_submit_case(
        *,
        case_type: Any,
        action_type: Any,
        label: Any,
        operation: Any,
        submit_locator: Any,
        main_step_text: Any,
        expected_type: Any = "",
        case_id: Any = "",
        locator: Any = "",
    ) -> bool:
        case_lower = str(case_type or "").lower()
        action_lower = str(action_type or "").lower()
        download_text = " ".join(str(value or "").lower() for value in (case_id, case_type, action_type, expected_type, label, operation, main_step_text, locator))
        if "download" in download_text or "ダウンロード" in str(label or "") or str(expected_type or "").strip().lower() == "download":
            return False
        if "negative" in case_lower or "negative" in action_lower:
            return False
        label_text = str(label or "")
        operation_lower = str(operation or "").lower()
        main_step_lower = str(main_step_text or "").lower()
        if case_lower == "upload_submit" or action_lower == "upload_submit":
            return True
        if "アップロード確認" in label_text:
            return True
        if submit_locator:
            return True
        upload_tokens = ("upload", "アップロード", "file", "ファイル")
        submit_tokens = ("submit", "click", "確認", "送信", "押下")
        if any(token in operation_lower for token in upload_tokens) and any(token in operation_lower for token in submit_tokens):
            return True
        if any(token in main_step_lower for token in ("submit", "click", "submit_locator")):
            return True
        return False

    @staticmethod
    def _parse_step_json(value: Any) -> Dict[str, Any]:
        if not value:
            return {}
        try:
            parsed = json.loads(str(value))
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _parse_steps_json(value: Any) -> List[Dict[str, Any]]:
        if not value:
            return []
        try:
            parsed = json.loads(str(value))
        except Exception:
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict)]

    @staticmethod
    def _is_download_step(step: Dict[str, Any]) -> bool:
        if not isinstance(step, dict):
            return False
        text = " ".join(
            str(step.get(key) or "").lower()
            for key in ("action_type", "case_type", "expected_type", "locator", "legacy_locator", "new_locator", "label")
        )
        return "download" in text or "fndownload" in text or "ダウンロード" in text

    @staticmethod
    def _download_condition_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        allowed = {"check", "uncheck", "select", "fill", "clear", "set_value", "press"}
        result: List[Dict[str, Any]] = []
        for step in steps:
            action_type = str(step.get("action_type") or step.get("type") or "").strip().lower()
            if action_type not in allowed:
                continue
            locator = step.get("locator") or step.get("legacy_locator") or step.get("new_locator")
            if not locator:
                continue
            copied = dict(step)
            copied["action_type"] = action_type
            copied.setdefault("locator", locator)
            copied.setdefault("legacy_locator", locator)
            copied.setdefault("new_locator", locator)
            result.append(copied)
        return result

    @staticmethod
    def _submit_locator_from_mapping(mapping: Optional[Dict[str, Any]], upload_locator: Any = "") -> str:
        if not mapping:
            return ""
        candidates = list(mapping.get("executable_cases") or []) + list(mapping.get("locator_changes") or []) + list(mapping.get("full_action_steps") or [])
        for item in candidates:
            locator = item.get("locator") or item.get("legacy_locator") or item.get("new_locator")
            evidence = " ".join(str(item.get(key) or "").lower() for key in ("action_hint", "action_type", "kind", "label", "raw", "semantic_key", "locator"))
            if locator and any(token in evidence for token in ("submit", "upload", "confirm", "確認", "アップロード")):
                return str(locator)
        return ""

    def _normalize_action_case(self, action: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(action)
        if "main_step" in normalized or "pre_steps" in normalized:
            normalized.setdefault("case_type", normalized.get("action_type") or "scenario")
            normalized.setdefault("action_type", normalized.get("case_type"))
            normalized.setdefault("kind", normalized.get("case_type"))
            normalized.setdefault("label", normalized.get("label") or normalized.get("case_type"))
            return normalized

        normalized.setdefault("legacy_locator", normalized.get("locator"))
        normalized.setdefault("new_locator", normalized.get("legacy_locator") or normalized.get("locator"))
        normalized.setdefault("action_type", normalized.get("action_hint") or normalized.get("kind") or "click")
        normalized.setdefault("case_type", normalized.get("action_type"))
        normalized.setdefault("label", normalized.get("label") or normalized.get("semantic_key") or normalized.get("legacy_locator") or normalized.get("case_type"))
        return normalized

    def _dedupe_runtime_actions(self, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        unique: List[Dict[str, Any]] = []
        for action in actions:
            if action.get("pre_steps") or action.get("main_step"):
                key = (
                    action.get("case_type"),
                    action.get("label"),
                    json.dumps(action.get("pre_steps", []), sort_keys=True, ensure_ascii=False, default=str),
                    json.dumps(action.get("main_step", {}), sort_keys=True, ensure_ascii=False, default=str),
                )
            else:
                key = (
                    action.get("case_type"),
                    action.get("legacy_locator"),
                    action.get("new_locator"),
                    action.get("label"),
                )
            if key in seen:
                continue
            seen.add(key)
            unique.append(action)
        return unique

    @staticmethod
    def _action_side_locator(action: Dict[str, Any], side: str) -> Optional[str]:
        side_key = f"{side}_locator"
        return action.get(side_key) or action.get("locator") or action.get("selector")

    @staticmethod
    def _upload_value_is_placeholder(value: Any) -> bool:
        text = str(value or "").strip()
        if not text:
            return True
        lowered = text.lower()
        return (
            lowered.startswith("${")
            or lowered in {"$upload_file", "upload_file"}
            or "fakepath" in lowered
            or lowered in {"${upload_invalid_file}", "${upload_empty_file}", "${upload_large_file}"}
        )

    @staticmethod
    def _existing_upload_value(value: Any) -> Optional[Any]:
        if isinstance(value, list):
            paths = [str(item) for item in value if Path(str(item)).exists()]
            return paths if paths and len(paths) == len(value) else None
        text = str(value or "").strip()
        if text and Path(text).exists():
            return text
        return None

    def _resolve_upload_value(self, value: Any, action_case: Dict[str, Any], step: Dict[str, Any], locator: Any) -> Any:
        existing = self._existing_upload_value(value)
        if existing is not None:
            return existing

        profile_value = self._upload_value_from_profiles(action_case, step, locator)
        if profile_value:
            return profile_value

        if self.upload_file:
            return self.upload_file

        return "" if self._upload_value_is_placeholder(value) else value

    def _upload_value_from_profiles(self, action_case: Dict[str, Any], step: Dict[str, Any], locator: Any) -> Optional[Any]:
        page_id = str(action_case.get("page_id") or "").lower()
        case_id = str(action_case.get("case_id") or step.get("case_id") or "").lower()
        case_type = str(action_case.get("case_type") or step.get("case_type") or step.get("action_type") or "").lower()
        locator_text = str(locator or step.get("locator") or "").strip()
        negative_case = self._is_negative_case(action_case.get("case_type"), step.get("action_type") or action_case.get("action_type"))

        def case_id_patterns(profile: Dict[str, Any]) -> List[str]:
            raw_patterns: List[Any] = []
            if profile.get("case_id"):
                raw_patterns.append(profile.get("case_id"))
            raw_patterns.extend(profile.get("case_ids") or [])
            patterns: List[str] = []
            for raw in raw_patterns:
                for item in re.split(r"[\r\n,;]+", str(raw or "")):
                    item = item.strip().lower()
                    if item and item not in patterns:
                        patterns.append(item)
            return patterns

        def matches(profile: Dict[str, Any], *, require_case_id: bool) -> bool:
            patterns = case_id_patterns(profile)
            if require_case_id and not patterns:
                return False
            if not require_case_id and patterns:
                return False
            if patterns and (not case_id or not any(fnmatch.fnmatch(case_id, pattern) for pattern in patterns)):
                return False
            profile_negative = self._truthy(profile.get("negative"))
            if profile_negative and not negative_case:
                return False
            page_patterns = profile.get("page_patterns") or []
            if page_patterns and not any(fnmatch.fnmatch(page_id, str(pattern).lower()) for pattern in page_patterns):
                return False
            case_types = [str(item).lower() for item in profile.get("case_types") or [] if str(item).strip()]
            if case_types and not any(fnmatch.fnmatch(case_type, pattern) for pattern in case_types):
                return False
            profile_locator = str(profile.get("locator") or "").strip()
            if profile_locator and profile_locator != locator_text and profile_locator not in locator_text and locator_text not in profile_locator:
                return False
            return True

        for require_case_id in (True, False):
            for profile in self.upload_profiles:
                if not matches(profile, require_case_id=require_case_id):
                    continue
                files = profile.get("files")
                if isinstance(files, list):
                    existing = self._existing_upload_value(files)
                    if existing:
                        return existing
                file_value = profile.get("file")
                existing = self._existing_upload_value(file_value)
                if existing:
                    return existing
        return None

    def _execute_action_case(
        self,
        page: Page,
        action_case: Dict[str, Any],
        *,
        side: str,
        browser_name: str,
        capture_dir: Path,
        test_id: str,
    ) -> Dict[str, Any]:
        """
        Execute a single action or a scenario composed of pre_steps + main_step.
        Example: upload_submit = set_input_files(...) then submit form.
        """
        steps: List[Dict[str, Any]] = []
        if action_case.get("pre_steps") or action_case.get("main_step"):
            steps.extend(action_case.get("pre_steps") or [])
            main_step = action_case.get("main_step")
            if main_step:
                steps.append(main_step)
        else:
            steps.append(action_case)

        last_result: Optional[Dict[str, Any]] = None
        executed = []

        for index, step in enumerate(steps, start=1):
            step = dict(step)
            action_type = step.get("action_type") or step.get("action_hint") or step.get("kind") or action_case.get("action_type") or "click"
            locator = self._action_side_locator(step, side)
            value = step.get("value") or step.get("test_data") or action_case.get("value")
            if str(action_type or "").lower() == "submit" and locator and not str(locator).strip().lower().startswith("form"):
                action_type = "click"
                step["action_type"] = "click"
                step["action_hint"] = "click"
            semantic_action = infer_semantic_action(action_type, step)
            action_lower = str(action_type or semantic_action).strip().lower()
            if semantic_action == "upload":
                value = self._resolve_upload_value(value, action_case, step, locator)

            if action_lower in {"snapshot", "page_snapshot", "visual_check", "wait"}:
                result = {
                    "status": "PASS",
                    "action_type": action_type,
                    "semantic_action": "wait",
                    "state": _capture_state(page, capture_dir, f"{test_id}_step{index}"),
                }
                executed.append(
                    {
                        "index": index,
                        "action_type": action_type,
                        "locator": locator or "__page__",
                        "status": result.get("status"),
                    }
                )
                last_result = result
                continue

            # If the main submit locator is missing, try to submit nearest form from the first upload field.
            if not locator and semantic_action == "submit":
                locator = action_case.get("submit_locator") or step.get("submit_locator")
                if not locator:
                    locator = "form"

            if not locator and semantic_action not in {"goto", "save_pdf"}:
                return {
                    "status": "BLOCKED",
                    "reason": f"Missing locator for scenario step {index}: {action_type}",
                    "action_type": action_type,
                    "state": _capture_state(page, capture_dir, f"{test_id}_step{index}_blocked"),
                    "executed_steps": executed,
                }

            step_context = {**action_case, **step, "locator": locator}
            if index < len(steps):
                self._strip_parent_expectation_from_pre_step(step_context, step)
            if step.get("action_type") or step.get("action_hint") or step.get("kind"):
                step_context["action_type"] = action_type
                step_context["action_hint"] = step.get("action_hint") or action_type
                step_context["kind"] = step.get("kind") or ""

            result = execute_action(
                page,
                action_type,
                locator,
                value,
                browser_name=browser_name,
                capture_dir=capture_dir,
                test_id=f"{test_id}_step{index}",
                timeout=self.timeout,
                action_context=step_context,
            )
            executed.append(
                {
                    "index": index,
                    "action_type": action_type,
                    "locator": locator,
                    "status": result.get("status"),
                    "reason": result.get("reason"),
                    "upload_file": result.get("upload_file") if semantic_action == "upload" else None,
                    "submit_locator": locator if str(action_case.get("case_type") or "").lower() == "upload_submit" and index == len(steps) else None,
                }
            )
            last_result = result

            if result.get("status") != "PASS":
                break
            if result.get("page_closed_after_action"):
                break

        if last_result is None:
            return {
                "status": "BLOCKED",
                "reason": "No executable steps in action case",
                "state": _capture_state(page, capture_dir, f"{test_id}_empty"),
                "executed_steps": executed,
            }

        last_result = dict(last_result)
        last_result["case_type"] = action_case.get("case_type")
        last_result["executed_steps"] = executed
        return last_result

    @staticmethod
    def _strip_parent_expectation_from_pre_step(step_context: Dict[str, Any], step: Dict[str, Any]) -> None:
        expectation_keys = {
            "expected",
            "expected_type",
            "expected_value",
            "expected_url",
            "expected_url_fragment",
            "target_url",
            "url_pattern",
            "expected_page",
            "target_page",
            "target_jsp",
            "opens_popup",
            "popup",
        }
        for key in expectation_keys:
            if key not in step:
                step_context.pop(key, None)
        step_evidence = " ".join(
            str(step.get(key) or "").strip().lower()
            for key in ("action_type", "action_hint", "kind", "case_type", "expected_type")
        )
        if not any(marker in step_evidence for marker in CHILD_NAVIGATION_CASE_TYPES):
            inherited_case_type = str(step_context.get("case_type") or "").strip().lower()
            if "case_type" not in step and any(marker in inherited_case_type for marker in CHILD_NAVIGATION_CASE_TYPES):
                step_context["case_type"] = step_context.get("action_type") or step.get("action_type") or "step"

    @staticmethod
    def _executed_step_field(action_result: Dict[str, Any], field: str) -> Optional[Any]:
        for step in action_result.get("executed_steps") or []:
            value = step.get(field)
            if value:
                return value
        return None


    def _run_captured_page_pair(
        self,
        legacy_page: Page,
        new_page: Page,
        mapping: Dict[str, Any],
        page_dir: Path,
        browser_name: str,
        legacy_nav: Dict[str, Any],
        new_nav: Dict[str, Any],
        manual: bool,
    ) -> List[Dict[str, Any]]:
        page_id = mapping.get("page_id") or "unknown"
        results: List[Dict[str, Any]] = []

        initial_ready_timeout = min(max(self.timeout, 15000), 30000)
        try:
            legacy_ready = _wait_for_semantic_ready(legacy_page, timeout=initial_ready_timeout)
        except Exception as exc:
            legacy_ready = {"semantic_wait_error": str(exc)}
        try:
            new_ready = _wait_for_semantic_ready(new_page, timeout=initial_ready_timeout)
        except Exception as exc:
            new_ready = {"semantic_wait_error": str(exc)}
        legacy_state = _capture_state(legacy_page, page_dir, "00_legacy_initial")
        new_state = _capture_state(new_page, page_dir, "00_new_initial")
        self._write_full_test_log(
            page_dir,
            page_id,
            "initial_state_captured",
            {
                "legacy_nav": legacy_nav,
                "new_nav": new_nav,
                "legacy_state": self._state_debug_summary(legacy_state),
                "new_state": self._state_debug_summary(new_state),
                "legacy_ready": legacy_ready,
                "new_ready": new_ready,
                "legacy_page": self._page_debug_summary(legacy_page),
                "new_page": self._page_debug_summary(new_page),
            },
        )
        results.append(
            self._compare_state(
                page_id,
                mapping.get("risk"),
                "page_snapshot",
                legacy_state,
                new_state,
                page_dir / "00_diff.png",
                legacy_nav=legacy_nav,
                new_nav=new_nav,
            )
        )

        if legacy_nav.get("status") != "PASS" or new_nav.get("status") != "PASS":
            self._write_full_test_log(
                page_dir,
                page_id,
                "navigation_blocked_skip_actions",
                {
                    "legacy_nav": legacy_nav,
                    "new_nav": new_nav,
                    "legacy_page": self._page_debug_summary(legacy_page),
                    "new_page": self._page_debug_summary(new_page),
                },
            )
            print(
                f"[{page_id}] ROUTE NAVIGATION blocked; skip action plan: "
                + json.dumps(
                    {
                        "legacy_status": legacy_nav.get("status"),
                        "legacy_reason": legacy_nav.get("reason"),
                        "new_status": new_nav.get("status"),
                        "new_reason": new_nav.get("reason"),
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
            return results

        target_actions, plan_source = self._build_action_plan(page_id, mapping)
        if plan_source != "checklist":
            for blocked in mapping.get("missing_legacy_elements", []):
                if self._is_dynamic_jsp_row_control(blocked):
                    print(
                        f"[{page_id}] SKIP static dynamic row control: "
                        + json.dumps(
                            {
                                "label": blocked.get("label") or blocked.get("key"),
                                "locator": blocked.get("locator"),
                                "reason": "covered by runtime/checklist CRUD action",
                            },
                            ensure_ascii=False,
                        )
                    )
                    continue
                missing_status, missing_reason = self._missing_legacy_element_status(blocked)
                results.append(
                    {
                        "page_id": page_id,
                        "risk": mapping.get("risk"),
                        "action": blocked.get("label") or blocked.get("key") or "missing_legacy_element",
                        "action_type": infer_semantic_action(blocked.get("action_hint") or blocked.get("kind"), blocked),
                        "status": missing_status,
                        "reason": missing_reason,
                        "legacy_locator": blocked.get("locator"),
                        "new_locator": None,
                        "legacy_screenshot": legacy_state.get("screenshot"),
                        "new_screenshot": new_state.get("screenshot"),
                        "legacy_frame": legacy_state.get("target_frame"),
                        "new_frame": new_state.get("target_frame"),
                        "frame_candidates": {
                            "legacy": legacy_state.get("frame_candidates", []),
                            "new": new_state.get("frame_candidates", []),
                        },
                    }
                )
        self._write_full_test_log(
            page_dir,
            page_id,
            "action_plan_built",
            {
                "source": plan_source,
                "planned": len(target_actions),
                "checklist_path": str(self.checklist_path) if self.checklist_path else None,
                "checklist_debug": self._last_checklist_debug,
                "action_kinds": dict(Counter(action.get("kind") or action.get("case_type") or "unknown" for action in target_actions)),
                "actions": [
                    {
                        "index": index,
                        "case_id": action.get("case_id"),
                        "label": action.get("label") or action.get("test_title") or action.get("semantic_key"),
                        "case_type": action.get("case_type"),
                        "action_type": action.get("action_type"),
                        "legacy_locator": action.get("legacy_locator") or action.get("locator"),
                        "new_locator": action.get("new_locator") or action.get("locator"),
                    }
                    for index, action in enumerate(target_actions, start=1)
                ],
            },
        )
        print(
            f"[{page_id}] ACTION PLAN SOURCE: "
            + json.dumps(
                {
                    "source": plan_source,
                    "planned": len(target_actions),
                    "locator_changes": len(mapping.get("locator_changes", []) or []),
                    "executable_cases": len(mapping.get("executable_cases", []) or []),
                    "full_action_steps": len(mapping.get("full_action_steps", []) or []),
                    "checklist_path": str(self.checklist_path) if self.checklist_path else None,
                    "checklist_debug": self._last_checklist_debug,
                    "kinds": dict(Counter(action.get("kind") or action.get("case_type") or "unknown" for action in target_actions)),
                },
                ensure_ascii=False,
            )
        )

        for action_index, action_case in enumerate(target_actions, start=1):
            action_name = action_case.get("label") or action_case.get("semantic_key") or action_case.get("case_type") or f"action_{action_index}"
            action_type = action_case.get("case_type") or action_case.get("action_type") or action_case.get("action_hint") or action_case.get("kind") or "click"
            semantic_action = infer_semantic_action(action_type, action_case)
            legacy_locator = self._action_side_locator(action_case, "legacy")
            new_locator = self._action_side_locator(action_case, "new")
            action_file_id = self._safe_name(f"{action_index:02d}_{action_name}")

            if action_case.get("pre_steps") or action_case.get("main_step"):
                # Use first concrete step locator for report display.
                steps_for_display = list(action_case.get("pre_steps") or [])
                if action_case.get("main_step"):
                    steps_for_display.append(action_case.get("main_step"))
                for step in steps_for_display:
                    legacy_locator = legacy_locator or self._action_side_locator(step, "legacy")
                    new_locator = new_locator or self._action_side_locator(step, "new")

            print(
                f"[{page_id}] START action {action_index}/{len(target_actions)}: "
                + json.dumps(
                    {
                        "action": action_name,
                        "action_type": action_type,
                        "semantic_action": semantic_action,
                        "legacy_locator": legacy_locator,
                        "new_locator": new_locator,
                        "source": action_case.get("source") or plan_source,
                    },
                    ensure_ascii=False,
                )
            )
            self._write_full_test_log(
                page_dir,
                page_id,
                "action_start",
                {
                    "action_index": action_index,
                    "action_total": len(target_actions),
                    "action_file_id": action_file_id,
                    "action_name": action_name,
                    "action_type": action_type,
                    "semantic_action": semantic_action,
                    "legacy_locator": legacy_locator,
                    "new_locator": new_locator,
                    "plan_source": action_case.get("source") or plan_source,
                    "action_case": action_case,
                    "legacy_page_before": self._page_debug_summary(legacy_page),
                    "new_page_before": self._page_debug_summary(new_page),
                },
            )

            if not legacy_locator and not action_case.get("pre_steps") and not action_case.get("main_step"):
                self._write_full_test_log(
                    page_dir,
                    page_id,
                    "action_blocked_missing_locator",
                    {
                        "action_index": action_index,
                        "action_name": action_name,
                        "action_type": action_type,
                        "legacy_locator": legacy_locator,
                        "new_locator": new_locator,
                    },
                )
                results.append(
                    {
                        "page_id": page_id,
                        "risk": mapping.get("risk"),
                        "action": action_name,
                        "action_type": semantic_action,
                        "status": "BLOCKED",
                        "reason": "legacy_locator is missing",
                        "legacy_locator": legacy_locator,
                        "new_locator": new_locator,
                    }
                )
                continue

            database_operation = self._database_operation_kind(action_case, action_type, semantic_action)
            legacy_before_state: Optional[Dict[str, Any]] = None
            new_before_state: Optional[Dict[str, Any]] = None
            if database_operation:
                legacy_before_state = _capture_state(legacy_page, page_dir, f"{action_file_id}_legacy_before")
                new_before_state = _capture_state(new_page, page_dir, f"{action_file_id}_new_before")
                self._write_full_test_log(
                    page_dir,
                    page_id,
                    "database_before_state_captured",
                    {
                        "action_index": action_index,
                        "action_name": action_name,
                        "database_operation": database_operation,
                        "legacy_before_state": self._state_debug_summary(legacy_before_state),
                        "new_before_state": self._state_debug_summary(new_before_state),
                    },
                )

            legacy_action = self._execute_action_case(
                legacy_page,
                action_case,
                side="legacy",
                browser_name=browser_name,
                capture_dir=page_dir,
                test_id=f"{action_file_id}_legacy",
            )
            new_action = self._execute_action_case(
                new_page,
                action_case,
                side="new",
                browser_name=browser_name,
                capture_dir=page_dir,
                test_id=f"{action_file_id}_new",
            )
            self._write_full_test_log(
                page_dir,
                page_id,
                "action_executed",
                {
                    "action_index": action_index,
                    "action_name": action_name,
                    "action_type": action_type,
                    "semantic_action": semantic_action,
                    "legacy_action": legacy_action,
                    "new_action": new_action,
                    "legacy_state_summary": self._state_debug_summary(legacy_action.get("state") or {}),
                    "new_state_summary": self._state_debug_summary(new_action.get("state") or {}),
                    "legacy_page_after_execute": self._page_debug_summary(legacy_page),
                    "new_page_after_execute": self._page_debug_summary(new_page),
                },
            )

            print(
                f"[{page_id}] END action: "
                + json.dumps(
                    {
                        "action": action_name,
                        "legacy_status": legacy_action.get("status"),
                        "new_status": new_action.get("status"),
                        "legacy_reason": legacy_action.get("reason"),
                        "new_reason": new_action.get("reason"),
                        "legacy_steps": legacy_action.get("executed_steps"),
                        "new_steps": new_action.get("executed_steps"),
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )

            target_reopen_hint = self._requires_target_reopen_after_action(
                action_case,
                action_type,
                semantic_action,
                legacy_action,
                new_action,
            )
            target_reopen_state = self._action_left_target_page(
                mapping,
                legacy_page,
                new_page,
                legacy_action,
                new_action,
            )
            target_reopen_candidate = self._should_reopen_target_for_following_action(
                target_reopen_hint,
                target_reopen_state,
            )
            if target_reopen_hint and not target_reopen_candidate:
                self._write_full_test_log(
                    page_dir,
                    page_id,
                    "target_reopen_hint_suppressed",
                    {
                        "action_index": action_index,
                        "action_name": action_name,
                        "action_type": action_type,
                        "semantic_action": semantic_action,
                        "target_reopen_state": target_reopen_state,
                        "reason": "Action text suggested a recovery boundary, but current pages still match the target page.",
                    },
                )
            has_following_action = action_index < len(target_actions)
            should_reopen_target = target_reopen_candidate and has_following_action
            reopen_result: Optional[Dict[str, Any]] = None
            if target_reopen_candidate and not has_following_action:
                self._write_full_test_log(
                    page_dir,
                    page_id,
                    "target_reopen_skipped_terminal_action",
                    {
                        "action_index": action_index,
                        "action_name": action_name,
                        "action_type": action_type,
                        "semantic_action": semantic_action,
                        "target_reopen_state": target_reopen_state,
                        "reason": "No following checklist action requires target-page recovery.",
                    },
                )
                print(
                    f"[{page_id}] SKIP target reopen after terminal action: "
                    + json.dumps(
                        {
                            "action": action_name,
                            "reason": "No following checklist action requires target-page recovery.",
                        },
                        ensure_ascii=False,
                    )
                )
            if should_reopen_target:
                legacy_action["state_before_reopen"] = legacy_action.get("state") or {}
                new_action["state_before_reopen"] = new_action.get("state") or {}
                self._write_full_test_log(
                    page_dir,
                    page_id,
                    "target_reopen_required",
                    {
                        "action_index": action_index,
                        "action_name": action_name,
                        "action_type": action_type,
                        "semantic_action": semantic_action,
                        "target_reopen_state": target_reopen_state,
                        "legacy_state_before_reopen": self._state_debug_summary(legacy_action.get("state_before_reopen") or {}),
                        "new_state_before_reopen": self._state_debug_summary(new_action.get("state_before_reopen") or {}),
                        "legacy_page_before_reopen": self._page_debug_summary(legacy_page),
                        "new_page_before_reopen": self._page_debug_summary(new_page),
                    },
                )
                legacy_page, new_page, reopen_result = self._reopen_target_pair(
                    legacy_page,
                    new_page,
                    mapping,
                    page_dir,
                    browser_name,
                    reason=(
                        f"after action {action_index}: {action_name}; "
                        f"{target_reopen_state.get('reason') or 'action left the target page'}"
                    ),
                )
                if reopen_result.get("status") == "PASS":
                    legacy_reopened_state = _capture_state(legacy_page, page_dir, f"{action_file_id}_legacy_after_reopen")
                    new_reopened_state = _capture_state(new_page, page_dir, f"{action_file_id}_new_after_reopen")
                    legacy_action["state_after_reopen"] = legacy_reopened_state
                    new_action["state_after_reopen"] = new_reopened_state
                self._write_full_test_log(
                    page_dir,
                    page_id,
                    "target_reopen_finished",
                    {
                        "action_index": action_index,
                        "action_name": action_name,
                        "reopen_result": reopen_result,
                        "legacy_state_after_reopen": self._state_debug_summary(legacy_action.get("state_after_reopen") or {}),
                        "new_state_after_reopen": self._state_debug_summary(new_action.get("state_after_reopen") or {}),
                        "legacy_page_after_reopen": self._page_debug_summary(legacy_page),
                        "new_page_after_reopen": self._page_debug_summary(new_page),
                    },
                )

            if database_operation:
                compared = self._compare_database_operation(
                    page_id,
                    mapping.get("risk"),
                    action_name,
                    action_type,
                    database_operation,
                    legacy_before_state or {},
                    new_before_state or {},
                    legacy_action,
                    new_action,
                    page_dir,
                    action_file_id,
                )
            else:
                compared = self._compare_state(
                    page_id,
                    mapping.get("risk"),
                    action_name,
                    legacy_action.get("state") or {},
                    new_action.get("state") or {},
                    page_dir / f"{action_index:02d}_diff.png",
                    legacy_action=legacy_action,
                    new_action=new_action,
                    action_type=action_type,
                )
            compared.update(
                {
                    "case_id": action_case.get("case_id"),
                    "test_title": action_name,
                    "action_type": action_type,
                    "legacy_locator": legacy_locator,
                    "new_locator": new_locator,
                    "upload_file": self._executed_step_field(legacy_action, "upload_file")
                    or self._executed_step_field(new_action, "upload_file"),
                    "submit_locator": self._executed_step_field(legacy_action, "submit_locator")
                    or self._executed_step_field(new_action, "submit_locator"),
                    "legacy_after_url": legacy_action.get("after_url"),
                    "new_after_url": new_action.get("after_url"),
                    "navigation_detected": bool(legacy_action.get("navigation_detected") or new_action.get("navigation_detected")),
                    "popup_detected": bool(legacy_action.get("popup_detected") or new_action.get("popup_detected")),
                    "frame_changed": bool(legacy_action.get("frame_changed") or new_action.get("frame_changed")),
                    "validation_only": bool(legacy_action.get("validation_only") and new_action.get("validation_only")),
                    "legacy_console_screenshot": legacy_action.get("console_evidence_screenshot"),
                    "new_console_screenshot": new_action.get("console_evidence_screenshot"),
                    "legacy_console_error_count": legacy_action.get("console_error_count"),
                    "new_console_error_count": new_action.get("console_error_count"),
                    "legacy_http_error_count": legacy_action.get("http_error_count"),
                    "new_http_error_count": new_action.get("http_error_count"),
                    "legacy_request_failed_count": legacy_action.get("request_failed_count"),
                    "new_request_failed_count": new_action.get("request_failed_count"),
                    "legacy_action": legacy_action,
                    "new_action": new_action,
                    "plan_source": plan_source,
                }
            )
            if reopen_result is not None:
                compared["post_action_reopen"] = reopen_result
                if reopen_result.get("status") != "PASS":
                    compared["recovery_failed_for_following_actions"] = True
                    compared["recovery_reason"] = reopen_result.get("reason") or "Failed to reopen target page after leaving/closing action."
            print(
                f"[{page_id}] COMPARE result: "
                + json.dumps(
                    {
                        "action": action_name,
                        "status": compared.get("status"),
                        "url_match": compared.get("url_match"),
                        "visual_status": (compared.get("visual") or {}).get("status"),
                        "visual_diff_percent": (compared.get("visual") or {}).get("diff_percent"),
                        "comparison_mode": compared.get("comparison_mode"),
                        "database_operation": compared.get("database_operation"),
                        "post_action_reopen_status": (compared.get("post_action_reopen") or {}).get("status"),
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
            self._write_full_test_log(
                page_dir,
                page_id,
                "compare_result",
                {
                    "action_index": action_index,
                    "action_name": action_name,
                    "status": compared.get("status"),
                    "reason": compared.get("reason"),
                    "url_match": compared.get("url_match"),
                    "visual": compared.get("visual"),
                    "download": {
                        "success_match": compared.get("download_success_match"),
                        "legacy_success": compared.get("legacy_download_success"),
                        "new_success": compared.get("new_download_success"),
                        "filename_match": compared.get("download_filename_match"),
                        "size_match": compared.get("download_size_match"),
                        "hash_match": compared.get("download_hash_match"),
                        "extension_match": compared.get("download_extension_match"),
                        "legacy_filename": compared.get("legacy_download_filename"),
                        "new_filename": compared.get("new_download_filename"),
                        "legacy_path": compared.get("legacy_download_path"),
                        "new_path": compared.get("new_download_path"),
                        "legacy_saved_path": compared.get("legacy_download_saved_path"),
                        "new_saved_path": compared.get("new_download_saved_path"),
                        "legacy_original_path": compared.get("legacy_download_original_path"),
                        "new_original_path": compared.get("new_download_original_path"),
                        "legacy_archive_path": compared.get("legacy_download_archive_path"),
                        "new_archive_path": compared.get("new_download_archive_path"),
                        "legacy_size": compared.get("legacy_download_size"),
                        "new_size": compared.get("new_download_size"),
                        "legacy_sha256": compared.get("legacy_download_sha256"),
                        "new_sha256": compared.get("new_download_sha256"),
                    },
                    "pdf_save": {
                        "success_match": compared.get("pdf_save_success_match"),
                        "legacy_saved": compared.get("legacy_pdf_saved"),
                        "new_saved": compared.get("new_pdf_saved"),
                        "legacy_path": compared.get("legacy_pdf_path"),
                        "new_path": compared.get("new_pdf_path"),
                        "legacy_size": compared.get("legacy_pdf_size"),
                        "new_size": compared.get("new_pdf_size"),
                    },
                    "legacy_screenshot": compared.get("legacy_screenshot"),
                    "new_screenshot": compared.get("new_screenshot"),
                    "diff_screenshot": compared.get("diff_screenshot"),
                    "post_action_reopen": compared.get("post_action_reopen"),
                    "legacy_action_status": (compared.get("legacy_action") or {}).get("status"),
                    "new_action_status": (compared.get("new_action") or {}).get("status"),
                },
            )
            results.append(compared)
            if should_reopen_target and reopen_result is not None and reopen_result.get("status") != "PASS":
                results.append(
                    {
                        "page_id": page_id,
                        "risk": mapping.get("risk"),
                        "action": "Recover target page for remaining checklist actions",
                        "action_type": "target_reopen",
                        "status": "BLOCKED",
                        "reason": reopen_result.get("reason") or "Failed to reopen target page after leaving/closing action.",
                        "legacy_screenshot": (legacy_action.get("state") or {}).get("screenshot"),
                        "new_screenshot": (new_action.get("state") or {}).get("screenshot"),
                        "legacy_action": (reopen_result.get("legacy") or {}),
                        "new_action": (reopen_result.get("new") or {}),
                        "post_action_reopen": reopen_result,
                        "plan_source": plan_source,
                    }
                )
                break

        self._write_full_test_log(
            page_dir,
            page_id,
            "page_finished",
            {
                "result_counts": dict(Counter(item.get("status") or "UNKNOWN" for item in results)),
                "legacy_page_final": self._page_debug_summary(legacy_page),
                "new_page_final": self._page_debug_summary(new_page),
            },
        )
        return results

    @staticmethod
    def _is_dynamic_jsp_row_control(blocked: Dict[str, Any]) -> bool:
        evidence = " ".join(
            str(blocked.get(key) or "")
            for key in ("kind", "key", "label", "locator", "raw", "action", "semantic_key")
        ).lower()
        return (
            ("<bean:" in evidence or "<logic:" in evidence or "bean:write" in evidence)
            and any(marker in evidence for marker in ("delete", "削除", "button", "onclick"))
        )

    @staticmethod
    def _missing_legacy_element_status(blocked: Dict[str, Any]) -> Tuple[str, str]:
        return "BLOCKED", "Legacy element has no mapped New equivalent"

    @staticmethod
    def _database_operation_kind(action_case: Dict[str, Any], action_type: Any, semantic_action: Any) -> Optional[str]:
        explicit_operation = str(
            action_case.get("database_operation")
            or action_case.get("db_operation")
            or ""
        ).strip().lower()
        explicit_aliases = {
            "create": "create",
            "insert": "create",
            "update": "update",
            "modify": "update",
            "delete": "delete",
            "remove": "delete",
        }
        if explicit_operation:
            return explicit_aliases.get(explicit_operation)
        if str(action_case.get("source") or "").strip().lower() == "guided_json_checklist":
            return None

        normalized_type = str(action_type or "").strip().lower()
        normalized_case_type = str(action_case.get("case_type") or "").strip().lower()
        if normalized_type in CHILD_NAVIGATION_CASE_TYPES or normalized_case_type in CHILD_NAVIGATION_CASE_TYPES:
            return None
        if normalized_type in {
            "snapshot",
            "page_snapshot",
            "visual_check",
            "wait",
            "initial_display",
            "result_table_verify",
            "download_template",
            "file_download",
            "download",
            "save_pdf",
            "pdf_save",
            "saved_pdf",
            "print_to_pdf",
            "close_window",
            "back_action",
            "link_navigation",
            "assert_attached",
            "assert_checked",
            "assert_disabled",
            "assert_enabled",
            "assert_text",
            "assert_unchecked",
            "assert_url",
            "assert_value",
            "assert_visible",
            "upload_select",
            "upload_without_file",
            "upload_submit",
        }:
            return None

        evidence = " ".join(
            str(value or "")
            for value in (
                action_case.get("case_type"),
                action_case.get("action_type"),
                action_case.get("label"),
                action_case.get("semantic_key"),
                action_case.get("locator"),
                action_case.get("legacy_locator"),
                action_case.get("new_locator"),
                action_case.get("submit_locator"),
                action_case.get("expected_type"),
                action_type,
                semantic_action,
                json.dumps(action_case.get("main_step") or {}, ensure_ascii=False, default=str),
            )
        ).lower()

        english_words = lambda *words: re.search(r"\b(?:" + "|".join(re.escape(word) for word in words) + r")\b", evidence) is not None

        if "delete_action" in evidence or "deletefile" in evidence or english_words("delete", "remove") or any(token in evidence for token in ("削除", "消去")):
            return "delete"
        if english_words("update", "modify", "edit", "save") or any(token in evidence for token in ("更新", "変更", "編集", "保存")):
            return "update"
        if "upload_submit" in evidence or english_words("create", "insert", "entry", "register", "add") or any(token in evidence for token in ("登録", "新規", "追加", "作成", "アップロード")):
            return "create"
        return None

    def _compare_database_operation(
        self,
        page_id: str,
        risk: str,
        action: str,
        action_type: Any,
        operation_kind: str,
        legacy_before: Dict[str, Any],
        new_before: Dict[str, Any],
        legacy_action: Dict[str, Any],
        new_action: Dict[str, Any],
        page_dir: Path,
        action_file_id: str,
    ) -> Dict[str, Any]:
        legacy_after = legacy_action.get("state") or {}
        new_after = new_action.get("state") or {}
        legacy_delta = compare_visual_screenshot(
            legacy_before.get("screenshot", ""),
            legacy_after.get("screenshot", ""),
            str(page_dir / f"{action_file_id}_legacy_before_after_diff.png"),
            threshold_percent=self.visual_threshold_percent,
        )
        new_delta = compare_visual_screenshot(
            new_before.get("screenshot", ""),
            new_after.get("screenshot", ""),
            str(page_dir / f"{action_file_id}_new_before_after_diff.png"),
            threshold_percent=self.visual_threshold_percent,
        )

        legacy_changed = legacy_delta.get("status") == "DIFF"
        new_changed = new_delta.get("status") == "DIFF"
        legacy_url_changed = self._normalized_url(legacy_before.get("url", "")) != self._normalized_url(legacy_after.get("url", ""))
        new_url_changed = self._normalized_url(new_before.get("url", "")) != self._normalized_url(new_after.get("url", ""))
        transition_match = legacy_changed == new_changed and legacy_url_changed == new_url_changed

        status = "PASS"
        reason = "Database operation compared within each system before/after."
        if "BLOCKED" in {legacy_action.get("status"), new_action.get("status"), legacy_delta.get("status"), new_delta.get("status")}:
            status = "BLOCKED"
            reason = "Database operation action or before/after capture was blocked."
        elif not transition_match:
            status = "DIFF"
            reason = "Legacy/New database operation transition shape differs."
        elif operation_kind in {"create", "update", "delete"} and not legacy_changed and not new_changed:
            status = "WARN"
            reason = "Database mutation completed but no visible before/after change was detected in either system."

        max_diff = max(
            [
                float(value)
                for value in (legacy_delta.get("diff_percent"), new_delta.get("diff_percent"))
                if isinstance(value, (int, float))
            ]
            or [0.0]
        )
        aggregate_visual = {
            "status": "PASS" if status in {"PASS", "WARN"} else status,
            "diff_percent": max_diff,
            "comparison_mode": "database_operation_before_after",
            "legacy_delta": legacy_delta,
            "new_delta": new_delta,
        }

        return {
            "page_id": page_id,
            "risk": risk,
            "action": action,
            "status": status,
            "reason": reason,
            "url_match": transition_match,
            "dom_match": None,
            "visual": aggregate_visual,
            "comparison_mode": "database_operation_before_after",
            "database_operation": operation_kind,
            "legacy_url": legacy_after.get("url"),
            "new_url": new_after.get("url"),
            "legacy_screenshot": legacy_after.get("screenshot"),
            "new_screenshot": new_after.get("screenshot"),
            "diff_screenshot": None,
            "legacy_before_screenshot": legacy_before.get("screenshot"),
            "legacy_after_screenshot": legacy_after.get("screenshot"),
            "legacy_diff_screenshot": legacy_delta.get("diff_screenshot"),
            "new_before_screenshot": new_before.get("screenshot"),
            "new_after_screenshot": new_after.get("screenshot"),
            "new_diff_screenshot": new_delta.get("diff_screenshot"),
            "legacy_before_url": legacy_before.get("url"),
            "legacy_after_url": legacy_after.get("url"),
            "new_before_url": new_before.get("url"),
            "new_after_url": new_after.get("url"),
            "legacy_delta": legacy_delta,
            "new_delta": new_delta,
            "legacy_delta_changed": legacy_changed,
            "new_delta_changed": new_changed,
            "legacy_url_changed": legacy_url_changed,
            "new_url_changed": new_url_changed,
            "legacy_frame": legacy_after.get("target_frame"),
            "new_frame": new_after.get("target_frame"),
            "frame_candidates": {
                "legacy": legacy_after.get("frame_candidates", []),
                "new": new_after.get("frame_candidates", []),
            },
            "legacy_action": legacy_action,
            "new_action": new_action,
        }

    def _action_left_target_page(
        self,
        mapping: Dict[str, Any],
        legacy_page: Page,
        new_page: Page,
        legacy_action: Dict[str, Any],
        new_action: Dict[str, Any],
    ) -> Dict[str, Any]:
        legacy_transition = bool(
            legacy_action.get("page_closed_after_action")
            or legacy_action.get("navigation_detected")
            or legacy_action.get("frame_changed")
        )
        new_transition = bool(
            new_action.get("page_closed_after_action")
            or new_action.get("navigation_detected")
            or new_action.get("frame_changed")
        )
        if not (legacy_transition or new_transition):
            return {"requires_reopen": False, "reason": "action did not navigate away from the current page"}

        popup_parent_preserved = (
            bool(legacy_action.get("popup_detected"))
            and bool(new_action.get("popup_detected"))
            and not self._page_is_closed(legacy_page)
            and not self._page_is_closed(new_page)
            and self._normalized_url(self._safe_page_url(legacy_page))
            == self._normalized_url(str(legacy_action.get("before_url") or ""))
            and self._normalized_url(self._safe_page_url(new_page))
            == self._normalized_url(str(new_action.get("before_url") or ""))
        )
        if popup_parent_preserved:
            return {
                "requires_reopen": False,
                "reason": "child popup closed while the original target page remained open",
                "legacy_transition": legacy_transition,
                "new_transition": new_transition,
                "legacy_matches_target": True,
                "new_matches_target": True,
                "legacy_popup_detected": True,
                "new_popup_detected": True,
                "popup_parent_preserved": True,
            }

        legacy_matches = (not self._page_is_closed(legacy_page)) and self._page_matches_mapping(legacy_page, mapping)
        new_matches = (not self._page_is_closed(new_page)) and self._page_matches_mapping(new_page, mapping)
        requires_reopen = not (legacy_matches and new_matches)
        reason = (
            "action changed URL/frame and current page no longer matches target page"
            if requires_reopen
            else "action changed URL/frame but current page still matches target page"
        )
        return {
            "requires_reopen": requires_reopen,
            "reason": reason,
            "legacy_transition": legacy_transition,
            "new_transition": new_transition,
            "legacy_matches_target": legacy_matches,
            "new_matches_target": new_matches,
            "legacy_navigation_detected": bool(legacy_action.get("navigation_detected")),
            "new_navigation_detected": bool(new_action.get("navigation_detected")),
            "legacy_frame_changed": bool(legacy_action.get("frame_changed")),
            "new_frame_changed": bool(new_action.get("frame_changed")),
            "legacy_page_closed_after_action": bool(legacy_action.get("page_closed_after_action")),
            "new_page_closed_after_action": bool(new_action.get("page_closed_after_action")),
            "legacy_popup_detected": bool(legacy_action.get("popup_detected")),
            "new_popup_detected": bool(new_action.get("popup_detected")),
        }

    @staticmethod
    def _should_reopen_target_for_following_action(
        target_reopen_hint: bool,
        target_reopen_state: Dict[str, Any],
    ) -> bool:
        # Textual hints such as "cancel" or "back" are useful diagnostics, but
        # they must not trigger recovery unless the active pages actually left
        # the target mapping.
        return bool(target_reopen_state.get("requires_reopen"))

    @staticmethod
    def _requires_target_reopen_after_action(
        action_case: Dict[str, Any],
        action_type: Any,
        semantic_action: Any,
        legacy_action: Dict[str, Any],
        new_action: Dict[str, Any],
    ) -> bool:
        if legacy_action.get("page_closed_after_action") or new_action.get("page_closed_after_action"):
            return True
        if RegressionEngine._is_negative_case(action_case.get("case_type"), action_case.get("action_type") or action_type):
            return bool(
                legacy_action.get("navigation_detected")
                or new_action.get("navigation_detected")
                or legacy_action.get("frame_changed")
                or new_action.get("frame_changed")
            )

        passive_actions = {
            "assert_visible",
            "assert_hidden",
            "assert_text",
            "assert_value",
            "assert_url",
            "assert_attached",
            "assert_enabled",
            "assert_disabled",
            "assert_checked",
            "assert_unchecked",
            "manual_assert",
            "expect_visible",
            "expect_text",
            "expect_value",
            "expect_url",
            "expect_attached",
            "verify_visible",
            "verify_text",
            "verify_value",
            "verify_attached",
            "snapshot",
            "page_snapshot",
            "visual_check",
            "wait",
        }
        steps: List[Dict[str, Any]] = []
        if action_case.get("pre_steps") or action_case.get("main_step"):
            steps.extend(action_case.get("pre_steps") or [])
            if isinstance(action_case.get("main_step"), dict):
                steps.append(action_case["main_step"])
        if steps:
            step_actions = {
                str(step.get("action_type") or step.get("action_hint") or step.get("kind") or "").strip().lower()
                for step in steps
            }
            if step_actions and step_actions <= passive_actions:
                return False
        elif str(semantic_action or action_type or "").strip().lower() in passive_actions:
            return False

        evidence = " ".join(
            str(value or "")
            for value in (
                action_case.get("case_type"),
                action_case.get("action_type"),
                action_case.get("label"),
                action_case.get("semantic_key"),
                action_case.get("locator"),
                action_case.get("legacy_locator"),
                action_case.get("new_locator"),
                action_case.get("expected_type"),
                action_type,
                semantic_action,
                json.dumps(action_case.get("main_step") or {}, ensure_ascii=False, default=str),
            )
        ).lower()
        if any(marker in evidence for marker in CHILD_NAVIGATION_CASE_TYPES):
            if not (legacy_action.get("popup_detected") or new_action.get("popup_detected")) and (
                legacy_action.get("navigation_detected")
                or new_action.get("navigation_detected")
                or legacy_action.get("frame_changed")
                or new_action.get("frame_changed")
            ):
                return True
        if any(marker in evidence for marker in ("close_window", "window.close", "parent.close")):
            return True
        if any(marker in evidence for marker in ("back_action", "キャンセル", "取消", "戻る", "戻り", "戻 ")):
            return True
        if re.search(r"\b(?:cancel|back|bak)\b", evidence):
            return True
        return False

    def _reopen_target_pair(
        self,
        legacy_page: Page,
        new_page: Page,
        mapping: Dict[str, Any],
        page_dir: Path,
        browser_name: str,
        *,
        reason: str,
    ) -> Tuple[Page, Page, Dict[str, Any]]:
        page_id = mapping.get("page_id") or "unknown"
        print(
            f"[{page_id}] REOPEN target page after leaving/closing action: "
            + json.dumps({"reason": reason}, ensure_ascii=False)
        )
        self._write_full_test_log(
            page_dir,
            page_id,
            "reopen_pair_start",
            {
                "reason": reason,
                "legacy_page_before": self._page_debug_summary(legacy_page),
                "new_page_before": self._page_debug_summary(new_page),
            },
        )

        legacy_page, legacy_back = self._try_history_back_reopen_side(
            legacy_page,
            mapping,
            page_dir,
            "legacy_reopen_history_back",
        )
        new_page, new_back = self._try_history_back_reopen_side(
            new_page,
            mapping,
            page_dir,
            "new_reopen_history_back",
        )
        if legacy_back.get("status") == "PASS" and new_back.get("status") == "PASS":
            result = {
                "status": "PASS",
                "reason": "Recovered target page using frame history.back().",
                "legacy": legacy_back,
                "new": new_back,
                "reopen_strategy": "history_back",
            }
            self._write_full_test_log(page_dir, page_id, "reopen_pair_history_back_success", result)
            return legacy_page, new_page, result

        legacy_page, legacy_nav = self._reopen_target_side(
            legacy_page,
            mapping,
            page_dir,
            browser_name,
            side="legacy",
        )
        new_page, new_nav = self._reopen_target_side(
            new_page,
            mapping,
            page_dir,
            browser_name,
            side="new",
        )
        status = "PASS" if legacy_nav.get("status") == "PASS" and new_nav.get("status") == "PASS" else "BLOCKED"
        result = {
            "status": status,
            "reason": reason if status == "PASS" else "Failed to reopen target page after leaving/closing action.",
            "legacy": legacy_nav,
            "new": new_nav,
        }
        self._write_full_test_log(
            page_dir,
            page_id,
            "reopen_pair_end",
            {
                "status": status,
                "reason": result.get("reason"),
                "legacy_nav": legacy_nav,
                "new_nav": new_nav,
                "legacy_page_after": self._page_debug_summary(legacy_page),
                "new_page_after": self._page_debug_summary(new_page),
            },
        )
        print(
            f"[{page_id}] REOPEN result: "
            + json.dumps(
                {
                    "status": status,
                    "legacy_status": legacy_nav.get("status"),
                    "legacy_reason": legacy_nav.get("reason"),
                    "new_status": new_nav.get("status"),
                    "new_reason": new_nav.get("reason"),
                },
                ensure_ascii=False,
                default=str,
            )
        )
        return legacy_page, new_page, result

    def _try_history_back_reopen_side(
        self,
        page: Page,
        mapping: Dict[str, Any],
        page_dir: Path,
        capture_name: str,
    ) -> Tuple[Page, Dict[str, Any]]:
        if self._page_is_closed(page):
            return page, {"status": "SKIPPED", "strategy": "history_back", "reason": "page is closed"}
        if self._page_matches_mapping(page, mapping):
            return page, {"status": "PASS", "strategy": "history_back", "reason": "already on target page"}

        attempts: List[Dict[str, Any]] = []
        try:
            frames = list(page.frames)
        except Exception as exc:
            return page, {"status": "SKIPPED", "strategy": "history_back", "reason": f"frames unavailable: {exc}"}

        for index, frame in enumerate(frames):
            try:
                frame_url = frame.url
            except Exception:
                frame_url = ""
            if not frame_url or frame_url == "about:blank":
                continue
            try:
                frame.evaluate("() => history.back()")
                page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout, 8000))
                page.wait_for_timeout(1000)
            except Exception as exc:
                attempts.append({"index": index, "frame_url": frame_url, "status": "ERROR", "reason": str(exc)})
                continue
            matched = self._page_matches_mapping(page, mapping)
            attempts.append({"index": index, "frame_url": frame_url, "status": "PASS" if matched else "NO_MATCH"})
            if matched:
                state = _capture_state(page, page_dir, capture_name)
                return page, {
                    "status": "PASS",
                    "strategy": "history_back",
                    "frame_index": index,
                    "from_url": frame_url,
                    "state": self._state_debug_summary(state),
                    "attempts": attempts,
                }

        return page, {
            "status": "SKIPPED",
            "strategy": "history_back",
            "reason": "history.back did not return to target page",
            "attempts": attempts,
        }

    def _reopen_target_side(
        self,
        page: Page,
        mapping: Dict[str, Any],
        page_dir: Path,
        browser_name: str,
        *,
        side: str,
    ) -> Tuple[Page, Dict[str, Any]]:
        """Re-enter the current target page after an action leaves/closes it.

        Minimal lifecycle recovery policy:
        - If the current page is still one of the route-map step pages, reuse
          the current session and run the route map back to the target page.
        - Otherwise, reopen the login/base entry URL, perform the normal login
          from env/Config credentials if a login form is present, then run the
          route map back to the target page.
        - If no route map is available, keep the previous direct-url fallback.
        """
        page_id = mapping.get("page_id") or "unknown"
        entry_url = self.legacy_base_url if side == "legacy" else self.new_base_url
        route = self.route_map_catalog.find_for_target(page_id, side=side) or self.route_map_catalog.find_for_target(page_id)

        if route:
            page, takeover_nav = self._takeover_recovery_page(page, route)
            route_step = self._page_is_route_map_step(page, route)
            pre_nav: Dict[str, Any]

            if route_step:
                pre_nav = {
                    "status": "PASS",
                    "strategy": "reuse_current_route_step",
                    "current_url": self._safe_page_url(page),
                    "context_takeover": takeover_nav,
                }
            else:
                page, pre_nav = self._login_recovery_page(
                    page,
                    entry_url,
                    page_dir,
                    f"{side}_reopen_login_entry",
                )
                if pre_nav.get("status") != "PASS":
                    pre_nav["reopen_strategy"] = "login_recovery_before_route_map"
                    return page, pre_nav

            navigator = RouteNavigator(
                self.route_map_catalog,
                timeout=self.timeout,
                browser_name=browser_name,
                upload_file=self.upload_file,
            )
            page, nav = navigator.navigate(
                page,
                entry_url=entry_url,
                target_page=page_id,
                capture_dir=page_dir,
                side=side,
            )
            nav["pre_recovery"] = pre_nav
            nav["route_step_detected"] = route_step
            nav["reopen_strategy"] = "route_map_from_current_step" if route_step else "login_recovery_then_route_map"
            return page, nav

        target_url = self._page_url(
            self.legacy_base_url if side == "legacy" else self.new_base_url,
            mapping.get("entry_url") or mapping.get("resolved_entry_url") or page_id,
        )
        page, nav = self._open_or_reset_page(page, target_url, page_dir, f"{side}_reopen_direct")
        nav["reopen_strategy"] = "direct_url"
        if nav.get("status") == "PASS" and not self._page_matches_mapping(page, mapping):
            nav.update(
                {
                    "status": "BLOCKED",
                    "target_reached": False,
                    "reason": f"Reopen direct URL did not reach target page: {page_id}",
                }
            )
        elif nav.get("status") == "PASS":
            nav["target_reached"] = True
        return page, nav

    def _login_recovery_page(
        self,
        page: Page,
        entry_url: str,
        page_dir: Path,
        capture_name: str,
    ) -> Tuple[Page, Dict[str, Any]]:
        page, nav = self._open_or_reset_page(page, entry_url, page_dir, capture_name)
        if nav.get("status") != "PASS":
            return page, nav

        login_result = self._try_login_if_login_form(page)
        nav["login_recovery"] = login_result
        if login_result.get("status") == "BLOCKED":
            nav.update(
                {
                    "status": "BLOCKED",
                    "reason": login_result.get("reason") or "Login recovery failed.",
                }
            )
        return page, nav

    def _takeover_recovery_page(self, page: Page, route: Dict[str, Any]) -> Tuple[Page, Dict[str, Any]]:
        """Prefer an already-open sibling/container page before login recovery."""
        try:
            pages = list(page.context.pages)
        except Exception as exc:
            return page, {"status": "SKIPPED", "reason": f"context pages unavailable: {exc}"}

        candidates = []
        debug_candidates = []
        target_page = (
            route.get("target_page")
            or route.get("target_page_name")
            or (route.get("source_route") or {}).get("target_page")
            or (route.get("source_route") or {}).get("target_page_name")
        )
        target_mapping = {"page_id": target_page} if target_page else {}
        for candidate in pages:
            if self._page_is_closed(candidate):
                debug_candidates.append({"closed": True, "url": "about:closed", "selected": False})
                continue
            try:
                url = candidate.url
            except Exception:
                url = ""
            if url == "about:blank":
                debug_candidates.append({"closed": False, "url": url, "about_blank": True, "selected": False})
                continue
            route_step = self._page_is_route_map_step(candidate, route)
            target_match = bool(target_mapping and self._page_matches_mapping(candidate, target_mapping))
            login_like = bool(re.search(r"login", str(url or ""), re.IGNORECASE))
            negative_evidence = self._page_has_negative_marker(candidate)
            candidates.append((1 if negative_evidence else 0, 0 if target_match else 1, 0 if route_step else 1, 1 if login_like else 0, candidate, url, route_step, target_match, negative_evidence))
            debug_candidates.append(
                {
                    "closed": False,
                    "url": url,
                    "route_step_detected": route_step,
                    "target_page_detected": target_match,
                    "login_like": login_like,
                    "negative_evidence": negative_evidence,
                    "selected": False,
                }
            )

        if not candidates:
            return page, {"status": "SKIPPED", "reason": "no open sibling/container page found", "candidates": debug_candidates}

        candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3], str(item[5] or "")))
        _, _, _, _, selected, url, route_step, target_match, negative_evidence = candidates[0]
        for item in debug_candidates:
            if item.get("url") == url and not item.get("closed"):
                item["selected"] = True
                break
        try:
            selected.bring_to_front()
        except Exception:
            pass
        try:
            selected.wait_for_load_state("domcontentloaded", timeout=min(self.timeout, 5000))
        except Exception:
            pass
        return selected, {
            "status": "PASS",
            "strategy": "context_sibling_takeover",
            "url": url,
            "route_step_detected": route_step,
            "target_page_detected": target_match,
            "negative_evidence": negative_evidence,
            "candidates": debug_candidates,
        }

    def _try_login_if_login_form(self, page: Page) -> Dict[str, Any]:
        password_selectors = [
            "input[type='password']",
            "input[name='password']",
            "input[name='passwd']",
            "input[name='pass']",
        ]
        password_selector = self._first_visible_selector(page, password_selectors, timeout=1500)
        if not password_selector:
            return {"status": "PASS", "reason": "login_form_not_detected"}

        username = self._env_or_config(
            "LOGIN_USERNAME",
            "LOGIN_USER",
            "LOGIN_USER_ID",
            "USERNAME",
            "USER_ID",
        )
        password = self._env_or_config(
            "LOGIN_PASSWORD",
            "LOGIN_PASS",
            "PASSWORD",
        )
        if not username or not password:
            return {
                "status": "BLOCKED",
                "reason": "Login form detected but username/password were not found in environment or Config.",
            }

        user_selector = self._first_visible_selector(
            page,
            [
                "input[name='user']",
                "input[name='username']",
                "input[name='userId']",
                "input[name='userid']",
                "input[name='loginId']",
                "input[name='login_id']",
                "input[type='text']",
            ],
            timeout=1500,
        )
        if not user_selector:
            return {"status": "BLOCKED", "reason": "Login username field was not found."}

        try:
            page.locator(user_selector).first().fill(username, timeout=3000)
            page.locator(password_selector).first().fill(password, timeout=3000)

            submit_selector = self._first_visible_selector(
                page,
                [
                    "input[type='button']",
                    "input[type='submit']",
                    "button[type='submit']",
                    "button",
                ],
                timeout=1000,
            )
            if submit_selector:
                page.locator(submit_selector).first().click(timeout=5000)
            else:
                page.locator(password_selector).first().press("Enter", timeout=3000)

            try:
                page.wait_for_load_state("domcontentloaded", timeout=max(self.timeout, 10000))
            except Exception:
                pass
            return {"status": "PASS", "strategy": "login_form_submit", "url": self._safe_page_url(page)}
        except Exception as exc:
            return {"status": "BLOCKED", "reason": f"Login recovery failed: {exc}"}

    @staticmethod
    def _env_or_config(*names: str) -> str:
        for name in names:
            value = os.environ.get(name)
            if value:
                return value
            value = getattr(Config, name, None)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _first_visible_selector(page: Page, selectors: List[str], *, timeout: int = 1000) -> Optional[str]:
        for selector in selectors:
            try:
                locator = page.locator(selector).first()
                if locator.count() > 0 and locator.is_visible(timeout=timeout):
                    return selector
            except Exception:
                continue
        return None

    def _page_is_route_map_step(self, page: Page, route: Dict[str, Any]) -> bool:
        if self._page_is_closed(page):
            return False
        if self._page_has_negative_marker(page):
            return False

        tokens = self._route_map_step_tokens(route)
        if not tokens:
            return False

        try:
            urls = [page.url]
            urls.extend(frame.url for frame in page.frames)
        except Exception:
            return False

        haystack = "\n".join(str(url or "").replace("\\", "/").lower() for url in urls)
        return any(token in haystack for token in tokens)

    @staticmethod
    def _page_has_negative_marker(page: Page) -> bool:
        if RegressionEngine._page_is_closed(page):
            return False
        try:
            frames = list(page.frames)
        except Exception:
            frames = []
        for frame in frames:
            try:
                if frame.locator("[data-moonlight-negative-state], #moonlight-negative-visual-evidence").count() > 0:
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _route_map_step_tokens(route: Dict[str, Any]) -> List[str]:
        tokens: List[str] = []

        def add_token(value: Any) -> None:
            raw = str(value or "").replace("\\", "/").strip().lower()
            if not raw:
                return
            leaf = raw.rsplit("/", 1)[-1]
            candidates = [raw, leaf]
            stem = re.sub(r"\.(jsp|do|action)$", "", leaf, flags=re.IGNORECASE)
            if stem and stem != leaf:
                candidates.extend([stem, f"{stem}.do", f"{stem}.jsp"])
            for candidate in candidates:
                candidate = candidate.strip("/")
                if len(candidate) >= 3 and candidate not in tokens:
                    tokens.append(candidate)

        def walk(value: Any, parent_key: str = "") -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    key_text = str(key or "").lower()
                    if isinstance(child, str) and any(
                        marker in key_text
                        for marker in ("url", "page", "path", "action", "target", "entry", "href")
                    ):
                        add_token(child)
                    walk(child, key_text)
            elif isinstance(value, list):
                for child in value:
                    walk(child, parent_key)

        walk(route)
        return tokens

    @staticmethod
    def _safe_page_url(page: Page) -> str:
        try:
            return page.url
        except Exception:
            return ""

    @classmethod
    def _action_url_candidates(cls, state: Dict[str, Any], action: Dict[str, Any]) -> List[str]:
        urls: List[str] = []
        for value in (
            action.get("popup_url"),
            action.get("after_url"),
            action.get("current_url"),
            state.get("url"),
        ):
            if value:
                urls.append(str(value))

        target_frame = state.get("target_frame") if isinstance(state.get("target_frame"), dict) else {}
        if target_frame.get("url"):
            urls.append(str(target_frame["url"]))
        for value in action.get("after_frame_urls") or []:
            if value:
                urls.append(str(value))

        unique: List[str] = []
        seen = set()
        for url in urls:
            normalized = str(url or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique.append(normalized)
        return unique

    @classmethod
    def _same_child_target(cls, legacy_urls: List[str], new_urls: List[str]) -> Optional[bool]:
        if not legacy_urls or not new_urls:
            return None
        for legacy_url in legacy_urls:
            legacy_normalized = cls._normalized_url(legacy_url)
            legacy_aliases = page_aliases(legacy_url)
            for new_url in new_urls:
                if legacy_normalized and legacy_normalized == cls._normalized_url(new_url):
                    return True
                if legacy_aliases and legacy_aliases & page_aliases(new_url):
                    return True
        return False

    @classmethod
    def _child_navigation_compare_fields(
        cls,
        legacy_state: Dict[str, Any],
        new_state: Dict[str, Any],
        legacy_action: Dict[str, Any],
        new_action: Dict[str, Any],
    ) -> Dict[str, Any]:
        legacy_urls = cls._action_url_candidates(legacy_state, legacy_action)
        new_urls = cls._action_url_candidates(new_state, new_action)
        same_target = cls._same_child_target(legacy_urls, new_urls)
        legacy_expected = legacy_action.get("expected_navigation_match")
        new_expected = new_action.get("expected_navigation_match")
        expected_match: Optional[bool] = None
        if legacy_expected is not None or new_expected is not None:
            expected_match = bool(legacy_expected) and bool(new_expected)

        if expected_match is not None:
            child_match = expected_match
        else:
            child_match = same_target

        return {
            "child_navigation_match": child_match,
            "child_same_target": same_target,
            "legacy_child_url_candidates": legacy_urls[:20],
            "new_child_url_candidates": new_urls[:20],
            "legacy_expected_navigation_match": legacy_expected,
            "new_expected_navigation_match": new_expected,
            "legacy_expected_navigation_needles": legacy_action.get("expected_navigation_needles") or [],
            "new_expected_navigation_needles": new_action.get("expected_navigation_needles") or [],
        }


    def _compare_state(
        self,
        page_id: str,
        risk: str,
        action: str,
        legacy_state: Dict[str, Any],
        new_state: Dict[str, Any],
        diff_path: Path,
        **extra: Any,
    ) -> Dict[str, Any]:
        action_type = str(extra.get("action_type") or action or "page_snapshot")
        visual = {"status": "SKIPPED", "reason": "Visual comparison is disabled for this action type"}
        normalized_action_type = action_type.lower()
        if normalized_action_type not in NON_VISUAL_ACTION_TYPES:
            ignore_top_px = 86 if (
                str(legacy_state.get("screenshot_scope") or "").startswith("browser_screen_composited")
                and str(new_state.get("screenshot_scope") or "").startswith("browser_screen_composited")
            ) else 0
            visual = compare_visual_screenshot(
                legacy_state.get("screenshot", ""),
                new_state.get("screenshot", ""),
                str(diff_path),
                threshold_percent=self.visual_threshold_percent,
                ignore_top_px=ignore_top_px,
            )
        url_match = self._normalized_url(legacy_state.get("url", "")) == self._normalized_url(new_state.get("url", ""))
        dom_match = str(legacy_state.get("dom", "")) == str(new_state.get("dom", ""))
        child_navigation_compare = {}
        if normalized_action_type in CHILD_NAVIGATION_CASE_TYPES:
            child_navigation_compare = self._child_navigation_compare_fields(
                legacy_state,
                new_state,
                extra.get("legacy_action") or {},
                extra.get("new_action") or {},
            )
            if child_navigation_compare.get("child_navigation_match") is not None:
                url_match = bool(child_navigation_compare["child_navigation_match"])
        status = _judge_compare_status(
            action_type=action_type,
            visual_status=visual.get("status"),
            url_match=url_match,
            legacy_action_status=(extra.get("legacy_action") or {}).get("status"),
            new_action_status=(extra.get("new_action") or {}).get("status"),
            legacy_nav_status=(extra.get("legacy_nav") or {}).get("status"),
            new_nav_status=(extra.get("new_nav") or {}).get("status"),
        )
        if status == "DIFF" and self._looks_like_data_variance(action_type, visual, legacy_state, new_state):
            status = "WARN"
            visual["data_variance_tolerated"] = True
            visual["reason"] = "Visual diff appears to be table/list data variance between environments."

        download_compare = {}
        if normalized_action_type in DOWNLOAD_CASE_TYPES:
            download_compare = self._download_compare_fields(
                extra.get("legacy_action") or {},
                extra.get("new_action") or {},
            )
            if download_compare.get("download_success_match") is False:
                status = "DIFF"
            elif download_compare.get("download_hash_match") is False:
                status = "DIFF"
            elif download_compare.get("download_hash_match") is None and download_compare.get("download_size_match") is False:
                status = "DIFF"
            elif download_compare.get("download_extension_match") is False:
                status = "DIFF"

        dialog_compare = {}
        if normalized_action_type in BROWSER_DIALOG_CASE_TYPES:
            dialog_compare = self._browser_dialog_compare_fields(
                extra.get("legacy_action") or {},
                extra.get("new_action") or {},
            )
            if dialog_compare.get("browser_dialog_match") is False:
                status = "DIFF"

        print_compare = {}
        if normalized_action_type in PRINT_CASE_TYPES:
            print_compare = self._print_compare_fields(
                extra.get("legacy_action") or {},
                extra.get("new_action") or {},
            )
            if print_compare.get("print_invocation_match") is False:
                status = "DIFF"

        pdf_compare = {}
        if normalized_action_type in PDF_SAVE_CASE_TYPES:
            pdf_compare = self._pdf_save_compare_fields(
                extra.get("legacy_action") or {},
                extra.get("new_action") or {},
            )
            if pdf_compare.get("pdf_save_success_match") is False:
                status = "DIFF"

        if normalized_action_type in CHILD_NAVIGATION_CASE_TYPES:
            if child_navigation_compare.get("child_navigation_match") is False:
                status = "DIFF"
            elif child_navigation_compare.get("child_navigation_match") is None and status == "PASS":
                status = "WARN"

        return {
            "page_id": page_id,
            "risk": risk,
            "action": action,
            "status": status,
            "url_match": url_match,
            "dom_match": dom_match,
            "visual": visual,
            "legacy_url": legacy_state.get("url"),
            "new_url": new_state.get("url"),
            "legacy_screenshot": legacy_state.get("screenshot"),
            "new_screenshot": new_state.get("screenshot"),
            "diff_screenshot": visual.get("diff_screenshot"),
            "legacy_frame": legacy_state.get("target_frame"),
            "new_frame": new_state.get("target_frame"),
            "frame_candidates": {
                "legacy": legacy_state.get("frame_candidates", []),
                "new": new_state.get("frame_candidates", []),
            },
            **download_compare,
            **dialog_compare,
            **print_compare,
            **pdf_compare,
            **child_navigation_compare,
            **extra,
        }

    @staticmethod
    def _looks_like_browser_temp_download_name(filename: Any) -> bool:
        name = Path(str(filename or "").replace("\\", "/")).name
        return bool(name and not Path(name).suffix and UUID_DOWNLOAD_NAME_RE.match(name))

    @staticmethod
    def _download_comparable_filename(value: Any) -> str:
        if not value:
            return ""
        filename = Path(str(value).replace("\\", "/")).name
        if RegressionEngine._looks_like_browser_temp_download_name(filename):
            return ""
        # Report archive paths may include the test prefix before a browser
        # generated UUID. That suffix is still not a business filename.
        if "__" in filename:
            tail = filename.rsplit("__", 1)[-1]
            if RegressionEngine._looks_like_browser_temp_download_name(tail):
                return ""
        return filename

    @staticmethod
    def _download_action_filename(action: Dict[str, Any]) -> str:
        for key in ("download_filename", "download_suggested_filename", "suggested_filename", "saved_filename"):
            value = action.get(key)
            if value:
                filename = RegressionEngine._download_comparable_filename(value)
                if filename:
                    return filename
        path = action.get("download_path")
        if path:
            return RegressionEngine._download_comparable_filename(path)
        return ""

    @staticmethod
    def _download_action_path(action: Dict[str, Any]) -> str:
        for key in ("download_path", "download_saved_path", "download_archive_path", "download_original_path"):
            value = action.get(key)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _download_action_size(action: Dict[str, Any]) -> Optional[int]:
        for key in ("download_size", "saved_size", "file_size"):
            value = action.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        path = RegressionEngine._download_action_path(action)
        if path:
            try:
                return Path(path).stat().st_size
            except OSError:
                return None
        return None

    @staticmethod
    def _download_action_sha256(action: Dict[str, Any]) -> str:
        value = action.get("download_sha256") or action.get("sha256") or action.get("file_sha256")
        if value:
            return str(value)
        path = RegressionEngine._download_action_path(action)
        if not path:
            return ""
        try:
            digest = hashlib.sha256()
            with Path(path).open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            return ""

    @staticmethod
    def _download_action_extension(filename: str) -> str:
        suffix = Path(str(filename or "")).suffix.lower()
        return suffix if suffix else ""

    @classmethod
    def _download_action_success(cls, action: Dict[str, Any]) -> bool:
        if action.get("status") != "PASS":
            return False
        has_download_evidence = bool(
            cls._download_action_path(action)
            or cls._download_action_filename(action)
            or action.get("download_source")
        )
        if not has_download_evidence:
            return False
        size = cls._download_action_size(action)
        if size is not None and size <= 0:
            return False
        return True

    @classmethod
    def _download_compare_fields(cls, legacy_action: Dict[str, Any], new_action: Dict[str, Any]) -> Dict[str, Any]:
        legacy_filename = cls._download_action_filename(legacy_action)
        new_filename = cls._download_action_filename(new_action)
        legacy_success = cls._download_action_success(legacy_action)
        new_success = cls._download_action_success(new_action)
        legacy_size = cls._download_action_size(legacy_action)
        new_size = cls._download_action_size(new_action)
        legacy_sha256 = cls._download_action_sha256(legacy_action)
        new_sha256 = cls._download_action_sha256(new_action)
        legacy_extension = cls._download_action_extension(legacy_filename)
        new_extension = cls._download_action_extension(new_filename)
        filename_match: Optional[bool]
        if legacy_filename and new_filename:
            filename_match = legacy_filename == new_filename
        else:
            filename_match = None
        size_match: Optional[bool]
        if legacy_size is not None and new_size is not None:
            size_match = legacy_size == new_size
        else:
            size_match = None
        hash_match: Optional[bool]
        if legacy_sha256 and new_sha256:
            hash_match = legacy_sha256 == new_sha256
        else:
            hash_match = None
        extension_match: Optional[bool]
        if legacy_extension and new_extension:
            extension_match = legacy_extension == new_extension
        else:
            extension_match = None
        return {
            "download_success_match": legacy_success == new_success,
            "legacy_download_success": legacy_success,
            "new_download_success": new_success,
            "download_filename_match": filename_match,
            "download_size_match": size_match,
            "download_hash_match": hash_match,
            "download_extension_match": extension_match,
            "legacy_download_filename": legacy_filename,
            "new_download_filename": new_filename,
            "legacy_download_suggested_filename": legacy_action.get("download_suggested_filename"),
            "new_download_suggested_filename": new_action.get("download_suggested_filename"),
            "legacy_download_path": cls._download_action_path(legacy_action),
            "new_download_path": cls._download_action_path(new_action),
                "legacy_download_original_path": legacy_action.get("download_original_path"),
                "new_download_original_path": new_action.get("download_original_path"),
                "legacy_download_saved_path": legacy_action.get("download_saved_path"),
                "new_download_saved_path": new_action.get("download_saved_path"),
                "legacy_download_archive_path": legacy_action.get("download_archive_path"),
                "new_download_archive_path": new_action.get("download_archive_path"),
                "legacy_download_size": legacy_size,
                "new_download_size": new_size,
            "legacy_download_sha256": legacy_sha256,
            "new_download_sha256": new_sha256,
            "legacy_download_source": legacy_action.get("download_source"),
            "new_download_source": new_action.get("download_source"),
        }

    @staticmethod
    def _pdf_save_success(action: Dict[str, Any]) -> bool:
        try:
            size = int(action.get("pdf_size") or 0)
        except (TypeError, ValueError):
            size = 0
        return bool(action.get("status") == "PASS" and action.get("pdf_path") and size > 0)

    @classmethod
    def _pdf_save_compare_fields(cls, legacy_action: Dict[str, Any], new_action: Dict[str, Any]) -> Dict[str, Any]:
        legacy_success = cls._pdf_save_success(legacy_action)
        new_success = cls._pdf_save_success(new_action)
        return {
            "pdf_save_success_match": legacy_success == new_success,
            "legacy_pdf_saved": legacy_success,
            "new_pdf_saved": new_success,
            "legacy_pdf_path": legacy_action.get("pdf_path"),
            "new_pdf_path": new_action.get("pdf_path"),
            "legacy_pdf_size": legacy_action.get("pdf_size"),
            "new_pdf_size": new_action.get("pdf_size"),
            "legacy_pdf_backend": legacy_action.get("pdf_backend"),
            "new_pdf_backend": new_action.get("pdf_backend"),
        }

    @staticmethod
    def _dialog_signature(action: Dict[str, Any]) -> str:
        dialogs = action.get("dialogs") or []
        if not isinstance(dialogs, list) or not dialogs:
            return ""
        first = dialogs[0] if isinstance(dialogs[0], dict) else {}
        dialog_type = str(first.get("type") or "").strip().lower()
        message = re.sub(r"\s+", " ", str(first.get("message") or "")).strip()
        return f"{dialog_type}:{message}"

    @classmethod
    def _browser_dialog_compare_fields(cls, legacy_action: Dict[str, Any], new_action: Dict[str, Any]) -> Dict[str, Any]:
        legacy_signature = cls._dialog_signature(legacy_action)
        new_signature = cls._dialog_signature(new_action)
        if legacy_signature and new_signature:
            dialog_match: Optional[bool] = legacy_signature == new_signature
        else:
            dialog_match = None if not legacy_signature and not new_signature else False
        return {
            "browser_dialog_match": dialog_match,
            "legacy_browser_dialog": legacy_signature,
            "new_browser_dialog": new_signature,
        }

    @staticmethod
    def _print_compare_fields(legacy_action: Dict[str, Any], new_action: Dict[str, Any]) -> Dict[str, Any]:
        legacy_invoked = bool(legacy_action.get("print_invoked"))
        new_invoked = bool(new_action.get("print_invoked"))
        return {
            "print_invocation_match": legacy_invoked == new_invoked,
            "legacy_print_invoked": legacy_invoked,
            "new_print_invoked": new_invoked,
            "legacy_print_events": legacy_action.get("print_events") or [],
            "new_print_events": new_action.get("print_events") or [],
        }

    @staticmethod
    def _looks_like_data_variance(
        action_type: str,
        visual: Dict[str, Any],
        legacy_state: Dict[str, Any],
        new_state: Dict[str, Any],
    ) -> bool:
        if str(visual.get("status") or "").upper() != "DIFF":
            return False
        try:
            diff_percent = float(visual.get("diff_percent") or 0)
        except (TypeError, ValueError):
            diff_percent = 0
        if diff_percent <= 0 or diff_percent > 10:
            return False

        normalized_action = str(action_type or "").lower()
        if normalized_action not in {"page_snapshot", "snapshot", "initial_display", "result_table_verify"}:
            return False

        legacy_lines = RegressionEngine._stable_text_lines(legacy_state.get("text", ""))
        new_lines = RegressionEngine._stable_text_lines(new_state.get("text", ""))
        if not legacy_lines or not new_lines:
            return False
        shared = set(legacy_lines) & set(new_lines)
        combined = " ".join(shared).lower()
        table_markers = ("一覧", "検索結果", "結果", "削除", "アップロード日時", "ファイル", "table", "list")
        return len(shared) >= 2 and any(marker.lower() in combined for marker in table_markers)

    @staticmethod
    def _stable_text_lines(value: Any) -> List[str]:
        lines: List[str] = []
        for raw_line in str(value or "").splitlines():
            line = " ".join(raw_line.split())
            if not line:
                continue
            # Drop row values that are commonly environment data, while keeping
            # titles, headers, and button labels useful for structure checks.
            if re.search(r"\d{4}年\d{1,2}月\d{1,2}日|\d{4}[-/]\d{1,2}[-/]\d{1,2}", line):
                continue
            if re.search(r"\.(csv|tsv|xls|xlsx|pdf|txt)\b", line, re.IGNORECASE):
                continue
            if len(line) > 120:
                continue
            lines.append(line)
        return lines

    def render_report(self, results: List[Dict[str, Any]]) -> Path:
        report_path, report_dir, report_page = self._report_location(results)
        browser_name = self.current_browser_name or "-"
        counts = Counter(item["status"] for item in results)
        rows = "\n".join(self._render_result(item, report_dir, index) for index, item in enumerate(results, start=1))
        report_dir.mkdir(parents=True, exist_ok=True)
        results_json_path = report_dir / "regression_results.json"
        results_json_path.write_text(
            json.dumps(self._json_safe(results), ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        report_path.write_text(
            f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>Moonlight Regression Report - {html.escape(report_page)}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Arial, sans-serif; color: #17202a; background: #f5f7fb; }}
    header {{ padding: 24px 32px; background: #17202a; color: white; }}
    h1 {{ margin: 0 0 12px; font-size: 26px; letter-spacing: 0; }}
    .subtitle {{ margin: 0 0 16px; color: #d6eaf8; font-size: 13px; overflow-wrap: anywhere; }}
    .summary {{ display: flex; gap: 12px; flex-wrap: wrap; }}
    .summary-details {{ margin-top: 16px; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .summary-card {{ background: #273746; border-radius: 8px; padding: 12px; }}
    .summary-card h2 {{ margin: 0 0 8px; font-size: 13px; color: #d6eaf8; }}
    .summary-card p {{ margin: 4px 0; font-size: 12px; color: #f8f9f9; overflow-wrap: anywhere; }}
    .summary-card .num {{ font-size: 20px; font-weight: 800; }}
    .debug-links {{ margin-top: 14px; display: flex; gap: 10px; flex-wrap: wrap; }}
    .debug-links a {{ color: #fff; background: #34495e; text-decoration: none; padding: 7px 10px; border-radius: 4px; font-size: 12px; font-weight: 700; }}
    .back-to-top {{ position: fixed; right: 20px; bottom: 18px; z-index: 10; color: #fff; background: #17202a; text-decoration: none; padding: 9px 12px; border-radius: 6px; font-size: 12px; font-weight: 800; box-shadow: 0 4px 12px rgba(23,32,42,0.22); }}
    .case-link {{ color: #1f618d; font-weight: 700; text-decoration: none; }}
    .case-link:hover {{ text-decoration: underline; }}
    .pill {{ padding: 8px 12px; border-radius: 6px; background: #273746; font-weight: 700; }}
    main {{ padding: 24px 32px; }}
    .case {{ margin-bottom: 20px; border: 1px solid #d9e0ea; border-radius: 8px; background: white; overflow: hidden; }}
    .case-head {{ display: flex; align-items: center; gap: 12px; padding: 12px 16px; border-bottom: 1px solid #e7ecf3; flex-wrap: wrap; }}
    .case-title {{ display: flex; align-items: center; gap: 10px; min-width: min(100%, 420px); }}
    .case-title strong {{ overflow-wrap: anywhere; }}
    .status {{ padding: 4px 8px; border-radius: 4px; color: white; font-weight: 700; font-size: 12px; }}
    .PASS {{ background: #1e8449; }} .WARN {{ background: #b7950b; }} .DIFF {{ background: #b7950b; }} .BLOCKED {{ background: #922b21; }} .ERROR {{ background: #7b241c; }} .EXCLUDED {{ background: #6c757d; }} .NOTRUN {{ background: #85929e; }}
    .meta {{ color: #52616f; font-size: 13px; overflow-wrap: anywhere; }}
    .grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; padding: 16px; }}
    .db-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    figure {{ margin: 0; }}
    figcaption {{ margin-bottom: 6px; font-size: 12px; color: #52616f; font-weight: 700; }}
    img {{ width: 100%; max-height: 520px; object-fit: contain; border: 1px solid #d9e0ea; background: #fff; }}
    .details {{ padding: 0 16px 16px; font-size: 13px; color: #34495e; }}
    .detail-grid {{ display: grid; grid-template-columns: 160px minmax(0, 1fr); gap: 6px 12px; margin-bottom: 12px; }}
    .detail-grid b {{ color: #17202a; }}
    table {{ width: 100%; border-collapse: collapse; }}
    td {{ border-top: 1px solid #e7ecf3; padding: 8px 10px; }}
    details {{ margin-top: 10px; }}
    summary {{ cursor: pointer; font-weight: 700; color: #17202a; }}
    code {{ font-family: Consolas, monospace; font-size: 12px; white-space: pre-wrap; }}
    @media (max-width: 900px) {{ .grid, .summary-details {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <header id="top">
    <h1>Moonlight Legacy/New Regression Report - {html.escape(report_page)}</h1>
    <p class="subtitle">Page: {html.escape(report_page)} / Browser: {html.escape(browser_name)} / Report: {html.escape(str(report_path))}</p>
    <div class="summary">
      <span class="pill">Total: {len(results)}</span>
      <span class="pill">PASS: {counts.get('PASS', 0)}</span>
      <span class="pill">DIFF: {counts.get('DIFF', 0)}</span>
      <span class="pill">BLOCKED: {counts.get('BLOCKED', 0)}</span>
      <span class="pill">ERROR: {counts.get('ERROR', 0)}</span>
    </div>
    {self._render_debug_links(report_dir)}
    {self._render_summary_details(results, counts)}
  </header>
  <main>{self._render_coverage_matrix(results)}{rows or '<p>No results.</p>'}</main>
  <a class="back-to-top" href="#top">Back to top</a>
</body>
</html>
""",
            encoding="utf-8",
        )
        return report_path

    def _report_location(self, results: List[Dict[str, Any]]) -> Tuple[Path, Path, str]:
        pages = sorted({str(item.get("page_id") or "-") for item in results})
        report_page = pages[0] if len(pages) == 1 else f"{len(pages)} pages"
        report_dir = self.output_dir

        if len(pages) == 1:
            asset_dir = self._first_asset_dir(results)
            if asset_dir is not None:
                report_dir = asset_dir
            else:
                report_dir = self.output_dir / f"0001_{self._safe_name(report_page)}"

        return report_dir / "regression_report.html", report_dir, report_page

    @staticmethod
    def _first_asset_dir(results: List[Dict[str, Any]]) -> Optional[Path]:
        for item in results:
            for key in (
                "legacy_screenshot",
                "new_screenshot",
                "diff_screenshot",
                "legacy_before_screenshot",
                "legacy_after_screenshot",
                "legacy_diff_screenshot",
                "new_before_screenshot",
                "new_after_screenshot",
                "new_diff_screenshot",
            ):
                value = item.get(key)
                if value:
                    return Path(value).resolve().parent
        return None

    def _render_result(self, item: Dict[str, Any], report_dir: Path, index: int = 0) -> str:
        if item.get("comparison_mode") == "database_operation_before_after":
            return self._render_database_operation_result(item, report_dir, index)

        visual = item.get("visual") or {}
        diff_percent = visual.get("diff_percent")
        diff_text = "-" if diff_percent is None else f"{diff_percent:.4f}%"
        action_type = item.get("action_type") or self._action_type_from_payload(item)
        reason = item.get("reason") or self._blocked_reason(item) or "-"
        anchor_id = self._report_case_anchor(item, index)
        return f"""
<section class="case" id="{html.escape(anchor_id)}">
  <div class="case-head">
    <div class="case-title">
      <span class="status {html.escape(item.get('status', 'DIFF'))}">{html.escape(item.get('status', 'DIFF'))}</span>
      <strong>{html.escape(str(item.get('page_id')))}</strong>
    </div>
    <span class="meta">risk={html.escape(str(item.get('risk')))} / action={html.escape(str(item.get('action')))} / diff={diff_text}</span>
  </div>
  <div class="grid">
    {self._figure('Legacy', item.get('legacy_screenshot'), report_dir)}
    {self._figure('New', item.get('new_screenshot'), report_dir)}
    {self._figure('Diff', item.get('diff_screenshot'), report_dir)}
    {self._render_console_figures(item, report_dir)}
  </div>
  <div class="details">
    <div class="detail-grid">
      <b>URL match</b><span>{item.get('url_match')}</span>
      <b>Action type</b><span>{html.escape(str(action_type or '-'))}</span>
      <b>Legacy</b><span>{html.escape(str(item.get('legacy_url') or '-'))}</span>
      <b>New</b><span>{html.escape(str(item.get('new_url') or '-'))}</span>
      <b>Upload file</b><span>{html.escape(str(item.get('upload_file') or '-'))}</span>
      <b>Submit locator</b><span>{html.escape(str(item.get('submit_locator') or '-'))}</span>
      {self._render_download_detail_rows(item)}
      {self._render_pdf_save_detail_rows(item)}
      <b>After URL</b><span>{html.escape(str(item.get('legacy_after_url') or '-'))} / {html.escape(str(item.get('new_after_url') or '-'))}</span>
      <b>Runtime</b><span>navigation={item.get('navigation_detected')} / popup={item.get('popup_detected')} / frame={item.get('frame_changed')} / validation_only={item.get('validation_only')}</span>
      {self._render_console_detail_rows(item)}
      <b>Reason</b><span>{html.escape(str(reason))}</span>
    </div>
    {self._render_diagnostics(item)}
  </div>
</section>"""

    def _render_database_operation_result(self, item: Dict[str, Any], report_dir: Path, index: int = 0) -> str:
        legacy_delta = item.get("legacy_delta") or {}
        new_delta = item.get("new_delta") or {}
        legacy_diff = legacy_delta.get("diff_percent")
        new_diff = new_delta.get("diff_percent")
        legacy_diff_text = "-" if legacy_diff is None else f"{legacy_diff:.4f}%"
        new_diff_text = "-" if new_diff is None else f"{new_diff:.4f}%"
        action_type = item.get("action_type") or self._action_type_from_payload(item)
        reason = item.get("reason") or self._blocked_reason(item) or "-"
        operation = item.get("database_operation") or "-"
        anchor_id = self._report_case_anchor(item, index)
        return f"""
<section class="case" id="{html.escape(anchor_id)}">
  <div class="case-head">
    <div class="case-title">
      <span class="status {html.escape(item.get('status', 'DIFF'))}">{html.escape(item.get('status', 'DIFF'))}</span>
      <strong>{html.escape(str(item.get('page_id')))}</strong>
    </div>
    <span class="meta">risk={html.escape(str(item.get('risk')))} / action={html.escape(str(item.get('action')))} / DB={html.escape(str(operation))} / legacy Δ={legacy_diff_text} / new Δ={new_diff_text}</span>
  </div>
  <div class="grid db-grid">
    {self._figure('Legacy Before', item.get('legacy_before_screenshot'), report_dir)}
    {self._figure('Legacy After', item.get('legacy_after_screenshot'), report_dir)}
    {self._figure('Legacy Before/After Diff', item.get('legacy_diff_screenshot'), report_dir)}
    {self._figure('New Before', item.get('new_before_screenshot'), report_dir)}
    {self._figure('New After', item.get('new_after_screenshot'), report_dir)}
    {self._figure('New Before/After Diff', item.get('new_diff_screenshot'), report_dir)}
    {self._render_console_figures(item, report_dir)}
  </div>
  <div class="details">
    <div class="detail-grid">
      <b>Compare mode</b><span>database_operation_before_after</span>
      <b>DB operation</b><span>{html.escape(str(operation))}</span>
      <b>Transition match</b><span>{item.get('url_match')}</span>
      <b>Action type</b><span>{html.escape(str(action_type or '-'))}</span>
      <b>Legacy delta</b><span>{html.escape(str(legacy_delta.get('status') or '-'))} / {legacy_diff_text}</span>
      <b>New delta</b><span>{html.escape(str(new_delta.get('status') or '-'))} / {new_diff_text}</span>
      <b>Legacy before</b><span>{html.escape(str(item.get('legacy_before_url') or '-'))}</span>
      <b>Legacy after</b><span>{html.escape(str(item.get('legacy_after_url') or '-'))}</span>
      <b>New before</b><span>{html.escape(str(item.get('new_before_url') or '-'))}</span>
      <b>New after</b><span>{html.escape(str(item.get('new_after_url') or '-'))}</span>
      {self._render_console_detail_rows(item)}
      <b>Reason</b><span>{html.escape(str(reason))}</span>
    </div>
    {self._render_diagnostics(item)}
  </div>
</section>"""

    def _figure(self, label: str, path: Optional[str], report_dir: Path) -> str:
        if not path:
            return f"<figure><figcaption>{html.escape(label)}</figcaption><div class=\"meta\">No screenshot</div></figure>"
        try:
            rel = Path(path).resolve().relative_to(report_dir.resolve())
        except ValueError:
            rel = Path(path).resolve()
        return f'<figure><figcaption>{html.escape(label)}</figcaption><img src="{html.escape(rel.as_posix())}" alt="{html.escape(label)}"></figure>'

    @staticmethod
    def _render_debug_links(report_dir: Path) -> str:
        links = []
        for filename, label in (
            ("full_test_log.jsonl", "Full Test Log"),
            ("regression_results.json", "Result JSON"),
        ):
            path = report_dir / filename
            if path.exists():
                links.append(f'<a href="{html.escape(filename)}" target="_blank">{html.escape(label)}</a>')
        if not links:
            return ""
        return f'<div class="debug-links">{"".join(links)}</div>'

    @staticmethod
    def _render_download_detail_rows(item: Dict[str, Any]) -> str:
        match_fields = {
            "download_success_match",
            "download_filename_match",
            "download_size_match",
            "download_hash_match",
            "download_extension_match",
        }
        has_download = any(
            item.get(key) is not None if key in match_fields else bool(item.get(key))
            for key in (
                "download_success_match",
                "download_filename_match",
                "download_size_match",
                "download_hash_match",
                "download_extension_match",
                "legacy_download_filename",
                "new_download_filename",
                "legacy_download_path",
                "new_download_path",
                "legacy_download_archive_path",
                "new_download_archive_path",
            )
        )
        if not has_download:
            return ""
        legacy_hash = str(item.get("legacy_download_sha256") or "-")
        new_hash = str(item.get("new_download_sha256") or "-")
        if len(legacy_hash) > 16:
            legacy_hash = legacy_hash[:16] + "..."
        if len(new_hash) > 16:
            new_hash = new_hash[:16] + "..."
        return (
            f"<b>Download success match</b><span>{html.escape(str(item.get('download_success_match') if item.get('download_success_match') is not None else '-'))}</span>"
            f"<b>Download filename match</b><span>{html.escape(str(item.get('download_filename_match') if item.get('download_filename_match') is not None else '-'))}</span>"
            f"<b>Download size match</b><span>{html.escape(str(item.get('download_size_match') if item.get('download_size_match') is not None else '-'))}</span>"
            f"<b>Download hash match</b><span>{html.escape(str(item.get('download_hash_match') if item.get('download_hash_match') is not None else '-'))}</span>"
            f"<b>Legacy download file</b><span>{html.escape(str(item.get('legacy_download_filename') or '-'))}</span>"
            f"<b>New download file</b><span>{html.escape(str(item.get('new_download_filename') or '-'))}</span>"
            f"<b>Download size</b><span>{html.escape(str(item.get('legacy_download_size') if item.get('legacy_download_size') is not None else '-'))} / {html.escape(str(item.get('new_download_size') if item.get('new_download_size') is not None else '-'))}</span>"
            f"<b>Download sha256</b><span>{html.escape(legacy_hash)} / {html.escape(new_hash)}</span>"
            f"<b>Download path</b><span>{html.escape(str(item.get('legacy_download_path') or '-'))} / {html.escape(str(item.get('new_download_path') or '-'))}</span>"
            f"<b>Archive path</b><span>{html.escape(str(item.get('legacy_download_archive_path') or '-'))} / {html.escape(str(item.get('new_download_archive_path') or '-'))}</span>"
            f"<b>Original download path</b><span>{html.escape(str(item.get('legacy_download_original_path') or '-'))} / {html.escape(str(item.get('new_download_original_path') or '-'))}</span>"
        )

    @staticmethod
    def _render_pdf_save_detail_rows(item: Dict[str, Any]) -> str:
        has_pdf_save = any(
            item.get(key) is not None
            for key in (
                "pdf_save_success_match",
                "legacy_pdf_saved",
                "new_pdf_saved",
                "legacy_pdf_path",
                "new_pdf_path",
                "legacy_pdf_size",
                "new_pdf_size",
                "legacy_pdf_backend",
                "new_pdf_backend",
            )
        )
        if not has_pdf_save:
            return ""
        legacy_pdf = f"{item.get('legacy_pdf_path') or '-'} ({item.get('legacy_pdf_size') or '-'} bytes)"
        new_pdf = f"{item.get('new_pdf_path') or '-'} ({item.get('new_pdf_size') or '-'} bytes)"
        return (
            f"<b>PDF save match</b><span>{html.escape(str(item.get('pdf_save_success_match') if item.get('pdf_save_success_match') is not None else '-'))}</span>"
            f"<b>Legacy PDF saved</b><span>{html.escape(str(item.get('legacy_pdf_saved') if item.get('legacy_pdf_saved') is not None else '-'))}</span>"
            f"<b>New PDF saved</b><span>{html.escape(str(item.get('new_pdf_saved') if item.get('new_pdf_saved') is not None else '-'))}</span>"
            f"<b>Legacy PDF</b><span>{html.escape(legacy_pdf)}</span>"
            f"<b>New PDF</b><span>{html.escape(new_pdf)}</span>"
            f"<b>PDF backend</b><span>{html.escape(str(item.get('legacy_pdf_backend') or '-'))} / {html.escape(str(item.get('new_pdf_backend') or '-'))}</span>"
        )

    def _render_console_figures(self, item: Dict[str, Any], report_dir: Path) -> str:
        if not item.get("legacy_console_screenshot") and not item.get("new_console_screenshot"):
            return ""
        return (
            self._figure("Legacy Console", item.get("legacy_console_screenshot"), report_dir)
            + self._figure("New Console", item.get("new_console_screenshot"), report_dir)
        )

    @staticmethod
    def _render_console_detail_rows(item: Dict[str, Any]) -> str:
        has_console = any(
            item.get(key) is not None
            for key in (
                "legacy_console_error_count",
                "new_console_error_count",
                "legacy_http_error_count",
                "new_http_error_count",
                "legacy_request_failed_count",
                "new_request_failed_count",
            )
        )
        if not has_console:
            return ""
        return (
            f"<b>Console errors</b><span>Legacy={html.escape(str(item.get('legacy_console_error_count') if item.get('legacy_console_error_count') is not None else '-'))} / "
            f"New={html.escape(str(item.get('new_console_error_count') if item.get('new_console_error_count') is not None else '-'))}</span>"
            f"<b>HTTP errors</b><span>Legacy={html.escape(str(item.get('legacy_http_error_count') if item.get('legacy_http_error_count') is not None else '-'))} / "
            f"New={html.escape(str(item.get('new_http_error_count') if item.get('new_http_error_count') is not None else '-'))}</span>"
            f"<b>Request failed</b><span>Legacy={html.escape(str(item.get('legacy_request_failed_count') if item.get('legacy_request_failed_count') is not None else '-'))} / "
            f"New={html.escape(str(item.get('new_request_failed_count') if item.get('new_request_failed_count') is not None else '-'))}</span>"
        )

    @staticmethod
    def _blocked_reason(item: Dict[str, Any]) -> Optional[str]:
        for key in ("legacy_action", "new_action", "legacy_nav", "new_nav"):
            value = item.get(key) or {}
            if value.get("reason"):
                return value.get("reason")
        return None

    @staticmethod
    def _action_type_from_payload(item: Dict[str, Any]) -> Optional[str]:
        for key in ("legacy_action", "new_action"):
            value = item.get(key) or {}
            if value.get("semantic_action"):
                return value.get("semantic_action")
        return None

    def _render_summary_details(self, results: List[Dict[str, Any]], counts: Counter) -> str:
        """Render compact diagnostic information in the top summary area."""
        total = len(results)
        pages = sorted({str(item.get("page_id") or "-") for item in results})
        action_counts = Counter(str(item.get("action_type") or self._action_type_from_payload(item) or "-") for item in results)
        risk_counts = Counter(str(item.get("risk") or "-") for item in results)
        visual_counts = Counter(str((item.get("visual") or {}).get("status") or "-") for item in results)
        url_true = sum(1 for item in results if item.get("url_match") is True)
        url_false = sum(1 for item in results if item.get("url_match") is False)
        diffs = [
            float((item.get("visual") or {}).get("diff_percent"))
            for item in results
            if isinstance((item.get("visual") or {}).get("diff_percent"), (int, float))
        ]
        avg_diff = sum(diffs) / len(diffs) if diffs else 0.0
        max_diff = max(diffs) if diffs else 0.0
        hotspot_counts = Counter(
            str(item.get("page_id") or "-")
            for item in results
            if item.get("status") not in {"PASS"}
        )
        hotspots = ", ".join(f"{page}:{count}" for page, count in hotspot_counts.most_common(5)) or "-"

        def fmt_counter(counter: Counter) -> str:
            return ", ".join(f"{html.escape(str(k))}:{v}" for k, v in counter.most_common()) or "-"

        return f"""
    <div class="summary-details">
      <div class="summary-card">
        <h2>Execution</h2>
        <p><span class="num">{total}</span> results / {len(pages)} page(s)</p>
        <p>Risk: {fmt_counter(risk_counts)}</p>
      </div>
      <div class="summary-card">
        <h2>Status</h2>
        <p>PASS: {counts.get('PASS', 0)} / WARN: {counts.get('WARN', 0)}</p>
        <p>DIFF: {counts.get('DIFF', 0)} / BLOCKED: {counts.get('BLOCKED', 0)} / ERROR: {counts.get('ERROR', 0)}</p>
      </div>
      <div class="summary-card">
        <h2>Comparison</h2>
        <p>URL match: {url_true} true / {url_false} false</p>
        <p>Visual: {fmt_counter(visual_counts)}</p>
        <p>Visual diff avg/max: {avg_diff:.4f}% / {max_diff:.4f}%</p>
      </div>
      <div class="summary-card">
        <h2>Actions / Hotspots</h2>
        <p>Actions: {fmt_counter(action_counts)}</p>
        <p>Problem pages: {html.escape(hotspots)}</p>
      </div>
    </div>"""

    @staticmethod
    def _report_anchor_slug(value: Any) -> str:
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "case")).strip("-").lower()
        return slug[:80] or "case"

    @classmethod
    def _report_case_anchor(cls, item: Dict[str, Any], index: int) -> str:
        basis = item.get("case_id") or item.get("action") or item.get("test_title") or item.get("page_id") or "case"
        return f"case-{max(0, int(index)):04d}-{cls._report_anchor_slug(basis)}"

    @staticmethod
    def _report_status_class(status: Any) -> str:
        normalized = re.sub(r"[^A-Za-z0-9]+", "", str(status or "NOTRUN")).upper()
        return normalized or "NOTRUN"

    @classmethod
    def _render_status_badge(cls, status: Any) -> str:
        text = str(status or "NOT RUN")
        return f'<span class="status {html.escape(cls._report_status_class(text))}">{html.escape(text)}</span>'

    def _coverage_result_index(self, results: List[Dict[str, Any]]) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
        lookup: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for index, item in enumerate(results, start=1):
            page = self._target_page_name(item.get("page_id"))
            payload = {
                "status": item.get("status") or "-",
                "anchor": self._report_case_anchor(item, index),
            }
            case_id = str(item.get("case_id") or "").strip()
            if case_id:
                lookup.setdefault(("case_id", page, case_id), payload)
            for title in (item.get("test_title"), item.get("action")):
                title_text = str(title or "").strip()
                if title_text:
                    lookup.setdefault(("title", page, title_text), payload)
        return lookup

    @staticmethod
    def _coverage_row_status(row: Dict[str, Any], matched: Optional[Dict[str, Any]]) -> str:
        if matched:
            return str(matched.get("status") or "-")
        if row.get("excluded_reason"):
            return "EXCLUDED"
        return "NOT RUN"

    def _render_coverage_matrix(self, results: List[Dict[str, Any]]) -> str:
        pages = sorted({self._target_page_name(item.get("page_id")) for item in results if item.get("page_id")})
        checklist_rows: List[Dict[str, Any]] = []
        for page in pages:
            for source_row in self._checklist_case_rows.get(page) or []:
                row = dict(source_row)
                row.setdefault("page_id", page)
                checklist_rows.append(row)

        if checklist_rows:
            result_index = self._coverage_result_index(results)
            body = "\n".join(
                self._render_coverage_row(row, result_index)
                for row in checklist_rows
            )
        else:
            body = '<tr><td colspan="6">No checklist cases were loaded for this report.</td></tr>'

        return f"""
<section class="case">
  <div class="case-head"><strong>Checklist Cases</strong></div>
  <div class="details">
    <table>
      <thead>
        <tr>
          <td><b>case_id</b></td>
          <td><b>test_title</b></td>
          <td><b>status</b></td>
          <td><b>automation_mode</b></td>
          <td><b>destructive</b></td>
          <td><b>excluded_reason</b></td>
        </tr>
      </thead>
      <tbody>{body}</tbody>
    </table>
  </div>
</section>"""

    def _render_coverage_row(self, row: Dict[str, Any], result_index: Dict[Tuple[str, str, str], Dict[str, Any]]) -> str:
        page = self._target_page_name(row.get("page_id"))
        case_id = str(row.get("case_id") or "-")
        title = str(row.get("test_title") or "-")
        matched = None
        if row.get("case_id"):
            matched = result_index.get(("case_id", page, str(row.get("case_id"))))
        if not matched and row.get("test_title"):
            matched = result_index.get(("title", page, str(row.get("test_title"))))
        status = self._coverage_row_status(row, matched)
        anchor = matched.get("anchor") if matched else ""
        case_cell = html.escape(case_id)
        if anchor:
            case_cell = f'<a class="case-link" href="#{html.escape(str(anchor))}">{case_cell}</a>'
        return (
                "<tr>"
            f"<td>{case_cell}</td>"
            f"<td>{html.escape(title)}</td>"
            f"<td>{self._render_status_badge(status)}</td>"
                f"<td>{html.escape(str(row.get('automation_mode') or '-'))}</td>"
                f"<td>{html.escape(str(row.get('destructive') or 'false'))}</td>"
                f"<td>{html.escape(str(row.get('excluded_reason') or '-'))}</td>"
                "</tr>"
        )

    def _render_diagnostics(self, item: Dict[str, Any]) -> str:
        legacy_action = item.get("legacy_action") or {}
        new_action = item.get("new_action") or {}
        legacy_frame = item.get("legacy_frame") or legacy_action.get("target_frame") or {}
        new_frame = item.get("new_frame") or new_action.get("target_frame") or {}
        diagnostics = {
            "legacy_locator": item.get("legacy_locator"),
            "new_locator": item.get("new_locator"),
            "legacy_frame": legacy_frame,
            "new_frame": new_frame,
            "legacy_wait": legacy_action.get("wait_state"),
            "new_wait": new_action.get("wait_state"),
            "legacy_selector_found": legacy_action.get("selector_found"),
            "new_selector_found": new_action.get("selector_found"),
            "legacy_blocked_reason": legacy_action.get("reason"),
            "new_blocked_reason": new_action.get("reason"),
            "upload_file": item.get("upload_file"),
            "submit_locator": item.get("submit_locator"),
            "download_success_match": item.get("download_success_match"),
            "legacy_download_success": item.get("legacy_download_success"),
            "new_download_success": item.get("new_download_success"),
            "download_filename_match": item.get("download_filename_match"),
            "download_size_match": item.get("download_size_match"),
            "download_hash_match": item.get("download_hash_match"),
            "download_extension_match": item.get("download_extension_match"),
            "legacy_download_filename": item.get("legacy_download_filename"),
            "new_download_filename": item.get("new_download_filename"),
            "legacy_download_path": item.get("legacy_download_path"),
            "new_download_path": item.get("new_download_path"),
            "legacy_download_saved_path": item.get("legacy_download_saved_path"),
            "new_download_saved_path": item.get("new_download_saved_path"),
            "legacy_download_original_path": item.get("legacy_download_original_path"),
            "new_download_original_path": item.get("new_download_original_path"),
            "legacy_download_archive_path": item.get("legacy_download_archive_path"),
            "new_download_archive_path": item.get("new_download_archive_path"),
            "legacy_download_size": item.get("legacy_download_size"),
            "new_download_size": item.get("new_download_size"),
            "legacy_download_sha256": item.get("legacy_download_sha256"),
            "new_download_sha256": item.get("new_download_sha256"),
            "pdf_save_success_match": item.get("pdf_save_success_match"),
            "legacy_pdf_saved": item.get("legacy_pdf_saved"),
            "new_pdf_saved": item.get("new_pdf_saved"),
            "legacy_pdf_path": item.get("legacy_pdf_path"),
            "new_pdf_path": item.get("new_pdf_path"),
            "legacy_pdf_size": item.get("legacy_pdf_size"),
            "new_pdf_size": item.get("new_pdf_size"),
            "legacy_pdf_backend": item.get("legacy_pdf_backend"),
            "new_pdf_backend": item.get("new_pdf_backend"),
            "legacy_console_screenshot": item.get("legacy_console_screenshot"),
            "new_console_screenshot": item.get("new_console_screenshot"),
            "legacy_console_error_count": item.get("legacy_console_error_count"),
            "new_console_error_count": item.get("new_console_error_count"),
            "legacy_http_error_count": item.get("legacy_http_error_count"),
            "new_http_error_count": item.get("new_http_error_count"),
            "legacy_request_failed_count": item.get("legacy_request_failed_count"),
            "new_request_failed_count": item.get("new_request_failed_count"),
            "legacy_after_url": item.get("legacy_after_url"),
            "new_after_url": item.get("new_after_url"),
            "navigation_detected": item.get("navigation_detected"),
            "popup_detected": item.get("popup_detected"),
            "frame_changed": item.get("frame_changed"),
            "validation_only": item.get("validation_only"),
            "post_action_reopen": item.get("post_action_reopen"),
            "frame_candidates": item.get("frame_candidates"),
        }
        return (
            "<details class=\"diag\">"
            "<summary>Diagnostics</summary>"
            f"<code>{html.escape(json.dumps(diagnostics, ensure_ascii=False, default=str, indent=2))}</code>"
            "</details>"
        )

    def _dedupe_actions(self, actions: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Backward-compatible dedupe used by older tests and callers.

        Keep the first action for the same semantic key. page_mapping places
        locator_changes before full_action_steps, so this preserves explicit
        migration mappings over later fallback scanner actions.
        """
        seen = set()
        unique: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        for action in actions:
            key = (
                action.get("semantic_key")
                or action.get("label")
                or action.get("legacy_locator")
                or action.get("locator")
                or json.dumps(action, sort_keys=True, ensure_ascii=False, default=str)
            )
            if key in seen:
                skipped.append(action)
                continue
            seen.add(key)
            unique.append(action)
        return unique, skipped

    @staticmethod
    def _page_url(base_url: str, page_id: str) -> str:
        return urljoin(base_url, str(page_id or "").lstrip("/"))

    @staticmethod
    def _route_map_paths(route_map_path: Optional[str]) -> Optional[List[Path]]:
        if not route_map_path:
            return None
        paths: List[Path] = []
        for raw in str(route_map_path).split(","):
            item = raw.strip()
            if not item:
                continue
            path = Path(item)
            if any(char in item for char in "*?[]"):
                paths.extend(Path(match) for match in sorted(glob.glob(item)))
            elif path.is_dir():
                paths.extend(sorted(path.rglob("usable_route_map*.json")))
            else:
                paths.append(path)
        return paths

    @staticmethod
    def _normalized_url(url: str) -> str:
        if not url:
            return ""
        parts = urlsplit(url)
        ignored = {"userid", "sessionid", "jsessionid", "token", "csrf", "_", "timestamp"}
        query_pairs = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() in ignored:
                continue
            query_pairs.append((key, value))
        query = urlencode(query_pairs)
        return f"{parts.path.rstrip('/') or '/'}?{query}".rstrip("?")

    @staticmethod
    def _safe_name(value: Any) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown")).strip("_")[:120] or "unknown"

    @staticmethod
    def _target_page_name(value: Any) -> str:
        name = PureWindowsPath(str(value or "").strip().replace("/", "\\")).name
        if name.lower().endswith(".do"):
            name = name[:-3] + ".jsp"
        return name.lower()

    @staticmethod
    def _target_page_aliases(value: Any) -> set[str]:
        return page_aliases(value)

    def _page_matches_mapping(self, page: Page, mapping: Dict[str, Any]) -> bool:
        """
        Best-effort check that direct navigation landed on the requested page.

        Legacy Struts pages can render the useful state inside frames or
        popups, so this checks all frame URLs against both page_id and resolved
        action URL. Body presence alone is not enough because login/menu pages
        also satisfy that.
        """
        target_values = [
            mapping.get("page_id"),
            mapping.get("entry_url"),
            mapping.get("resolved_entry_url"),
        ]
        needles = []
        for value in target_values:
            raw = str(value or "").replace("\\", "/").strip().lower()
            if not raw:
                continue
            leaf = raw.rsplit("/", 1)[-1]
            stem = re.sub(r"\.(jsp|do|action)$", "", leaf, flags=re.IGNORECASE)
            for candidate in (raw, leaf, stem, f"{stem}.do", *page_aliases(value)):
                candidate = candidate.strip("/")
                if len(candidate) >= 3 and candidate not in needles:
                    needles.append(candidate)

        if not needles:
            return True

        urls = []
        try:
            urls.append(page.url)
            urls.extend(frame.url for frame in page.frames)
        except PlaywrightError:
            return False

        haystack = "\n".join(str(url or "").replace("\\", "/").lower() for url in urls)
        return any(needle in haystack for needle in needles)

    @staticmethod
    def _page_is_closed(page: Page) -> bool:
        try:
            return page.is_closed()
        except Exception:
            return True

    def _open_or_reset_page(self, page: Page, url: str, capture_dir: Path, name: str) -> Tuple[Page, Dict[str, Any]]:
        try:
            if self._page_is_closed(page):
                context = page.context
                page = context.new_page()
        except Exception as exc:
            return page, {"status": "BLOCKED", "url": url, "reason": f"Failed to create replacement page: {exc}"}

        nav = self._goto(page, url)
        if nav.get("status") != "PASS":
            try:
                nav["state"] = _capture_state(page, capture_dir, name)
            except Exception as exc:
                nav["capture_error"] = str(exc)
        return page, nav

    def _goto(self, page: Page, url: str) -> Dict[str, Any]:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=max(self.timeout, 60000))

            try:
                page.wait_for_selector("body, form, table, input", timeout=10000)
            except Exception:
                pass

            return {"status": "PASS", "url": page.url}
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            return {"status": "BLOCKED", "url": url, "reason": str(exc)}
