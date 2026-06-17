from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from workspace import workspace_root

ROOT = workspace_root()
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from edge_popup_compare import (  # noqa: E402
    CAPTURE_JS,
    capture_page,
    compare_records,
    compare_resources,
    normalize_dom,
    normalize_obj,
    normalize_text,
    rel,
    sha256_text,
)


ENTRIES = [
    {"key": "FI", "label": "FI", "code": "0400", "dir": "JpBiblioListSimpleAnalysis_FI_edge"},
    {"key": "Fterm", "label": "Fターム", "code": "0430", "dir": "JpBiblioListSimpleAnalysis_Fterm_edge"},
    {"key": "IPC", "label": "IPC", "code": "0380", "dir": "JpBiblioListSimpleAnalysis_IPC_edge"},
    {"key": "ApplicantOwner", "label": "出願人・権利者名", "code": "0690", "dir": "JpBiblioListSimpleAnalysis_ApplicantOwner_edge"},
    {"key": "FeatureTerm", "label": "特徴語", "code": "4000", "dir": "JpBiblioListSimpleAnalysis_FeatureTerm_edge"},
]


def find_parent_page(browser: Any, host: str) -> Any:
    pages = [p for c in browser.contexts for p in c.pages if host in p.url and "PatlicsTopMain.do" in p.url]
    if not pages:
        pages = [p for c in browser.contexts for p in c.pages if host in p.url]
    if not pages:
        seen = [p.url for c in browser.contexts for p in c.pages]
        raise RuntimeError(f"No PATLICS page found for {host}. Seen: {seen}")
    return pages[-1]


def current_frame(page: Any, frame_name: str) -> Any:
    frames = [f for f in page.frames if f.name == frame_name]
    if not frames:
        raise RuntimeError(f"Frame not found: {frame_name}; frames={[f.name + ':' + f.url for f in page.frames]}")
    return frames[-1]


def has_simple_analysis_menu(frame: Any, code: str) -> bool:
    return bool(
        frame.evaluate(
            """(code) => Boolean(document.querySelector('#blocAnalyzeTb')) &&
              Array.from(document.querySelectorAll('[onclick]')).some((el) => {
                const onclick = String(el.getAttribute('onclick') || '');
                return onclick.includes('fnAnalyzeDoc') && onclick.includes("'list'") && onclick.includes(code);
              })""",
            code,
        )
    )


def restore_parent(page: Any, frame_name: str, code: str) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for _ in range(5):
        frame = current_frame(page, frame_name)
        try:
            if has_simple_analysis_menu(frame, code):
                return {"ok": True, "frame_url": frame.url, "attempts": attempts}
        except PlaywrightError as exc:
            attempts.append({"action": "check_menu", "url": frame.url, "error": str(exc)})
        attempts.append({"action": "history.back", "from": frame.url})
        try:
            frame.evaluate("() => { history.back(); return location.href; }")
        except PlaywrightError as exc:
            attempts[-1]["error"] = str(exc)
        page.wait_for_timeout(1600)
    frame = current_frame(page, frame_name)
    return {"ok": False, "frame_url": frame.url, "attempts": attempts}


def click_entry(page: Any, frame_name: str, entry: dict[str, str]) -> dict[str, Any]:
    page.bring_to_front()
    frame = current_frame(page, frame_name)
    before_pages = [p.url for p in page.context.pages]
    before_url = frame.url
    click = frame.evaluate(
        """(entry) => {
          const norm = (v) => String(v ?? '').replace(/\\s+/g, ' ').trim();
          const header = document.querySelector('#blocAnalyzeTb');
          if (header) {
            header.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
          }
          const candidates = Array.from(document.querySelectorAll('[onclick]'));
          const item = candidates.find((el) => {
            const onclick = String(el.getAttribute('onclick') || '');
            return onclick.includes('fnAnalyzeDoc') &&
              onclick.includes("'list'") &&
              onclick.includes(entry.code) &&
              norm(el.innerText || el.textContent) === entry.label;
          });
          const menu = document.querySelector('#blocAnalyzeMenu');
          const info = {
            beforeUrl: location.href,
            headerFound: Boolean(header),
            menuFound: Boolean(menu),
            menuDisplay: menu ? getComputedStyle(menu).display : '',
            itemFound: Boolean(item),
            itemText: item ? norm(item.innerText || item.textContent) : '',
            itemOnclick: item ? item.getAttribute('onclick') : '',
            itemVisible: item ? Boolean(item.offsetWidth || item.offsetHeight || item.getClientRects().length) : false,
          };
          if (!item) return { ...info, clicked: false };
          item.click();
          return { ...info, clicked: true };
        }""",
        entry,
    )
    for _ in range(36):
        page.wait_for_timeout(500)
        frame = current_frame(page, frame_name)
        if frame.url != before_url and not frame.url.endswith("/process") and not frame.url.endswith("/process.jsp"):
            break
    after_pages = [p.url for p in page.context.pages]
    frame = current_frame(page, frame_name)
    return {
        "before_url": before_url,
        "after_url": frame.url,
        "click": click,
        "new_pages": [url for url in after_pages if url not in before_pages],
    }


