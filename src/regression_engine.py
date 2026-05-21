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
    ) -> None:
        self.mapping_path = Path(mapping_path)
        self.legacy_base_url = (legacy_base_url or Config.LEGACY_URL).rstrip("/")
        self.new_base_url = (new_base_url or Config.NEW_URL).rstrip("/")
        self.output_dir = Path(output_dir)
        self.visual_threshold_percent = visual_threshold_percent
        self.timeout = timeout
        self.mapping = self.load_mapping()

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
    ) -> List[Dict[str, Any]]:
        page_id = mapping.get("page_id") or f"page_{page_index}"
        page_dir = self.output_dir / f"{page_index:04d}_{self._safe_name(page_id)}"
        page_dir.mkdir(parents=True, exist_ok=True)
        results: List[Dict[str, Any]] = []

        legacy_url = self._page_url(self.legacy_base_url, page_id)
        new_url = self._page_url(self.new_base_url, page_id)
        legacy_nav = self._goto(legacy_page, legacy_url)
        new_nav = self._goto(new_page, new_url)

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

        for action_index, change in enumerate(mapping.get("locator_changes", []), start=1):
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
