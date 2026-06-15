from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.request import urlopen

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from workspace import workspace_root

ROOT = workspace_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def safe_filename(value: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "")).strip(" ._")
    return text or "print_page"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_pdf_sha256(path: Path) -> str:
    data = path.read_bytes()
    data = re.sub(
        rb"/(CreationDate|ModDate) \(D:[^)]*\)",
        lambda match: b"/" + match.group(1) + b" (D:{PDF_DATE})",
        data,
    )
    return hashlib.sha256(data).hexdigest()


def cdp_targets(cdp_base: str) -> List[Dict[str, Any]]:
    url = cdp_base.rstrip("/") + "/json/list"
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def print_target_ids(cdp_base: str) -> set[str]:
    return {
        str(item.get("id"))
        for item in cdp_targets(cdp_base)
        if item.get("type") == "page" and str(item.get("url") or "").startswith("edge://print")
    }


def close_print_pages(cdp_base: str) -> None:
    for item in cdp_targets(cdp_base):
        if item.get("type") == "page" and str(item.get("url") or "").startswith("edge://print"):
            try:
                with urlopen(cdp_base.rstrip("/") + "/json/close/" + str(item.get("id")), timeout=2):
                    pass
            except Exception:
                pass


def wait_new_print_targets(cdp_base: str, before: set[str], timeout_sec: float = 8.0) -> List[Dict[str, Any]]:
    deadline = time.time() + timeout_sec
    latest: List[Dict[str, Any]] = []
    while time.time() < deadline:
        targets = cdp_targets(cdp_base)
        latest = [
            item
            for item in targets
            if item.get("type") == "page"
            and str(item.get("url") or "").startswith("edge://print")
            and str(item.get("id")) not in before
        ]
        if latest:
            return latest
        time.sleep(0.25)
    return latest


def find_page(browser: Any, host: str, path_fragment: str) -> Any:
    matches = []
    for context in browser.contexts:
        for page in context.pages:
            try:
                if host in page.url and path_fragment in page.url:
                    matches.append(page)
            except PlaywrightError:
                pass
    if not matches:
        seen = []
        for context in browser.contexts:
            for page in context.pages:
                try:
                    seen.append(page.url)
                except PlaywrightError:
                    pass
        raise RuntimeError(f"No page found for host={host!r}, path={path_fragment!r}. Seen URLs: {seen}")
    return matches[-1]


def save_pdf_with_cdp(page: Any, pdf_path: Path) -> Dict[str, Any]:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    session = page.context.new_cdp_session(page)
    try:
        payload = session.send(
            "Page.printToPDF",
            {
                "printBackground": True,
                "preferCSSPageSize": True,
                "landscape": True,
            },
        )
        data = payload.get("data")
        if not data:
            raise RuntimeError("Page.printToPDF returned no data")
        pdf_path.write_bytes(base64.b64decode(str(data)))
    finally:
        try:
            session.detach()
        except PlaywrightError:
            pass

    return {
        "pdf_path": str(pdf_path),
        "pdf_filename": pdf_path.name,
        "pdf_size": pdf_path.stat().st_size,
        "pdf_sha256": sha256_file(pdf_path),
        "pdf_normalized_sha256": normalized_pdf_sha256(pdf_path),
        "pdf_backend": "cdp_print_to_pdf",
    }