def wait_analysis_page(context: Any, host: str, timeout_ms: int = 30000) -> Any:
    deadline = datetime.now().timestamp() + timeout_ms / 1000
    last_match = None
    while datetime.now().timestamp() < deadline:
        matches = [
            page
            for page in context.pages
            if host in page.url and "JpAnalyzeListDocForBiblioList.do" in page.url
        ]
        if matches:
            last_match = matches[-1]
            try:
                last_match.wait_for_load_state("domcontentloaded", timeout=5000)
            except PlaywrightError:
                pass
            last_match.wait_for_timeout(1000)
            return last_match
        context.pages[0].wait_for_timeout(500)
    seen = [page.url for page in context.pages]
    raise RuntimeError(f"Analysis page not found for host={host}. Seen pages: {seen}")


def normalize_record(record: dict[str, Any], hosts: tuple[str, str]) -> None:
    record["raw_text_sha256"] = sha256_text(record.get("text") or "")
    record["normalized_text"] = normalize_text(record.get("text") or "", hosts)
    record["normalized_text_sha256"] = sha256_text(record["normalized_text"])
    record["raw_dom_sha256"] = sha256_text(record.get("dom") or "")
    record["normalized_dom"] = normalize_dom(record.get("dom") or "", hosts)
    record["normalized_dom_sha256"] = sha256_text(record["normalized_dom"])
    record["normalized_controls"] = normalize_obj(record.get("controls") or [], hosts)
    record["normalized_controls_sha256"] = sha256_text(json.dumps(record["normalized_controls"], ensure_ascii=False, sort_keys=True))
    record["normalized_tables"] = normalize_obj(record.get("tables") or [], hosts)
    record["normalized_tables_sha256"] = sha256_text(json.dumps(record["normalized_tables"], ensure_ascii=False, sort_keys=True))


def capture_frame(page: Any, frame_name: str, label: str, output_dir: Path, hosts: tuple[str, str]) -> dict[str, Any]:
    page.bring_to_front()
    frame = current_frame(page, frame_name)
    try:
        frame.wait_for_load_state("domcontentloaded", timeout=10000)
    except PlaywrightError:
        pass
    page.wait_for_timeout(800)
    screenshot = output_dir / f"{label}.png"
    try:
        page.locator(f'frame[name="{frame_name}"], iframe[name="{frame_name}"]').first.screenshot(path=str(screenshot), timeout=5000)
        screenshot_method = "frame-element"
    except PlaywrightError:
        page.screenshot(path=str(screenshot), full_page=False, timeout=30000)
        screenshot_method = "page-viewport"
    record = frame.evaluate(CAPTURE_JS)
    record["captured_at"] = datetime.now().isoformat(timespec="seconds")
    record["screenshot"] = str(screenshot)
    record["screenshot_method"] = screenshot_method
    normalize_record(record, hosts)
    json_path = output_dir / f"{label}.json"
    json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    record["json"] = str(json_path)
    return record


def effective_status(compare: dict[str, Any], resources: dict[str, Any]) -> str:
    return "PASS" if compare.get("status") == "PASS" and resources.get("all_hashes_equal_by_order") else "DIFF"


