from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urljoin
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

from workspace import workspace_root

ROOT = workspace_root()
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from edge_interaction_compare import (  # noqa: E402
    build_result,
    enumerate_cases,
    render_interaction_report,
    run_case,
    status_counts_for,
    write_result_files,
)
from edge_simple_analysis_compare import (  # noqa: E402
    ENTRIES,
    click_entry,
    find_parent_page,
    restore_parent,
)


def close_aux_pages(context: Any) -> List[str]:
    closed = []
    for page in list(context.pages):
        try:
            url = page.url
        except Exception:
            continue
        if url.startswith("edge://downloads-hub/") or url.startswith("edge://print"):
            closed.append(url)
            try:
                page.close()
            except Exception:
                pass
    return closed


def close_analysis_pages(context: Any) -> List[str]:
    closed = []
    for page in list(context.pages):
        try:
            url = page.url
        except Exception:
            continue
        if "JpAnalyzeListDocForBiblioList.do" in url or "JpAnalyzeRankingPrint.do" in url:
            closed.append(url)
            try:
                page.close()
            except Exception:
                pass
    return closed


def wait_new_analysis_page(context: Any, host: str, known_pages: List[Any], timeout_ms: int = 30000) -> Any:
    known = set(known_pages)
    deadline = datetime.now().timestamp() + timeout_ms / 1000
    last_match = None
    while datetime.now().timestamp() < deadline:
        matches = [
            page
            for page in context.pages
            if page not in known and host in page.url and "JpAnalyzeListDocForBiblioList.do" in page.url
        ]
        if not matches:
            matches = [
                page
                for page in context.pages
                if host in page.url and "JpAnalyzeListDocForBiblioList.do" in page.url
            ]
        if matches:
            last_match = matches[-1]
            try:
                last_match.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            last_match.wait_for_timeout(1000)
            return last_match
        context.pages[0].wait_for_timeout(500)
    seen = [page.url for page in context.pages]
    raise RuntimeError(f"Analysis page not found for host={host}. Seen pages: {seen}; last_match={last_match}")


def open_entry_target(legacy_parent: Any, new_parent: Any, entry: Dict[str, str], args: argparse.Namespace) -> Dict[str, Any]:
    close_aux_pages(legacy_parent.context)
    close_aux_pages(new_parent.context)
    closed_legacy = close_analysis_pages(legacy_parent.context)
    closed_new = close_analysis_pages(new_parent.context)
    restore_legacy = restore_parent(legacy_parent, args.frame_name, entry["code"])
    restore_new = restore_parent(new_parent, args.frame_name, entry["code"])
    legacy_known = list(legacy_parent.context.pages)
    new_known = list(new_parent.context.pages)
    legacy_behavior = click_entry(legacy_parent, args.frame_name, entry)
    new_behavior = click_entry(new_parent, args.frame_name, entry)
    legacy_target = wait_new_analysis_page(legacy_parent.context, args.legacy_host, legacy_known)
    new_target = wait_new_analysis_page(new_parent.context, args.new_host, new_known)
    return {
        "legacy_target": legacy_target,
        "new_target": new_target,
        "parent_restore": {"legacy": restore_legacy, "new": restore_new},
        "legacy_behavior": legacy_behavior,
        "new_behavior": new_behavior,
        "closed_analysis_pages": {"legacy": closed_legacy, "new": closed_new},
    }


def close_cdp_aux_targets(cdp: str) -> List[str]:
    """Close Edge targets that can block Playwright CDP attachment."""
    closed: List[str] = []
    root = cdp.rstrip("/") + "/"
    try:
        pages = json.loads(urlopen(urljoin(root, "json/list"), timeout=5).read().decode("utf-8"))
    except Exception:
        return closed
    for target in pages:
        url = target.get("url") or ""
        target_id = target.get("id")
        if not target_id:
            continue
        if (
            url.startswith("edge://downloads")
            or url.startswith("edge://print")
            or url.startswith("edge://newtab")
            or "JpAnalyzeListDocForBiblioList.do" in url
            or "JpAnalyzeRankingPrint.do" in url
        ):
            try:
                urlopen(urljoin(root, f"json/close/{target_id}"), timeout=5).read()
                closed.append(url)
            except Exception:
                pass
    return closed


def wanted_behaviors(args: argparse.Namespace) -> set[str]:
    return {item.strip() for value in (args.only_behavior or []) for item in value.split(",") if item.strip()}


def enumerate_when_ready(page: Any, args: argparse.Namespace, wanted: set[str]) -> List[Dict[str, Any]]:
    deadline = datetime.now().timestamp() + 20
    latest: List[Dict[str, Any]] = []
    while datetime.now().timestamp() < deadline:
        latest = enumerate_cases(page, args.max_options)
        if not wanted or any(str(case.get("behavior_type") or "") in wanted for case in latest):
            return latest
        page.wait_for_timeout(500)
    return latest