def run_button_case(
    *,
    page: Any,
    cdp_base: str,
    selector: str,
    action_id: str,
    side_id: str,
    output_dir: Path,
) -> Dict[str, Any]:
    page.bring_to_front()
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except PlaywrightError:
        pass
    page.wait_for_timeout(400)

    title = safe_filename(page.title() or "テキスト印刷")
    pdf_name = f"{title}.pdf"
    pdf_path = output_dir / "pdf" / action_id / side_id / pdf_name

    before = print_target_ids(cdp_base)
    click_error = ""
    click_status = "PASS_JS"
    try:
        clicked = page.evaluate(
            """(selector) => {
                const element = document.querySelector(selector);
                if (!element) return false;
                element.click();
                return true;
            }""",
            selector,
        )
        if not clicked:
            click_status = "BLOCKED"
            click_error = f"Element not found: {selector}"
    except PlaywrightError as exc:
        click_status = "BLOCKED"
        click_error = str(exc)

    new_print_targets = wait_new_print_targets(cdp_base, before, timeout_sec=10)
    pdf_result = save_pdf_with_cdp(page, pdf_path)

    return {
        "side": side_id,
        "action_id": action_id,
        "selector": selector,
        "source_url": page.url,
        "source_title": page.title(),
        "click_status": click_status,
        "click_error": click_error,
        "print_preview_opened": bool(new_print_targets),
        "new_print_targets": [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "url": item.get("url"),
            }
            for item in new_print_targets
        ],
        **pdf_result,
    }


