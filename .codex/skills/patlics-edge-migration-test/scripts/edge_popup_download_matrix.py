from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from playwright.sync_api import sync_playwright

from workspace import workspace_root

ROOT = workspace_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HELPER_PATH = Path(__file__).with_name("edge_popup_download_compare.py")
SPEC = importlib.util.spec_from_file_location("edge_popup_download_compare", HELPER_PATH)
helper = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(helper)


CASES = [
    {"id": "current_default_10_20", "label": "CURRENT / default 10+20", "target": "CURRENT", "kinds": ["10", "20"]},
    {"id": "current_kind_10_only", "label": "CURRENT / kind 10 only", "target": "CURRENT", "kinds": ["10"]},
    {"id": "current_kind_20_only", "label": "CURRENT / kind 20 only", "target": "CURRENT", "kinds": ["20"]},
    {"id": "all_default_10_20", "label": "ALL / default 10+20", "target": "ALL", "kinds": ["10", "20"]},
    {"id": "all_kind_10_only", "label": "ALL / kind 10 only", "target": "ALL", "kinds": ["10"]},
    {"id": "all_kind_20_only", "label": "ALL / kind 20 only", "target": "ALL", "kinds": ["20"]},
]


def set_conditions(page: Any, case: Dict[str, Any]) -> Dict[str, Any]:
    return page.evaluate(
        """(testCase) => {
            const select = document.querySelector('select[name="slTarget"]');
            if (select) {
                select.value = testCase.target;
                select.dispatchEvent(new Event('change', { bubbles: true }));
            }
            const wanted = new Set(testCase.kinds || []);
            for (const checkbox of Array.from(document.querySelectorAll('input[name="cbKind"]'))) {
                checkbox.checked = wanted.has(checkbox.value);
                checkbox.dispatchEvent(new Event('change', { bubbles: true }));
            }
            return {
                target: select ? select.value : "",
                kinds: Array.from(document.querySelectorAll('input[name="cbKind"]')).map((item) => ({
                    value: item.value,
                    checked: item.checked,
                    disabled: item.disabled,
                })),
                pdf: Array.from(document.querySelectorAll('input[name="cbPDF"]')).map((item) => ({
                    value: item.value,
                    checked: item.checked,
                    disabled: item.disabled,
                })),
            };
        }""",
        case,
    )


def run_case(
    browser: Any,
    host: str,
    side: str,
    case: Dict[str, Any],
    output_dir: Path,
    timeout_ms: int,
) -> Dict[str, Any]:
    page = helper.open_choose_save_popup(browser, host)
    state = set_conditions(page, case)
    if case["kinds"] == ["10"]:
        result = helper.run_no_download_case(page, f"{case['id']}_{side}", timeout_ms=min(timeout_ms, 30000))
    else:
        result = helper.run_download_case(page, f"{case['id']}_{side}", output_dir, timeout_ms=timeout_ms)
    result["condition"] = case
    result["applied_state"] = state
    return result