def run_entry_interactions(
    legacy_parent: Any,
    new_parent: Any,
    entry: Dict[str, str],
    args: argparse.Namespace,
) -> Dict[str, Any]:
    output_dir = (ROOT / args.output_root / entry["interaction_dir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    result: Dict[str, Any] = {
        "entry": entry,
        "status": "BLOCKED",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(output_dir),
        "cases": [],
    }
    try:
        opened = open_entry_target(legacy_parent, new_parent, entry, args)
        legacy_target = opened["legacy_target"]
        new_target = opened["new_target"]
        result["parent_restore"] = opened["parent_restore"]
        result["legacy_behavior"] = opened["legacy_behavior"]
        result["new_behavior"] = opened["new_behavior"]
        result["closed_analysis_pages"] = opened["closed_analysis_pages"]
        result["target_pages"] = {"legacy": legacy_target.url, "new": new_target.url}

        wanted = wanted_behaviors(args)
        legacy_cases = enumerate_when_ready(legacy_target, args, wanted)
        new_cases = enumerate_when_ready(new_target, args, wanted)
        selected = legacy_cases
        if wanted:
            selected = [case for case in selected if str(case.get("behavior_type") or "") in wanted]
        selected = selected[: args.max_cases]
        cases: List[Dict[str, Any]] = []
        for index, case in enumerate(selected, start=1):
            close_aux_pages(legacy_target.context)
            close_aux_pages(new_target.context)
            if index > 1:
                opened = open_entry_target(legacy_parent, new_parent, entry, args)
                legacy_target = opened["legacy_target"]
                new_target = opened["new_target"]
            try:
                cases.append(run_case(case, index, legacy_target, new_target, output_dir, (args.legacy_host, args.new_host), args))
            except Exception as exc:
                cases.append(
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
                if "Target page, context or browser has been closed" in str(exc) or "no object with guid" in str(exc):
                    break
            partial = build_result(args, cases, len(legacy_cases), len(new_cases))
            partial.update(
                {
                    "entry": entry,
                    "parent_restore": result.get("parent_restore"),
                    "legacy_behavior": result.get("legacy_behavior"),
                    "new_behavior": result.get("new_behavior"),
                    "target_pages": result.get("target_pages"),
                }
            )
            write_result_files(partial, output_dir)
            print(
                f"{entry['label']} case {index}/{len(selected)} {cases[-1].get('status')} {cases[-1].get('action')} {cases[-1].get('selector')}",
                flush=True,
            )

        final = build_result(args, cases, len(legacy_cases), len(new_cases))
        final.update(
            {
                "entry": entry,
                "parent_restore": result.get("parent_restore"),
                "legacy_behavior": result.get("legacy_behavior"),
                "new_behavior": result.get("new_behavior"),
                "target_pages": result.get("target_pages"),
            }
        )
        result_path, report_path = write_result_files(final, output_dir)
        final["json"] = str(result_path)
        final["report"] = str(report_path)
        final["output_dir"] = str(output_dir)
        return final
    except Exception as exc:
        result["blocked"] = str(exc)
        result["coverage"] = {
            "legacy_enumerated": 0,
            "new_enumerated": 0,
            "executed_or_skipped": 0,
            "max_cases": args.max_cases,
            "status_counts": {"BLOCKED": 1},
        }
        result_path = output_dir / "interaction_result.json"
        report_path = output_dir / "interaction_report.html"
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        report_path.write_text(render_interaction_report(result, output_dir), encoding="utf-8")
        result["json"] = str(result_path)
        result["report"] = str(report_path)
        return result


def render_summary(summary: Dict[str, Any], output_root: Path) -> str:
    rows = []
    for item in summary.get("entries", []):
        status = item.get("status")
        cls = "ok" if status == "PASS" else "blocked" if status == "BLOCKED" else "ng"
        coverage = item.get("coverage") or {}
        rows.append(
            "<tr>"
            f"<td>{html.escape(str((item.get('entry') or {}).get('label')))}</td>"
            f"<td class=\"{cls}\">{html.escape(str(status))}</td>"
            f"<td><pre>{html.escape(json.dumps(coverage, ensure_ascii=False, indent=2))}</pre></td>"
            f"<td><a href=\"{html.escape(Path(item.get('report', '')).resolve().relative_to(output_root.resolve()).as_posix() if item.get('report') else '')}\">report</a></td>"
            f"<td>{html.escape(str(item.get('output_dir')))}</td>"
            f"<td><pre>{html.escape(json.dumps(item.get('blocked'), ensure_ascii=False, indent=2) if item.get('blocked') else '')}</pre></td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>Simple Analysis Interaction Summary</title>
  <style>
    body {{ font-family: Segoe UI, Yu Gothic UI, sans-serif; margin: 24px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d4d9e2; padding: 8px; vertical-align: top; }}
    th {{ background: #f4f6f9; text-align: left; }}
    .ok {{ color: #137333; font-weight: 700; }}
    .ng {{ color: #b3261e; font-weight: 700; }}
    .blocked {{ color: #8a5a00; font-weight: 700; }}
    pre {{ white-space: pre-wrap; margin: 0; }}
  </style>
</head>
<body>
  <h1>Simple Analysis Interaction Summary</h1>
  <p>Status: <b>{html.escape(str(summary.get('status')))}</b></p>
  <table>
    <tr><th>Entry</th><th>Status</th><th>Coverage</th><th>Report</th><th>Output</th><th>Blocked</th></tr>
    {''.join(rows)}
  </table>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Run interaction tests for all simple analysis dropdown entries.")
    parser.add_argument("--cdp", default="http://127.0.0.1:9222")
    parser.add_argument("--legacy-host", default="192.168.167.47")
    parser.add_argument("--new-host", default="192.168.167.192")
    parser.add_argument("--output-root", default="output/edge_compare")
    parser.add_argument("--path", default="JpAnalyzeListDocForBiblioList.do")
    parser.add_argument("--frame-name", default="frMain")
    parser.add_argument("--max-cases", type=int, default=20)
    parser.add_argument("--max-options", type=int, default=50)
    parser.add_argument("--wait-ms", type=int, default=900)
    parser.add_argument("--settle-ms", type=int, default=500)
    parser.add_argument("--artifact-timeout-ms", type=int, default=60000)
    parser.add_argument("--test-value", default="codex-interaction-test")
    parser.add_argument("--include-dangerous", action="store_true")
    parser.add_argument("--keep-new-pages", action="store_true")
    parser.add_argument("--start-entry", choices=[entry["key"] for entry in ENTRIES], help="Start from this entry key and continue through the remaining entries.")
    parser.add_argument("--only-behavior", action="append", help="Only execute cases with these behavior types, comma-separated or repeated.")
    parser.add_argument("--output-tag", default="interaction", help="Output tag used in per-entry and summary directories.")
    args = parser.parse_args()

    output_root = (ROOT / args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    entries = [
        {**entry, "interaction_dir": entry["dir"].replace("_edge", f"_{args.output_tag}_edge")}
        for entry in ENTRIES
    ]
    if args.start_entry:
        start_index = next(index for index, entry in enumerate(entries) if entry["key"] == args.start_entry)
        entries = entries[start_index:]
    results = []
    preclosed_targets = close_cdp_aux_targets(args.cdp)
    if preclosed_targets:
        print(f"Preclosed auxiliary targets: {len(preclosed_targets)}", flush=True)
    print(f"Connecting Edge CDP: {args.cdp}", flush=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp, timeout=20000)
        print("Connected Edge CDP", flush=True)
        print("Finding parent pages...", flush=True)
        legacy_parent = find_parent_page(browser, args.legacy_host)
        new_parent = find_parent_page(browser, args.new_host)
        print(f"Legacy parent: {legacy_parent.url}", flush=True)
        print(f"New parent: {new_parent.url}", flush=True)
        for entry in entries:
            print(f"Testing interaction {entry['label']}...", flush=True)
            result = run_entry_interactions(legacy_parent, new_parent, entry, args)
            print(f"{entry['label']}: {result['status']}", flush=True)
            results.append(
                {
                    "entry": result.get("entry"),
                    "status": result.get("status"),
                    "coverage": result.get("coverage"),
                    "output_dir": result.get("output_dir"),
                    "report": result.get("report"),
                    "json": result.get("json"),
                    "blocked": result.get("blocked"),
                }
            )
        browser.close()

    counts = Counter(item.get("status") for item in results)
    status = "DIFF" if counts.get("DIFF") else "BLOCKED" if counts.get("BLOCKED") else "PASS"
    summary_dir = output_root / f"JpBiblioListSimpleAnalysis_{args.output_tag}_edge"
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "status": status,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "preclosed_targets": preclosed_targets,
        "entries": results,
        "status_counts": dict(counts),
    }
    summary_json = summary_dir / "summary_result.json"
    summary_report = summary_dir / "summary_report.html"
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_report.write_text(render_summary(summary, output_root), encoding="utf-8")
    print(json.dumps({"status": status, "report": str(summary_report), "json": str(summary_json), "status_counts": dict(counts), "entries": results}, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
