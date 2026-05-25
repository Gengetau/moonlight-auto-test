import html
import json
import re
import time
import glob
from collections import Counter
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urljoin

from playwright.sync_api import Page, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from src.action_executor import _capture_state, execute_action, infer_semantic_action
from src.assert_engine import compare_visual_screenshot
from src.config_parser import Config
from src.route_navigator import RouteMapCatalog, RouteNavigator


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
    visual_required = normalized_action not in {"download", "close_window"}
    url_required = normalized_action in {"navigate", "page_snapshot"}

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
    ) -> None:
        self.mapping_path = Path(mapping_path)
        self.checklist_path = Path(checklist_path) if checklist_path else None
        self.legacy_base_url = legacy_base_url or Config.LEGACY_URL
        self.new_base_url = new_base_url or Config.NEW_URL
        self.output_dir = Path(output_dir)
        self.visual_threshold_percent = visual_threshold_percent
        self.timeout = timeout
        self.force_route_map = force_route_map
        self.upload_file = str(upload_file) if upload_file else None
        self.mapping = self.load_mapping()
        self.route_map_catalog = RouteMapCatalog(self._route_map_paths(route_map_path))

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
        根据风险等级或 target_page 选择需要执行回归测试的页面。

        优先级：
        1. 指定 target_page 时，仅返回对应页面
        2. 否则按 risk_level 过滤并排序
        """

        # ------------------------------------------------------------------
        # 指定单页面执行
        # ------------------------------------------------------------------
        if target_page:
            normalized_target = self._target_page_name(target_page)

            pages = [
                page
                for page in self.mapping.get("page_mappings", [])
                if self._target_page_name(page.get("page_id"))
                == normalized_target
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
        # 风险等级过滤
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
        # 按风险等级 + page_id 排序
        # ------------------------------------------------------------------
        pages.sort(
            key=lambda page: (
                rank[str(page.get("risk", "")).lower()],
                page.get("page_id", ""),
            )
        )

        # ------------------------------------------------------------------
        # limit 截断
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
        page_dir.mkdir(parents=True, exist_ok=True)

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

        # ============================================================
        # MANUAL MODE
        # ============================================================
        if manual:
            print("\n[MANUAL MODE] 请手动操作浏览器并导航至目标页面:")
            print(f" - 期待的目标画面: {page_id}")
            print(f" - Legacy 入口: {legacy_url}")
            print(f" - New 入口:    {new_url}")

            def _interactive_takeover(
                page: Page,
                label: str,
            ) -> Page:
                print(
                    f"\n >>> [{label}] 准备就绪后，在此处按回车 [ENTER] 进行接管..."
                )

                print(
                    "     (注: 如果浏览器点击无响应，请按回车激活同步锁)"
                )

                while True:
                    # ------------------------------------------------
                    # 让 Playwright 持续处理 protocol 消息
                    # ------------------------------------------------
                    try:
                        page.wait_for_timeout(200)
                    except Exception:
                        pass

                    input_signal = input(
                        f" [{label} READY?] "
                        f"按回车扫描页面 (或输入 'q' 放弃): "
                    ).strip().lower()

                    if input_signal == "q":
                        raise InterruptedError(
                            "User cancelled manual takeover"
                        )

                    all_pages = page.context.pages

                    print(
                        f" [{label}] 探测到 {len(all_pages)} 个页面对象:"
                    )

                    match_idx = -1

                    # ------------------------------------------------
                    # 页面匹配优先级
                    # 1. 包含 JSP 名
                    # 2. 包含对应 Struts Action
                    # ------------------------------------------------
                    target_action = str(
                        mapping.get("entry_url")
                        or mapping.get("resolved_entry_url")
                        or page_id
                    ).lower()

                    for idx, page_item in enumerate(all_pages):
                        url_lower = page_item.url.lower()

                        print(
                            f"    [{idx}] "
                            f"{page_item.url[:120]}"
                        )

                        if (
                            page_id.lower() in url_lower
                            or target_action in url_lower
                        ):
                            match_idx = idx

                    # ------------------------------------------------
                    # 自动发现匹配页面
                    # ------------------------------------------------
                    if match_idx != -1:
                        print(
                            f" [SUCCESS] 发现潜在匹配页面: "
                            f"[{match_idx}]"
                        )

                        choice_idx = match_idx

                    # ------------------------------------------------
                    # 未匹配时允许人工指定
                    # ------------------------------------------------
                    else:
                        print(
                            f" [WARN] 未发现包含 "
                            f"'{page_id}' 或 Action "
                            f"'{target_action}' 的页面。"
                        )

                        raw_choice = input(
                            f" >>> 请输入页面索引 "
                            f"[0-{len(all_pages)-1}] "
                            f"手动指定，或直接回车重试: "
                        ).strip()

                        if (
                            raw_choice.isdigit()
                            and 0 <= int(raw_choice) < len(all_pages)
                        ):
                            choice_idx = int(raw_choice)
                        else:
                            continue

                    # ------------------------------------------------
                    # 接管目标页面
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
                            f" [ERROR] 挂载失败 ({exc})，请重试。"
                        )

            # ========================================================
            # Legacy/New 手动接管
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
                f" [INFO] 已重定向接管目标: "
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
            # 手动模式直接执行已接管页面
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
                else:
                    new_nav.update({"strategy": "direct_url", "target_reached": True})
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
        checklist_cases = self._load_checklist_cases(page_id)
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

    def _load_checklist_cases(self, page_id: str) -> List[Dict[str, Any]]:
        """
        Load executable cases from generated migration checklist.

        This is intentionally tolerant. If the Excel does not yet contain
        machine-readable columns, it returns [] and the engine falls back to
        mapping executable_cases/full_action_steps.
        """
        if not self.checklist_path or not self.checklist_path.exists():
            return []

        try:
            from openpyxl import load_workbook
        except Exception:
            return []

        try:
            wb = load_workbook(self.checklist_path, data_only=True, read_only=True)
            sheet = wb["Checklist"] if "Checklist" in wb.sheetnames else wb[wb.sheetnames[0]]
            rows = list(sheet.iter_rows(values_only=True))
        except Exception:
            return []

        if not rows:
            return []

        headers = [str(value or "").strip() for value in rows[0]]
        header_map = {header.lower(): idx for idx, header in enumerate(headers) if header}

        def col(*names: str) -> Optional[int]:
            for name in names:
                key = name.strip().lower()
                if key in header_map:
                    return header_map[key]
            return None

        page_col = col("page_id", "page", "页面", "画面", "ページ", "対象画面")
        mode_col = col("automation_mode", "自动化模式", "自動化モード", "自動化", "automation")
        case_type_col = col("case_type", "ケース種別", "用例类型", "case")
        action_type_col = col("action_type", "アクション", "动作类型", "操作类型")
        locator_col = col("locator", "selector", "定位器", "セレクタ")
        legacy_locator_col = col("legacy_locator", "legacy selector", "移行前locator")
        new_locator_col = col("new_locator", "new selector", "移行後locator")
        submit_locator_col = col("submit_locator", "submit selector", "提交locator")
        test_data_col = col("test_data", "value", "测试数据", "テストデータ")
        case_id_col = col("case_id", "id", "no", "項目no")
        title_col = col("title", "test_viewpoint", "テスト観点", "测试项目", "用例")

        if page_col is None or mode_col is None:
            return []

        target_name = self._target_page_name(page_id)
        cases: List[Dict[str, Any]] = []

        for row in rows[1:]:
            def cell(idx: Optional[int]) -> str:
                if idx is None or idx >= len(row):
                    return ""
                value = row[idx]
                return "" if value is None else str(value).strip()

            if self._target_page_name(cell(page_col)) != target_name:
                continue

            mode = cell(mode_col).lower()
            if mode not in {"auto", "automated", "true", "yes", "y", "1", "自動", "自动"}:
                continue

            case_type = cell(case_type_col) or cell(action_type_col) or "click"
            action_type = cell(action_type_col) or case_type
            action_lower = str(action_type or case_type).strip().lower()
            locator = cell(locator_col)
            legacy_locator = cell(legacy_locator_col) or locator
            new_locator = cell(new_locator_col) or locator or legacy_locator
            submit_locator = cell(submit_locator_col)
            test_data = cell(test_data_col)
            label = cell(title_col) or cell(case_id_col) or case_type

            if action_type == "upload_submit" or case_type == "upload_submit":
                if not locator and not legacy_locator:
                    continue
                case: Dict[str, Any] = {
                    "case_type": "upload_submit",
                    "action_type": "upload_submit",
                    "label": label,
                    "source": "checklist",
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
                        "action_type": "submit",
                        "legacy_locator": submit_locator,
                        "new_locator": submit_locator,
                        "locator": submit_locator,
                    },
                }
                cases.append(self._normalize_action_case(case))
            else:
                if action_lower in {"snapshot", "page_snapshot", "visual_check", "wait"}:
                    locator = locator or "__page__"
                    legacy_locator = legacy_locator or locator
                    new_locator = new_locator or locator
                elif not legacy_locator and not new_locator:
                    continue
                cases.append(
                    self._normalize_action_case(
                        {
                            "case_type": case_type,
                            "action_type": action_type,
                            "label": label,
                            "legacy_locator": legacy_locator,
                            "new_locator": new_locator or legacy_locator,
                            "locator": locator,
                            "value": test_data,
                            "source": "checklist",
                        }
                    )
                )

        return cases

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
            semantic_action = infer_semantic_action(action_type, step)
            action_lower = str(action_type or semantic_action).strip().lower()
            if semantic_action == "upload" and self.upload_file:
                value = self.upload_file

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

            if not locator and semantic_action not in {"goto"}:
                return {
                    "status": "BLOCKED",
                    "reason": f"Missing locator for scenario step {index}: {action_type}",
                    "action_type": action_type,
                    "state": _capture_state(page, capture_dir, f"{test_id}_step{index}_blocked"),
                    "executed_steps": executed,
                }

            result = execute_action(
                page,
                action_type,
                locator,
                value,
                browser_name=browser_name,
                capture_dir=capture_dir,
                test_id=f"{test_id}_step{index}",
                timeout=self.timeout,
                action_context={**action_case, **step, "locator": locator},
            )
            executed.append(
                {
                    "index": index,
                    "action_type": action_type,
                    "locator": locator,
                    "status": result.get("status"),
                    "reason": result.get("reason"),
                    "upload_file_override": bool(self.upload_file and semantic_action == "upload"),
                }
            )
            last_result = result

            if result.get("status") != "PASS":
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

        legacy_state = _capture_state(legacy_page, page_dir, "00_legacy_initial")
        new_state = _capture_state(new_page, page_dir, "00_new_initial")
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

        for blocked in mapping.get("missing_legacy_elements", []):
            results.append(
                {
                    "page_id": page_id,
                    "risk": mapping.get("risk"),
                    "action": blocked.get("label") or blocked.get("key") or "missing_legacy_element",
                    "action_type": infer_semantic_action(blocked.get("action_hint") or blocked.get("kind"), blocked),
                    "status": "BLOCKED",
                    "reason": "Legacy element has no mapped New equivalent",
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

        target_actions, plan_source = self._build_action_plan(page_id, mapping)
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

            if not legacy_locator and not action_case.get("pre_steps") and not action_case.get("main_step"):
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
                    "action_type": action_type,
                    "legacy_locator": legacy_locator,
                    "new_locator": new_locator,
                    "legacy_action": legacy_action,
                    "new_action": new_action,
                    "plan_source": plan_source,
                }
            )
            print(
                f"[{page_id}] COMPARE result: "
                + json.dumps(
                    {
                        "action": action_name,
                        "status": compared.get("status"),
                        "url_match": compared.get("url_match"),
                        "visual_status": (compared.get("visual") or {}).get("status"),
                        "visual_diff_percent": (compared.get("visual") or {}).get("diff_percent"),
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
            results.append(compared)

        return results


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
        if action_type.lower() not in {"download", "close_window"}:
            visual = compare_visual_screenshot(
                legacy_state.get("screenshot", ""),
                new_state.get("screenshot", ""),
                str(diff_path),
                threshold_percent=self.visual_threshold_percent,
            )
        url_match = self._normalized_url(legacy_state.get("url", "")) == self._normalized_url(new_state.get("url", ""))
        dom_match = str(legacy_state.get("dom", "")) == str(new_state.get("dom", ""))
        status = _judge_compare_status(
            action_type=action_type,
            visual_status=visual.get("status"),
            url_match=url_match,
            legacy_action_status=(extra.get("legacy_action") or {}).get("status"),
            new_action_status=(extra.get("new_action") or {}).get("status"),
            legacy_nav_status=(extra.get("legacy_nav") or {}).get("status"),
            new_nav_status=(extra.get("new_nav") or {}).get("status"),
        )

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
            **extra,
        }

    def render_report(self, results: List[Dict[str, Any]]) -> Path:
        report_path = self.output_dir / "regression_report.html"
        counts = Counter(item["status"] for item in results)
        rows = "\n".join(self._render_result(item) for item in results)
        report_path.write_text(
            f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>Moonlight Legacy/New Regression Report</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; color: #17202a; background: #f5f7fb; }}
    header {{ padding: 24px 32px; background: #17202a; color: white; }}
    h1 {{ margin: 0 0 12px; font-size: 26px; letter-spacing: 0; }}
    .summary {{ display: flex; gap: 12px; flex-wrap: wrap; }}
    .summary-details {{ margin-top: 16px; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .summary-card {{ background: #273746; border-radius: 8px; padding: 12px; }}
    .summary-card h2 {{ margin: 0 0 8px; font-size: 13px; color: #d6eaf8; }}
    .summary-card p {{ margin: 4px 0; font-size: 12px; color: #f8f9f9; overflow-wrap: anywhere; }}
    .summary-card .num {{ font-size: 20px; font-weight: 800; }}
    .pill {{ padding: 8px 12px; border-radius: 6px; background: #273746; font-weight: 700; }}
    main {{ padding: 24px 32px; }}
    .case {{ margin-bottom: 20px; border: 1px solid #d9e0ea; border-radius: 8px; background: white; overflow: hidden; }}
    .case-head {{ display: flex; align-items: center; gap: 12px; padding: 12px 16px; border-bottom: 1px solid #e7ecf3; }}
    .status {{ padding: 4px 8px; border-radius: 4px; color: white; font-weight: 700; font-size: 12px; }}
    .PASS {{ background: #1e8449; }} .WARN {{ background: #b7950b; }} .DIFF {{ background: #b7950b; }} .BLOCKED {{ background: #922b21; }} .ERROR {{ background: #7b241c; }}
    .meta {{ color: #52616f; font-size: 13px; overflow-wrap: anywhere; }}
    .grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; padding: 16px; }}
    figure {{ margin: 0; }}
    figcaption {{ margin-bottom: 6px; font-size: 12px; color: #52616f; font-weight: 700; }}
    img {{ width: 100%; max-height: 520px; object-fit: contain; border: 1px solid #d9e0ea; background: #fff; }}
    .details {{ padding: 0 16px 16px; font-size: 13px; color: #34495e; }}
    code {{ font-family: Consolas, monospace; font-size: 12px; white-space: pre-wrap; }}
    @media (max-width: 900px) {{ .grid, .summary-details {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <header>
    <h1>Moonlight Legacy/New Regression Report</h1>
    <div class="summary">
      <span class="pill">Total: {len(results)}</span>
      <span class="pill">PASS: {counts.get('PASS', 0)}</span>
      <span class="pill">DIFF: {counts.get('DIFF', 0)}</span>
      <span class="pill">BLOCKED: {counts.get('BLOCKED', 0)}</span>
      <span class="pill">ERROR: {counts.get('ERROR', 0)}</span>
    </div>
    {self._render_summary_details(results, counts)}
  </header>
  <main>{self._render_coverage_matrix(results)}{rows or '<p>No results.</p>'}</main>
</body>
</html>
""",
            encoding="utf-8",
        )
        return report_path

    def _render_result(self, item: Dict[str, Any]) -> str:
        visual = item.get("visual") or {}
        diff_percent = visual.get("diff_percent")
        diff_text = "-" if diff_percent is None else f"{diff_percent:.4f}%"
        action_type = item.get("action_type") or self._action_type_from_payload(item)
        reason = item.get("reason") or self._blocked_reason(item) or "-"
        return f"""
<section class="case">
  <div class="case-head">
    <span class="status {html.escape(item.get('status', 'DIFF'))}">{html.escape(item.get('status', 'DIFF'))}</span>
    <strong>{html.escape(str(item.get('page_id')))}</strong>
    <span class="meta">risk={html.escape(str(item.get('risk')))} action={html.escape(str(item.get('action')))} diff={diff_text}</span>
  </div>
  <div class="grid">
    {self._figure('Legacy', item.get('legacy_screenshot'))}
    {self._figure('New', item.get('new_screenshot'))}
    {self._figure('Diff', item.get('diff_screenshot'))}
  </div>
  <div class="details">
    URL match: {item.get('url_match')} | Action type: {html.escape(str(action_type or '-'))}<br>
    Legacy: {html.escape(str(item.get('legacy_url') or '-'))}<br>
    New: {html.escape(str(item.get('new_url') or '-'))}<br>
    Reason: {html.escape(str(reason))}<br>
    {self._render_diagnostics(item)}
  </div>
</section>"""

    def _figure(self, label: str, path: Optional[str]) -> str:
        if not path:
            return f"<figure><figcaption>{html.escape(label)}</figcaption><div class=\"meta\">No screenshot</div></figure>"
        rel = Path(path).resolve().relative_to(self.output_dir.resolve())
        return f'<figure><figcaption>{html.escape(label)}</figcaption><img src="{html.escape(str(rel))}" alt="{html.escape(label)}"></figure>'

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

    def _render_coverage_matrix(self, results: List[Dict[str, Any]]) -> str:
        action_types = {
            str(item.get("action_type") or self._action_type_from_payload(item) or "").lower()
            for item in results
        }
        rows = [
            ("1-1 画面レイアウト", "AUTO"),
            ("2 画面遷移", "AUTO" if {"navigate", "page_snapshot", "wait"} & action_types else "REVIEW"),
            ("3 入力・検索・更新", "AUTO" if {"fill", "submit", "click", "select"} & action_types else "REVIEW"),
            ("6 ファイル出力", "AUTO" if "download" in action_types else "REVIEW"),
            ("7 ファイルアップロード", "AUTO" if "upload" in action_types else "REVIEW"),
        ]
        body = "\n".join(
            f"<tr><td>{html.escape(label)}</td><td>{html.escape(status)}</td></tr>"
            for label, status in rows
        )
        return f"""
<section class="case">
  <div class="case-head"><strong>Checklist Coverage Matrix</strong></div>
  <div class="details">
    <table>
      <tbody>{body}</tbody>
    </table>
  </div>
</section>"""

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
            "frame_candidates": item.get("frame_candidates"),
        }
        return f'<div class="diag"><code>{html.escape(json.dumps(diagnostics, ensure_ascii=False, default=str, indent=2))}</code></div>'

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
        return PureWindowsPath(str(value or "").replace("/", "\\")).name

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
            for candidate in (raw, leaf, stem, f"{stem}.do"):
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
