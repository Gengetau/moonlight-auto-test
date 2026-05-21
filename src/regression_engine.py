import html
import json
import re
from collections import Counter
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

from playwright.sync_api import Page, Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from src.action_executor import _capture_state, _normalize_text, execute_action, infer_semantic_action
from src.assert_engine import compare_visual_screenshot
from src.config_parser import Config
from src.utils.struts_resolver import StrutsResolver


class RegressionEngine:
    """
    Risk-first Legacy/New regression runner backed by page_mapping.json.

    The engine opens equivalent pages in both environments, replays mapped
    locator-change actions, compares URL/DOM/visual state, and writes a
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
        struts_config_path: Optional[str] = None,
    ) -> None:
        self.mapping_path = Path(mapping_path)
        self.legacy_base_url = (legacy_base_url or Config.LEGACY_URL).rstrip("/")
        self.new_base_url = (new_base_url or Config.NEW_URL).rstrip("/")
        self.output_dir = Path(output_dir)
        self.visual_threshold_percent = visual_threshold_percent
        self.timeout = timeout
        self.mapping = self.load_mapping()
        
        # 智能化路由解析
        self.resolver = StrutsResolver()
        config_paths = struts_config_path or "data/"
        self.resolver.load_configs(config_paths)

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
        if target_page:
            normalized_target = self._target_page_name(target_page)
            pages = [
                page
                for page in self.mapping.get("page_mappings", [])
                if self._target_page_name(page.get("page_id")) == normalized_target
            ]
            if not pages:
                available = sorted(
                    self._target_page_name(page.get("page_id"))
                    for page in self.mapping.get("page_mappings", [])
                    if page.get("page_id")
                )
                sample = ", ".join(available[:10])
                suffix = f" Available pages include: {sample}" if sample else " Mapping contains no pages."
                raise ValueError(
                    f"Target JSP page not found in mapping file {self.mapping_path}: {target_page}.{suffix}"
                )
            return pages[:1]

        levels = list(risk_levels or (["High", "Medium"] if risk_only else ["High", "Medium", "Low"]))
        rank = {level.lower(): index for index, level in enumerate(levels)}
        pages = [
            page
            for page in self.mapping.get("page_mappings", [])
            if str(page.get("risk", "")).lower() in rank
        ]
        pages.sort(key=lambda page: (rank[str(page.get("risk", "")).lower()], page.get("page_id", "")))
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
        page_dir = self.output_dir / f"{page_index:04d}_{self._safe_name(page_id)}"
        page_dir.mkdir(parents=True, exist_ok=True)

        legacy_url = self._page_url(self.legacy_base_url, self.resolver.resolve_entry_url(page_id))
        new_url = self._page_url(self.new_base_url, self.resolver.resolve_entry_url(page_id))

        if manual:
            print(f"\n[MANUAL MODE] 请手动操作浏览器并导航至目标页面:")
            print(f" - 期待的目标画面: {page_id}")
            print(f" - Legacy 入口: {legacy_url}")
            print(f" - New 入口:    {new_url}")

            def _interactive_takeover(p: Page, label: str) -> Page:
                print(f"\n >>> [{label}] 准备就绪后，在此处按回车 [ENTER] 进行接管...")
                print(f"     (注: 如果浏览器点击无响应，请按回车激活同步锁)")
                
                while True:
                    # 每 200ms 检查一次，同时让 Playwright 处理 protocol 消息
                    try:
                        p.wait_for_timeout(200)
                    except:
                        pass
                    
                    input_signal = input(f" [{label} READY?] 按回车扫描页面 (或输入 'q' 放弃): ").strip().lower()
                    if input_signal == 'q':
                        raise InterruptedError("User cancelled manual takeover")

                    all_pages = p.context.pages
                    print(f" [{label}] 探测到 {len(all_pages)} 个页面对象:")
                    
                    match_idx = -1
                    # 匹配优先级：1. 精确包含 JSP 名  2. 包含对应的 Struts Action 名
                    target_action = self.resolver.resolve_entry_url(page_id).lower()
                    
                    for idx, page_item in enumerate(all_pages):
                        url_lower = page_item.url.lower()
                        print(f"    [{idx}] {page_item.url[:120]}")
                        if page_id.lower() in url_lower or target_action in url_lower:
                            match_idx = idx
                    
                    if match_idx != -1:
                        print(f" [SUCCESS] 发现潜在匹配页面: [{match_idx}]")
                        choice_idx = match_idx
                    else:
                        print(f" [WARN] 未发现包含 '{page_id}' 或 Action '{target_action}' 的页面。")
                        raw_choice = input(f" >>> 请输入页面索引 [0-{len(all_pages)-1}] 手动指定，或直接回车重试: ").strip()
                        if raw_choice.isdigit() and 0 <= int(raw_choice) < len(all_pages):
                            choice_idx = int(raw_choice)
                        else:
                            continue
                    
                    target = all_pages[choice_idx]
                    try:
                        target.bring_to_front()
                        target.wait_for_load_state("domcontentloaded", timeout=3000)
                        return target
                    except Exception as e:
                        print(f" [ERROR] 挂载失败 ({e})，请重试。")

            legacy_page = _interactive_takeover(legacy_page, "Legacy")
            new_page = _interactive_takeover(new_page, "New")
            
            print(f" [INFO] 已重定向接管目标: Legacy({legacy_page.url}) | New({new_page.url})")
            
            legacy_nav = {"status": "PASS", "url": legacy_page.url, "manual": True}
            new_nav = {"status": "PASS", "url": new_page.url, "manual": True}
            
            # 手动模式直接调用抽取后的逻辑
            return self._run_captured_page_pair(
                legacy_page, new_page, mapping, page_dir, browser_name, legacy_nav, new_nav, manual=True
            )
        else:
            legacy_nav = self._goto(legacy_page, legacy_url)
            new_nav = self._goto(new_page, new_url)
            return self._run_captured_page_pair(
                legacy_page, new_page, mapping, page_dir, browser_name, legacy_nav, new_nav, manual=False
            )

    def _run_captured_page_pair(
        self,
        legacy_page: Page,
        new_page: Page,
        mapping: Dict[str, Any],
        page_dir: Path,
        browser_name: str,
        legacy_nav: Dict[str, Any],
        new_nav: Dict[str, Any],
        manual: bool = False, # 显式传递 manual 标志
    ) -> List[Dict[str, Any]]:
        page_id = mapping.get("page_id") or "unknown"
        results: List[Dict[str, Any]] = []
        
        # 获取初始 URL 用于状态隔离重置
        legacy_url = legacy_page.url
        new_url = new_page.url
        
        # 强制更新 Page 事件处理器以适配接管后的新上下文
        self._ensure_page_listeners(legacy_page)
        self._ensure_page_listeners(new_page)
        
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

        # [饱和式打击] 合并 locator_changes 和 full_action_steps
        # 确保不仅测试变更的项目，也对所有识别到的按钮/链接进行一致性确认
        target_actions = mapping.get("locator_changes", [])
        if not target_actions:
            # 如果没有检测到定位器变更，则退而求其次执行所有识别到的语义动作
            for step in mapping.get("full_action_steps", []):
                # 尝试从新世界中寻找对应的 locator
                # 这需要实时查找，由于 Mapping 阶段可能漏掉，我们在这里做最后尝试
                legacy_loc = step.get("legacy_locator")
                # 简单启发式：如果在 Mapping 中没发现变化，假设 locator 依然一致
                target_actions.append({
                    "legacy_locator": legacy_loc,
                    "new_locator": legacy_loc,
                    "label": step.get("label"),
                    "semantic_key": step.get("semantic_key"),
                    "kind": step.get("kind")
                })

        for action_index, change in enumerate(target_actions, start=1):
            legacy_locator = change.get("legacy_locator")
            new_locator = change.get("new_locator")
            action_name = change.get("label") or change.get("semantic_key") or f"action_{action_index}"
            action_type = infer_semantic_action(change.get("action_hint") or change.get("kind") or "click", change)
            if not legacy_locator or not new_locator:
                results.append(
                    {
                        "page_id": page_id,
                        "risk": mapping.get("risk"),
                        "action": action_name,
                        "action_type": action_type,
                        "status": "BLOCKED",
                        "reason": "legacy_locator or new_locator is missing",
                        "legacy_locator": legacy_locator,
                        "new_locator": new_locator,
                    }
                )
                continue

            # [物理重置隔离] 每个 Action 执行前，通过浏览器原生 Back 逻辑回退或重定向
            # 针对旧系统，直接 goto 可能导致 Session 丢失，而 goBack() 或重定向至初始捕获 URL 更稳健
            if not manual:
                if legacy_page.url != legacy_url:
                    try:
                        legacy_page.go_back(wait_until="domcontentloaded", timeout=10000)
                    except:
                        legacy_page.goto(legacy_url, wait_until="domcontentloaded", timeout=15000)
                if new_page.url != new_url:
                    try:
                        new_page.go_back(wait_until="domcontentloaded", timeout=10000)
                    except:
                        new_page.goto(new_url, wait_until="domcontentloaded", timeout=15000)
            else:
                # 在接管模式下，如果当前 URL 偏离了接管时的初始 URL，尝试执行原生回退
                if legacy_page.url != legacy_url:
                    print(f" [DEBUG] Legacy 页面发生偏移，尝试执行原生 Back...")
                    try: legacy_page.go_back(wait_until="domcontentloaded", timeout=5000)
                    except: pass
                if new_page.url != new_url:
                    print(f" [DEBUG] New 页面发生偏移，尝试执行原生 Back...")
                    try: new_page.go_back(wait_until="domcontentloaded", timeout=5000)
                    except: pass

            legacy_action = execute_action(
                legacy_page,
                action_type,
                legacy_locator,
                change.get("value"),
                browser_name=browser_name,
                capture_dir=page_dir,
                test_id=f"{action_index:02d}_legacy_{action_name}",
                timeout=self.timeout,
                action_context={**change, "locator": legacy_locator},
            )
            new_action = execute_action(
                new_page,
                action_type,
                new_locator,
                change.get("value"),
                browser_name=browser_name,
                capture_dir=page_dir,
                test_id=f"{action_index:02d}_new_{action_name}",
                timeout=self.timeout,
                action_context={**change, "locator": new_locator},
            )
            legacy_action_state = legacy_action.get("state") or {}
            new_action_state = new_action.get("state") or {}
            compared = self._compare_state(
                page_id,
                mapping.get("risk"),
                action_name,
                legacy_action_state,
                new_action_state,
                page_dir / f"{action_index:02d}_diff.png",
                legacy_action=legacy_action,
                new_action=new_action,
            )
            compared.update({"action_type": action_type, "legacy_locator": legacy_locator, "new_locator": new_locator})
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
        visual = compare_visual_screenshot(
            legacy_state.get("screenshot", ""),
            new_state.get("screenshot", ""),
            str(diff_path),
            threshold_percent=self.visual_threshold_percent,
        )
        url_match = self._normalized_url(legacy_state.get("url", "")) == self._normalized_url(new_state.get("url", ""))
        dom_match = _normalize_text(legacy_state.get("dom", "")) == _normalize_text(new_state.get("dom", ""))
        status = "PASS" if url_match and dom_match and visual.get("status") == "PASS" else "DIFF"
        if visual.get("status") == "BLOCKED":
            status = "BLOCKED"

        if (extra.get("legacy_action") or {}).get("status") == "BLOCKED" or (extra.get("new_action") or {}).get("status") == "BLOCKED":
            status = "BLOCKED"
        if (extra.get("legacy_nav") or {}).get("status") == "BLOCKED" or (extra.get("new_nav") or {}).get("status") == "BLOCKED":
            status = "BLOCKED"

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

    @staticmethod
    def _ensure_page_listeners(page: Page):
        """确保页面具备基本的事件监听，尤其是接管后的新页面对象"""
        try:
            # 简单的保活心跳，确保 CDP 链路通畅
            page.evaluate("1 + 1")
            
            # 如果是接管后的页面，需要重新绑定 Dialog 处理器以防挂起
            # 注意: Playwright sync_api 不支持直接 remove_all_listeners，我们直接叠加安全处理器
            def _safe_dialog(dialog):
                try:
                    dialog.accept()
                except:
                    pass
            page.on("dialog", _safe_dialog)
        except:
            pass

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
    .pill {{ padding: 8px 12px; border-radius: 6px; background: #273746; font-weight: 700; }}
    main {{ padding: 24px 32px; }}
    .case {{ margin-bottom: 20px; border: 1px solid #d9e0ea; border-radius: 8px; background: white; overflow: hidden; }}
    .case-head {{ display: flex; align-items: center; gap: 12px; padding: 12px 16px; border-bottom: 1px solid #e7ecf3; }}
    .status {{ padding: 4px 8px; border-radius: 4px; color: white; font-weight: 700; font-size: 12px; }}
    .PASS {{ background: #1e8449; }} .DIFF {{ background: #b7950b; }} .BLOCKED {{ background: #922b21; }}
    .meta {{ color: #52616f; font-size: 13px; overflow-wrap: anywhere; }}
    .grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; padding: 16px; }}
    figure {{ margin: 0; }}
    figcaption {{ margin-bottom: 6px; font-size: 12px; color: #52616f; font-weight: 700; }}
    img {{ width: 100%; max-height: 520px; object-fit: contain; border: 1px solid #d9e0ea; background: #fff; }}
    .details {{ padding: 0 16px 16px; font-size: 13px; color: #34495e; }}
    .diag {{ margin-top: 10px; padding: 10px; border: 1px solid #e7ecf3; border-radius: 6px; background: #f8fafc; overflow-wrap: anywhere; }}
    code {{ font-family: Consolas, monospace; font-size: 12px; white-space: pre-wrap; }}
    @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} }}
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
    </div>
  </header>
  <main>{rows or '<p>No results.</p>'}</main>
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
    URL match: {item.get('url_match')} | DOM match: {item.get('dom_match')} | Action type: {html.escape(str(action_type or '-'))}<br>
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

    @staticmethod
    def _page_url(base_url: str, page_id: str) -> str:
        page = str(page_id or "").lstrip("/")
        return f"{base_url}/{page}"

    @staticmethod
    def _normalized_url(url: str) -> str:
        if not url:
            return ""
        parts = urlsplit(url)
        return f"{parts.path.rstrip('/') or '/'}?{parts.query}".rstrip("?")

    @staticmethod
    def _safe_name(value: Any) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown")).strip("_")[:120] or "unknown"

    @staticmethod
    def _target_page_name(value: Any) -> str:
        return PureWindowsPath(str(value or "").replace("/", "\\")).name

    def _goto(self, page: Page, url: str) -> Dict[str, Any]:
        try:
            page.goto(url, wait_until="load", timeout=max(self.timeout, 30000))
            return {"status": "PASS", "url": url}
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            return {"status": "BLOCKED", "url": url, "reason": str(exc)}