def render_entry_report(result: dict[str, Any], output_dir: Path) -> str:
    status = result["status"]
    color = "#137333" if status == "PASS" else "#8a5a00" if status == "BLOCKED" else "#b3261e"
    compare = result.get("compare") or {}
    checks = compare.get("comparisons") or {}
    check_rows = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td class=\"{'ok' if (all(v.values()) if isinstance(v, dict) else bool(v)) else 'ng'}\"><pre>{html.escape(json.dumps(v, ensure_ascii=False, indent=2) if isinstance(v, dict) else str(v))}</pre></td></tr>"
        for k, v in checks.items()
    )
    resources = result.get("resources") or {}
    resource_rows = "".join(
        f"<tr><td>{html.escape(str(r.get('index')))}</td><td>{html.escape(str(r.get('legacy_kind')))}</td><td class=\"{'ok' if r.get('same_hash') else 'ng'}\">{html.escape(str(r.get('same_hash')))}</td><td>{html.escape(str(r.get('legacy_raw')))}</td><td>{html.escape(str(r.get('new_raw')))}</td><td>{html.escape(str(r.get('legacy_length')))} / {html.escape(str(r.get('new_length')))}</td></tr>"
        for r in resources.get("pairs", [])
    )
    accepted_rows = "".join(
        f"<tr><td>{html.escape(str(a.get('id')))}</td><td>{html.escape(str(a.get('status')))}</td><td>{html.escape(str(a.get('legacy')))}</td><td>{html.escape(str(a.get('new')))}</td><td>{html.escape(str(a.get('reason')))}</td></tr>"
        for a in ((compare.get("allowed_differences") or {}).get("accepted") or [])
    )
    diff_blocks = []
    for title, lines in [
        ("Text diff", ((compare.get("details") or {}).get("text_diff") or [])),
        ("Control diff", ((compare.get("details") or {}).get("control_diff") or [])),
        ("Table diff", ((compare.get("details") or {}).get("table_diff") or [])),
        ("DOM diff after accepted differences", ((compare.get("allowed_differences") or {}).get("remaining_dom_diff_after_allowed_server_differences") or [])),
    ]:
        diff_blocks.append(f"<h3>{html.escape(title)}</h3><pre>{html.escape(chr(10).join(lines) if lines else 'No diff')}</pre>")
    images = ""
    if result.get("legacy") and result.get("new"):
        visual = compare.get("visual") or {}
        images = "".join(
            f"<figure><figcaption>{html.escape(label)}</figcaption><img src=\"{html.escape(rel(path_value, output_dir))}\"></figure>"
            for label, path_value in [
                (".47", result["legacy"].get("screenshot")),
                (".192", result["new"].get("screenshot")),
                ("diff", visual.get("diff_screenshot")),
            ]
            if path_value
        )
    blocked = f"<h2>Blocked</h2><pre>{html.escape(json.dumps(result.get('blocked'), ensure_ascii=False, indent=2))}</pre>" if result.get("blocked") else ""
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>簡易分析 {html.escape(result['entry']['label'])} - {html.escape(status)}</title>
<style>body{{font-family:'Segoe UI','Yu Gothic UI',sans-serif;margin:20px;color:#172033}}.status{{background:{color};color:white;padding:4px 10px;border-radius:4px}}table{{border-collapse:collapse;width:100%;margin:12px 0 22px}}td,th{{border:1px solid #cfd6df;padding:6px;vertical-align:top}}th{{background:#eef2f6;text-align:left}}.ok{{color:#137333;font-weight:700}}.ng{{color:#b3261e;font-weight:700}}pre{{white-space:pre-wrap;max-height:360px;overflow:auto;background:#f6f8fa;padding:8px;border:1px solid #d4d9e2}}.images{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}img{{max-width:100%;border:1px solid #cfd6df}}code{{background:#f6f8fa;padding:1px 4px}}</style></head><body>
<h1>簡易分析 {html.escape(result['entry']['label'])} <span class="status">{html.escape(status)}</span></h1>
<p>Output: <code>{html.escape(str(output_dir))}</code></p>
<h2>Entry Behavior</h2><table><tr><th>Side</th><th>Before URL</th><th>After URL</th><th>Click evidence</th><th>New pages</th></tr>
<tr><td>.47</td><td>{html.escape(str((result.get('legacy_behavior') or {}).get('before_url')))}</td><td>{html.escape(str((result.get('legacy_behavior') or {}).get('after_url')))}</td><td><pre>{html.escape(json.dumps((result.get('legacy_behavior') or {}).get('click'), ensure_ascii=False, indent=2))}</pre></td><td><pre>{html.escape(json.dumps((result.get('legacy_behavior') or {}).get('new_pages'), ensure_ascii=False, indent=2))}</pre></td></tr>
<tr><td>.192</td><td>{html.escape(str((result.get('new_behavior') or {}).get('before_url')))}</td><td>{html.escape(str((result.get('new_behavior') or {}).get('after_url')))}</td><td><pre>{html.escape(json.dumps((result.get('new_behavior') or {}).get('click'), ensure_ascii=False, indent=2))}</pre></td><td><pre>{html.escape(json.dumps((result.get('new_behavior') or {}).get('new_pages'), ensure_ascii=False, indent=2))}</pre></td></tr></table>
{blocked}
<h2>Screenshots</h2><div class="images">{images}</div>
<h2>Checks</h2><table><tr><th>Check</th><th>Result</th></tr>{check_rows}<tr><td>static_resources_equal</td><td class="{'ok' if resources.get('all_hashes_equal_by_order') else 'ng'}">{html.escape(str(resources.get('all_hashes_equal_by_order')))}</td></tr></table>
<h2>Source Pages</h2><table><tr><th>Side</th><th>URL</th><th>Title</th><th>Counts</th><th>Metrics</th></tr>
<tr><td>.47</td><td>{html.escape(str((result.get('legacy') or {}).get('url')))}</td><td>{html.escape(str((result.get('legacy') or {}).get('title')))}</td><td><pre>{html.escape(json.dumps((result.get('legacy') or {}).get('counts'), ensure_ascii=False, indent=2))}</pre></td><td><pre>{html.escape(json.dumps((result.get('legacy') or {}).get('metrics'), ensure_ascii=False, indent=2))}</pre></td></tr>
<tr><td>.192</td><td>{html.escape(str((result.get('new') or {}).get('url')))}</td><td>{html.escape(str((result.get('new') or {}).get('title')))}</td><td><pre>{html.escape(json.dumps((result.get('new') or {}).get('counts'), ensure_ascii=False, indent=2))}</pre></td><td><pre>{html.escape(json.dumps((result.get('new') or {}).get('metrics'), ensure_ascii=False, indent=2))}</pre></td></tr></table>
<h2>Accepted Differences</h2><table><tr><th>ID</th><th>Status</th><th>.47</th><th>.192</th><th>Reason</th></tr>{accepted_rows}</table>
<h2>Static Resources</h2><p>Count {html.escape(str(resources.get('legacy_count')))} / {html.escape(str(resources.get('new_count')))}, diff_count={html.escape(str(resources.get('diff_count')))}, all_hashes_equal_by_order={html.escape(str(resources.get('all_hashes_equal_by_order')))}</p><table><tr><th>#</th><th>Kind</th><th>Same hash</th><th>.47 raw</th><th>.192 raw</th><th>Length</th></tr>{resource_rows}</table>
<h2>Raw Differences</h2>{''.join(diff_blocks)}
</body></html>"""


def render_summary(summary: dict[str, Any], output_root: Path) -> str:
    rows = ""
    for entry in summary["entries"]:
        status = entry["status"]
        cls = "ok" if status == "PASS" else "blocked" if status == "BLOCKED" else "ng"
        rows += f"<tr><td>{html.escape(entry['entry']['label'])}</td><td class='{cls}'>{html.escape(status)}</td><td><a href='{html.escape(rel(entry['report'], output_root))}'>report</a></td><td>{html.escape(str(entry['output_dir']))}</td><td><pre>{html.escape(json.dumps(entry.get('blocked'), ensure_ascii=False, indent=2) if entry.get('blocked') else '')}</pre></td></tr>"
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>簡易分析 5 entries summary</title>
<style>body{{font-family:'Segoe UI','Yu Gothic UI',sans-serif;margin:20px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #cfd6df;padding:7px;vertical-align:top}}th{{background:#eef2f6;text-align:left}}.ok{{color:#137333;font-weight:700}}.ng{{color:#b3261e;font-weight:700}}.blocked{{color:#8a5a00;font-weight:700}}pre{{white-space:pre-wrap;margin:0}}</style></head><body>
<h1>簡易分析 5 entries summary</h1><p>Status: <b>{html.escape(summary['status'])}</b></p>
<table><tr><th>Entry</th><th>Status</th><th>Report</th><th>Output directory</th><th>Blocked reason</th></tr>{rows}</table>
</body></html>"""


def run_entry(legacy_page: Any, new_page: Any, entry: dict[str, str], args: argparse.Namespace) -> dict[str, Any]:
    output_dir = (ROOT / args.output_root / entry["dir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "entry": entry,
        "output_dir": str(output_dir),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "BLOCKED",
    }
    try:
        legacy_parent = restore_parent(legacy_page, args.frame_name, entry["code"])
        new_parent = restore_parent(new_page, args.frame_name, entry["code"])
        result["parent_restore"] = {"legacy": legacy_parent, "new": new_parent}
        if not legacy_parent["ok"] or not new_parent["ok"]:
            result["blocked"] = {"reason": "Could not restore both environments to the parent page with the 簡易分析 menu.", "legacy": legacy_parent, "new": new_parent}
        else:
            result["legacy_behavior"] = click_entry(legacy_page, args.frame_name, entry)
            result["new_behavior"] = click_entry(new_page, args.frame_name, entry)
            if not (result["legacy_behavior"]["click"].get("clicked") and result["new_behavior"]["click"].get("clicked")):
                result["blocked"] = {"reason": "Entry click failed in one or both environments.", "legacy": result["legacy_behavior"], "new": result["new_behavior"]}
            else:
                hosts = (args.legacy_host, args.new_host)
                legacy_target = wait_analysis_page(legacy_page.context, args.legacy_host)
                new_target = wait_analysis_page(new_page.context, args.new_host)
                result["target_pages"] = {"legacy": legacy_target.url, "new": new_target.url}
                legacy = capture_page(legacy_target, "legacy_47", output_dir, hosts)
                new = capture_page(new_target, "new_192", output_dir, hosts)
                compare = compare_records(legacy, new, output_dir)
                resources = compare_resources(legacy, new)
                result.update(
                    {
                        "status": effective_status(compare, resources),
                        "legacy": {k: v for k, v in legacy.items() if k not in {"dom", "normalized_dom", "text", "normalized_text"}},
                        "new": {k: v for k, v in new.items() if k not in {"dom", "normalized_dom", "text", "normalized_text"}},
                        "compare": compare,
                        "resources": resources,
                    }
                )
    except Exception as exc:
        result["blocked"] = {"reason": str(exc)}
    report_path = output_dir / "compare_report.html"
    json_path = output_dir / "compare_result.json"
    result["report"] = str(report_path)
    result["json"] = str(json_path)
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(render_entry_report(result, output_dir), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare PATLICS simple analysis dropdown entries.")
    parser.add_argument("--cdp", default="http://127.0.0.1:9222")
    parser.add_argument("--legacy-host", default="192.168.167.47")
    parser.add_argument("--new-host", default="192.168.167.192")
    parser.add_argument("--output-root", default="output/edge_compare")
    parser.add_argument("--frame-name", default="frMain")
    args = parser.parse_args()

    output_root = (ROOT / args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(args.cdp)
        legacy_page = find_parent_page(browser, args.legacy_host)
        new_page = find_parent_page(browser, args.new_host)
        entries = []
        for entry in ENTRIES:
            print(f"Testing {entry['label']}...", flush=True)
            result = run_entry(legacy_page, new_page, entry, args)
            print(f"{entry['label']}: {result['status']}", flush=True)
            entries.append(
                {
                    "entry": result["entry"],
                    "status": result["status"],
                    "output_dir": result["output_dir"],
                    "report": result["report"],
                    "json": result["json"],
                    "blocked": result.get("blocked"),
                    "checks": (result.get("compare") or {}).get("comparisons"),
                    "resources": result.get("resources"),
                }
            )

    status_counts = Counter(item["status"] for item in entries)
    status = "DIFF" if status_counts.get("DIFF") else "BLOCKED" if status_counts.get("BLOCKED") else "PASS"
    summary_dir = output_root / "JpBiblioListSimpleAnalysis_edge"
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "status": status,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "entries": entries,
        "status_counts": dict(status_counts),
    }
    summary_json = summary_dir / "summary_result.json"
    summary_report = summary_dir / "summary_report.html"
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_report.write_text(render_summary(summary, output_root), encoding="utf-8")
    print(json.dumps({"status": status, "status_counts": dict(status_counts), "report": str(summary_report), "json": str(summary_json), "entries": entries}, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