def compare_pdf_pair(legacy: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    filename_match = legacy.get("pdf_filename") == new.get("pdf_filename")
    size_match = legacy.get("pdf_size") == new.get("pdf_size")
    sha256_match = legacy.get("pdf_sha256") == new.get("pdf_sha256")
    normalized_sha256_match = legacy.get("pdf_normalized_sha256") == new.get("pdf_normalized_sha256")
    preview_match = bool(legacy.get("print_preview_opened")) == bool(new.get("print_preview_opened"))
    return {
        "status": "PASS" if all([filename_match, size_match, normalized_sha256_match, preview_match]) else "DIFF",
        "filename_match": filename_match,
        "size_match": size_match,
        "sha256_match": sha256_match,
        "normalized_sha256_match": normalized_sha256_match,
        "print_preview_opened_match": preview_match,
        "legacy_pdf_filename": legacy.get("pdf_filename"),
        "new_pdf_filename": new.get("pdf_filename"),
        "legacy_pdf_size": legacy.get("pdf_size"),
        "new_pdf_size": new.get("pdf_size"),
        "legacy_pdf_sha256": legacy.get("pdf_sha256"),
        "new_pdf_sha256": new.get("pdf_sha256"),
        "legacy_pdf_normalized_sha256": legacy.get("pdf_normalized_sha256"),
        "new_pdf_normalized_sha256": new.get("pdf_normalized_sha256"),
        "legacy_pdf_path": legacy.get("pdf_path"),
        "new_pdf_path": new.get("pdf_path"),
    }


def rel(path: str, base: Path) -> str:
    try:
        return Path(path).resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return path


def html_report(result: Dict[str, Any], output_dir: Path) -> str:
    rows = []
    for action in result["actions"]:
        cmp = action["compare"]
        legacy = action["legacy"]
        new = action["new"]
        rows.append(
            "<tr>"
            f"<td>{html.escape(action['label'])}</td>"
            f"<td class=\"{'ok' if cmp['status'] == 'PASS' else 'ng'}\">{html.escape(cmp['status'])}</td>"
            f"<td>{html.escape(str(cmp['filename_match']))}<br>{html.escape(str(cmp['legacy_pdf_filename']))}<br>{html.escape(str(cmp['new_pdf_filename']))}</td>"
            f"<td>{html.escape(str(cmp['size_match']))}<br>{html.escape(str(cmp['legacy_pdf_size']))}<br>{html.escape(str(cmp['new_pdf_size']))}</td>"
            f"<td>{html.escape(str(cmp['sha256_match']))}<br><code>{html.escape(str(cmp['legacy_pdf_sha256']))}</code><br><code>{html.escape(str(cmp['new_pdf_sha256']))}</code></td>"
            f"<td>{html.escape(str(cmp['normalized_sha256_match']))}<br><code>{html.escape(str(cmp['legacy_pdf_normalized_sha256']))}</code><br><code>{html.escape(str(cmp['new_pdf_normalized_sha256']))}</code></td>"
            f"<td>{html.escape(str(cmp['print_preview_opened_match']))}<br>.47={html.escape(str(legacy.get('print_preview_opened')))} / .192={html.escape(str(new.get('print_preview_opened')))}</td>"
            f"<td><a href=\"{html.escape(rel(str(legacy.get('pdf_path')), output_dir))}\">.47 PDF</a><br><a href=\"{html.escape(rel(str(new.get('pdf_path')), output_dir))}\">.192 PDF</a></td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Edge PDF compare - {html.escape(result['status'])}</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #172033; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
    th, td {{ border: 1px solid #d4d9e2; padding: 8px; vertical-align: top; }}
    th {{ background: #f4f6f9; text-align: left; }}
    code {{ word-break: break-all; }}
    .status {{ display: inline-block; padding: 4px 10px; border-radius: 4px; color: white; background: {'#137333' if result['status'] == 'PASS' else '#b3261e'}; }}
    .ok {{ color: #137333; font-weight: 600; }}
    .ng {{ color: #b3261e; font-weight: 600; }}
  </style>
</head>
<body>
  <h1>Edge PDF compare <span class="status">{html.escape(result['status'])}</span></h1>
  <p>Target: <code>{html.escape(result['target_path'])}</code> / legacy: <code>{html.escape(result['legacy_host'])}</code> / new: <code>{html.escape(result['new_host'])}</code></p>
  <table>
    <tr><th>Button</th><th>Status</th><th>Filename</th><th>Size</th><th>Raw SHA-256</th><th>Normalized SHA-256</th><th>Print preview opened</th><th>Files</th></tr>
    {''.join(rows)}
  </table>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp", default="http://127.0.0.1:9222")
    parser.add_argument("--legacy-host", default="192.168.167.47")
    parser.add_argument("--new-host", default="192.168.167.192")
    parser.add_argument("--path", default="JpGazetteTextprint.do")
    parser.add_argument("--output-dir", default="output/edge_compare/JpGazetteTextprint_edge")
    args = parser.parse_args()

    output_dir = (ROOT / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    actions = [
        {"id": "print", "label": "印刷", "selector": "#print"},
        {"id": "preview", "label": "印刷プレビュー", "selector": "#preview"},
    ]

    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp)
        legacy_page = find_page(browser, args.legacy_host, args.path)
        new_page = find_page(browser, args.new_host, args.path)

        for action in actions:
            close_print_pages(args.cdp)
            time.sleep(0.5)
            legacy = run_button_case(
                page=legacy_page,
                cdp_base=args.cdp,
                selector=action["selector"],
                action_id=action["id"],
                side_id="legacy_47",
                output_dir=output_dir,
            )
            close_print_pages(args.cdp)
            time.sleep(0.5)
            new = run_button_case(
                page=new_page,
                cdp_base=args.cdp,
                selector=action["selector"],
                action_id=action["id"],
                side_id="new_192",
                output_dir=output_dir,
            )
            results.append(
                {
                    **action,
                    "legacy": legacy,
                    "new": new,
                    "compare": compare_pdf_pair(legacy, new),
                }
            )

        browser.close()

    status = "PASS" if all(item["compare"]["status"] == "PASS" for item in results) else "DIFF"
    result = {
        "status": status,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_path": args.path,
        "legacy_host": args.legacy_host,
        "new_host": args.new_host,
        "browser": "Microsoft Edge via CDP",
        "pdf_backend": "cdp_print_to_pdf",
        "actions": results,
    }
    json_path = output_dir / "pdf_compare_result.json"
    report_path = output_dir / "pdf_compare_report.html"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(html_report(result, output_dir), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": status,
                "report": str(report_path),
                "json": str(json_path),
                "actions": [
                    {
                        "id": item["id"],
                        "status": item["compare"]["status"],
                        "filename_match": item["compare"]["filename_match"],
                        "size_match": item["compare"]["size_match"],
                        "sha256_match": item["compare"]["sha256_match"],
                        "normalized_sha256_match": item["compare"]["normalized_sha256_match"],
                        "print_preview_opened_match": item["compare"]["print_preview_opened_match"],
                    }
                    for item in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