def summarize_case(case: Dict[str, Any], legacy: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    compare = helper.compare_downloads(legacy, new)
    return {
        "id": case["id"],
        "label": case["label"],
        "condition": case,
        "legacy": legacy,
        "new": new,
        "compare": compare,
        "status": compare["status"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp", default="http://127.0.0.1:9222")
    parser.add_argument("--legacy-host", default="192.168.167.47")
    parser.add_argument("--new-host", default="192.168.167.192")
    parser.add_argument("--output-dir", default="output/edge_compare/GazetteChooseSaveDoc_edge")
    parser.add_argument("--download-timeout-ms", type=int, default=60000)
    parser.add_argument("--case", action="append", choices=[case["id"] for case in CASES])
    parser.add_argument("--append-existing", action="store_true")
    args = parser.parse_args()

    output_dir = (ROOT / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "download_matrix_result.json"
    report_path = output_dir / "download_matrix_report.html"
    selected_cases = [case for case in CASES if not args.case or case["id"] in set(args.case)]

    results: List[Dict[str, Any]] = []
    if args.append_existing and json_path.exists():
        existing = json.loads(json_path.read_text(encoding="utf-8"))
        results = list(existing.get("cases") or [])

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp)
        for case in selected_cases:
            legacy = run_case(browser, args.legacy_host, "legacy_47", case, output_dir, args.download_timeout_ms)
            new = run_case(browser, args.new_host, "new_192", case, output_dir, args.download_timeout_ms)
            results = [item for item in results if item.get("id") != case["id"]]
            results.append(summarize_case(case, legacy, new))
            results.sort(key=lambda item: next((idx for idx, known in enumerate(CASES) if known["id"] == item.get("id")), 999))
            partial_status = "PASS" if all(item["status"] == "PASS" for item in results) else "DIFF"
            partial = build_result(partial_status, results)
            json_path.write_text(json.dumps(partial, ensure_ascii=False, indent=2), encoding="utf-8")
            report_path.write_text(render_html(partial, output_dir), encoding="utf-8")
        browser.close()

    status = "PASS" if all(item["status"] == "PASS" for item in results) else "DIFF"
    result = build_result(status, results)
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(render_html(result, output_dir), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": status,
                "report": str(report_path),
                "json": str(json_path),
                "cases": [
                    {
                        "id": item["id"],
                        "status": item["status"],
                        "filename_match": item["compare"]["filename_match"],
                        "normalized_filename_match": item["compare"]["normalized_filename_match"],
                        "size_match": item["compare"]["size_match"],
                        "raw_sha256_match": item["compare"]["sha256_match"],
                        "zip_content_match": item["compare"]["zip_content_match"],
                        "legacy_size": item["compare"]["legacy_size"],
                        "new_size": item["compare"]["new_size"],
                    }
                    for item in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if status == "PASS" else 2


def build_result(status: str, results: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "status": status,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "browser": "Microsoft Edge via CDP",
        "target_path": "GazetteChooseSaveDoc.do",
        "action": "Output",
        "cases": results,
    }


def rel(path: str, base: Path) -> str:
    try:
        return Path(path).resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return path or ""


def render_html(result: Dict[str, Any], output_dir: Path) -> str:
    import html

    rows = []
    for item in result["cases"]:
        cmp = item["compare"]
        if cmp.get("comparison_mode") == "NO_DOWNLOAD":
            outcome = (
                f"No download<br>HTTP {html.escape(str(cmp.get('legacy_response_status')))} / "
                f"{html.escape(str(cmp.get('new_response_status')))}<br>"
                f"Response body match: {html.escape(str(cmp.get('no_download_response_match')))}"
            )
        else:
            outcome = (
                f"Download<br>{html.escape(str(cmp['normalized_filename_match']))}<br>"
                f"{html.escape(str(cmp['legacy_normalized_filename']))}<br>"
                f"{html.escape(str(cmp['new_normalized_filename']))}"
            )
        rows.append(
            "<tr>"
            f"<td>{html.escape(item['label'])}</td>"
            f"<td>{html.escape(item['status'])}</td>"
            f"<td>{html.escape(str(item['condition']['target']))}</td>"
            f"<td>{html.escape(','.join(item['condition']['kinds']))}</td>"
            f"<td>{html.escape(str(cmp['success_match']))}</td>"
            f"<td>{outcome}</td>"
            f"<td>{html.escape(str(cmp['size_match']))}<br>{html.escape(str(cmp['legacy_size']))}<br>{html.escape(str(cmp['new_size']))}</td>"
            f"<td>{html.escape(str(cmp['zip_content_match']))}</td>"
            f"<td>{html.escape(str(cmp['sha256_match']))}</td>"
            f"<td><a href=\"{html.escape(rel(str(cmp.get('legacy_path')), output_dir))}\">.47</a><br><a href=\"{html.escape(rel(str(cmp.get('new_path')), output_dir))}\">.192</a></td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Download matrix compare - {html.escape(result['status'])}</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #172033; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
    th, td {{ border: 1px solid #d4d9e2; padding: 8px; vertical-align: top; }}
    th {{ background: #f4f6f9; text-align: left; }}
    .status {{ display: inline-block; padding: 4px 10px; border-radius: 4px; color: white; background: {'#137333' if result['status'] == 'PASS' else '#b3261e'}; }}
  </style>
</head>
<body>
  <h1>Download matrix compare <span class="status">{html.escape(result['status'])}</span></h1>
  <p>Target: <code>GazetteChooseSaveDoc.do</code> / Action: <code>出力</code></p>
  <table>
    <tr><th>Case</th><th>Status</th><th>Target</th><th>Kinds</th><th>Behavior match</th><th>Outcome</th><th>Size</th><th>ZIP content</th><th>Raw ZIP SHA</th><th>Files</th></tr>
    {''.join(rows)}
  </table>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
