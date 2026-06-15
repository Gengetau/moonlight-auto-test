from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from workspace import workspace_root

ROOT = workspace_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def safe_filename(value: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "")).strip(" ._")
    return text or "download"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_download_filename(filename: str) -> str:
    text = str(filename or "")
    return re.sub(r"(\d{14})(?=\.zip$)", "{TIMESTAMP}", text)


def normalized_response_body(value: str) -> str:
    text = re.sub(r">\s+<", "><", str(value or "").strip())
    return re.sub(r"\s+", " ", text)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def zip_content_manifest(path: Path) -> Dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = []
            for info in archive.infolist():
                data = archive.read(info.filename)
                entries.append(
                    {
                        "filename": info.filename,
                        "file_size": info.file_size,
                        "crc": info.CRC,
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
            return {"ok": True, "entries": entries}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "entries": []}


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


def open_choose_save_popup(browser: Any, host: str) -> Any:
    try:
        return find_page(browser, host, "GazetteChooseSaveDoc.do")
    except RuntimeError:
        pass

    parent = None
    for context in browser.contexts:
        for page in context.pages:
            try:
                if host in page.url and "GazetteForBiblioList.do" in page.url:
                    parent = page
                    break
            except PlaywrightError:
                pass
        if parent:
            break
    if parent is None:
        return find_page(browser, host, "GazetteChooseSaveDoc.do")

    frame = next((item for item in parent.frames if item.name == "frMenuFrame"), None)
    if frame is None:
        return find_page(browser, host, "GazetteChooseSaveDoc.do")

    with parent.context.expect_page(timeout=10000) as popup_info:
        try:
            frame.locator("a[onclick*='fnSaveDocs']").click(timeout=5000)
        except PlaywrightError:
            frame.evaluate(
                """() => {
                    if (typeof fnSaveDocs === 'function') {
                        fnSaveDocs();
                        return;
                    }
                    const link = Array.from(document.querySelectorAll('a')).find((item) =>
                        (item.getAttribute('onclick') || '').includes('fnSaveDocs')
                    );
                    if (!link) throw new Error('fnSaveDocs entry not found');
                    link.click();
                }"""
            )
    popup = popup_info.value
    popup.wait_for_load_state("domcontentloaded", timeout=10000)
    popup.wait_for_timeout(600)
    return popup


def run_download_case(page: Any, side_id: str, output_dir: Path, timeout_ms: int = 45000) -> Dict[str, Any]:
    page.bring_to_front()
    page.wait_for_timeout(300)
    side_dir = output_dir / "downloads" / side_id
    side_dir.mkdir(parents=True, exist_ok=True)
    dialogs = []
    result: Dict[str, Any] = {
        "side": side_id,
        "source_url": page.url,
        "source_title": page.title(),
        "selector": "input[name=btSave]",
        "download_timeout_ms": timeout_ms,
    }

    def handle_dialog(dialog: Any) -> None:
        dialogs.append({"type": dialog.type, "message": dialog.message})
        dialog.accept()

    expected_host = urlparse(result["source_url"]).netloc

    def is_download_from_page(download: Any) -> bool:
        try:
            parsed = urlparse(download.url)
            return parsed.netloc == expected_host and parsed.path.endswith("/GazetteSaveDocs.do")
        except Exception:
            return False

    page.on("dialog", handle_dialog)
    try:
        with page.context.expect_event("download", predicate=is_download_from_page, timeout=timeout_ms) as download_info:
            page.evaluate(
                """() => {
                    const button = document.querySelector('input[name="btSave"]');
                    if (!button) throw new Error('btSave not found');
                    button.click();
                }"""
            )
        download = download_info.value
        suggested = download.suggested_filename
        save_path = side_dir / safe_filename(suggested)
        download.save_as(str(save_path))
        result.update(
            {
                "status": "PASS",
                "download_filename": suggested,
                "normalized_download_filename": normalized_download_filename(suggested),
                "saved_path": str(save_path),
                "saved_filename": save_path.name,
                "download_size": save_path.stat().st_size,
                "download_sha256": sha256_file(save_path),
                "zip_content": zip_content_manifest(save_path),
                "dialogs": dialogs,
            }
        )
    except PlaywrightTimeoutError as exc:
        result.update({"status": "BLOCKED", "reason": f"Download event timeout: {exc}", "dialogs": dialogs})
    except Exception as exc:
        result.update({"status": "BLOCKED", "reason": str(exc), "dialogs": dialogs})
    finally:
        try:
            page.remove_listener("dialog", handle_dialog)
        except PlaywrightError:
            pass
        try:
            if not page.is_closed():
                page.close()
        except PlaywrightError:
            pass
    return result


def run_no_download_case(page: Any, side_id: str, timeout_ms: int = 30000) -> Dict[str, Any]:
    page.bring_to_front()
    page.wait_for_timeout(300)
    result: Dict[str, Any] = {
        "side": side_id,
        "source_url": page.url,
        "source_title": page.title(),
        "selector": "input[name=btSave]",
        "response_timeout_ms": timeout_ms,
    }
    expected_host = urlparse(result["source_url"]).netloc

    def is_save_response(response: Any) -> bool:
        parsed = urlparse(response.url)
        return parsed.netloc == expected_host and parsed.path.endswith("/GazetteSaveDocs.do")

    try:
        with page.context.expect_event("response", predicate=is_save_response, timeout=timeout_ms) as response_info:
            page.evaluate(
                """() => {
                    const button = document.querySelector('input[name="btSave"]');
                    if (!button) throw new Error('btSave not found');
                    button.click();
                }"""
            )
        response = response_info.value
        body = response.body().decode("utf-8", errors="replace")
        normalized_body = normalized_response_body(body)
        content_type = response.headers.get("content-type", "")
        expected_message = "ファイルが存在しません。" in body
        result.update(
            {
                "status": "NO_DOWNLOAD" if response.status == 200 and expected_message else "BLOCKED",
                "response_status": response.status,
                "response_content_type": content_type,
                "response_body": body,
                "normalized_response_body_sha256": sha256_text(normalized_body),
                "expected_no_file_message": expected_message,
            }
        )
    except PlaywrightTimeoutError as exc:
        result.update({"status": "BLOCKED", "reason": f"Response event timeout: {exc}"})
    except Exception as exc:
        result.update({"status": "BLOCKED", "reason": str(exc)})
    finally:
        try:
            if not page.is_closed():
                page.close()
        except PlaywrightError:
            pass
    return result


def compare_downloads(legacy: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    legacy_success = legacy.get("status") == "PASS"
    new_success = new.get("status") == "PASS"
    legacy_no_download = legacy.get("status") == "NO_DOWNLOAD"
    new_no_download = new.get("status") == "NO_DOWNLOAD"
    filename_match = legacy.get("download_filename") == new.get("download_filename")
    normalized_filename_match = legacy.get("normalized_download_filename") == new.get("normalized_download_filename")
    size_match = legacy.get("download_size") == new.get("download_size")
    sha256_match = legacy.get("download_sha256") == new.get("download_sha256")
    zip_content_match = (legacy.get("zip_content") or {}).get("entries") == (new.get("zip_content") or {}).get("entries")
    no_download_response_match = all(
        [
            legacy_no_download,
            new_no_download,
            legacy.get("response_status") == new.get("response_status"),
            legacy.get("response_content_type") == new.get("response_content_type"),
            legacy.get("normalized_response_body_sha256") == new.get("normalized_response_body_sha256"),
            legacy.get("expected_no_file_message") is True,
            new.get("expected_no_file_message") is True,
        ]
    )
    download_match = all([legacy_success, new_success, normalized_filename_match, size_match, zip_content_match])
    effective_pass = download_match or no_download_response_match
    return {
        "status": "PASS" if effective_pass else "DIFF",
        "raw_status": "PASS" if all([legacy_success, new_success, filename_match, size_match, sha256_match]) else "DIFF",
        "comparison_mode": "NO_DOWNLOAD" if no_download_response_match else "DOWNLOAD",
        "success_match": legacy.get("status") == new.get("status"),
        "legacy_success": legacy_success,
        "new_success": new_success,
        "legacy_no_download": legacy_no_download,
        "new_no_download": new_no_download,
        "no_download_response_match": no_download_response_match,
        "legacy_response_status": legacy.get("response_status"),
        "new_response_status": new.get("response_status"),
        "legacy_response_content_type": legacy.get("response_content_type"),
        "new_response_content_type": new.get("response_content_type"),
        "legacy_response_body_sha256": legacy.get("normalized_response_body_sha256"),
        "new_response_body_sha256": new.get("normalized_response_body_sha256"),
        "filename_match": filename_match,
        "normalized_filename_match": normalized_filename_match,
        "size_match": size_match,
        "sha256_match": sha256_match,
        "zip_content_match": zip_content_match,
        "legacy_filename": legacy.get("download_filename"),
        "new_filename": new.get("download_filename"),
        "legacy_normalized_filename": legacy.get("normalized_download_filename"),
        "new_normalized_filename": new.get("normalized_download_filename"),
        "legacy_size": legacy.get("download_size"),
        "new_size": new.get("download_size"),
        "legacy_sha256": legacy.get("download_sha256"),
        "new_sha256": new.get("download_sha256"),
        "legacy_zip_content": legacy.get("zip_content"),
        "new_zip_content": new.get("zip_content"),
        "legacy_path": legacy.get("saved_path"),
        "new_path": new.get("saved_path"),
    }


def rel(path: str, base: Path) -> str:
    try:
        return Path(path).resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return path or ""


def html_report(result: Dict[str, Any], output_dir: Path) -> str:
    cmp = result["compare"]
    status = result["status"]
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Edge download compare - {html.escape(status)}</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #172033; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
    th, td {{ border: 1px solid #d4d9e2; padding: 8px; vertical-align: top; }}
    th {{ background: #f4f6f9; text-align: left; }}
    code {{ word-break: break-all; }}
    .status {{ display: inline-block; padding: 4px 10px; border-radius: 4px; color: white; background: {'#137333' if status == 'PASS' else '#b3261e'}; }}
  </style>
</head>
<body>
  <h1>Edge download compare <span class="status">{html.escape(status)}</span></h1>
  <p>Target: <code>GazetteChooseSaveDoc.do</code> / action: <code>出力</code></p>
  <table>
    <tr><th>Item</th><th>Result</th></tr>
    <tr><td>Success</td><td>{html.escape(str(cmp['success_match']))} / .47={html.escape(str(cmp['legacy_success']))} / .192={html.escape(str(cmp['new_success']))}</td></tr>
    <tr><td>Filename raw</td><td>{html.escape(str(cmp['filename_match']))}<br>{html.escape(str(cmp.get('legacy_filename')))}<br>{html.escape(str(cmp.get('new_filename')))}</td></tr>
    <tr><td>Filename normalized</td><td>{html.escape(str(cmp['normalized_filename_match']))}<br>{html.escape(str(cmp.get('legacy_normalized_filename')))}<br>{html.escape(str(cmp.get('new_normalized_filename')))}</td></tr>
    <tr><td>Size</td><td>{html.escape(str(cmp['size_match']))}<br>{html.escape(str(cmp.get('legacy_size')))}<br>{html.escape(str(cmp.get('new_size')))}</td></tr>
    <tr><td>Raw ZIP SHA-256</td><td>{html.escape(str(cmp['sha256_match']))}<br><code>{html.escape(str(cmp.get('legacy_sha256')))}</code><br><code>{html.escape(str(cmp.get('new_sha256')))}</code></td></tr>
    <tr><td>ZIP content</td><td>{html.escape(str(cmp['zip_content_match']))}<br><pre>{html.escape(json.dumps(cmp.get('legacy_zip_content'), ensure_ascii=False, indent=2))}</pre><pre>{html.escape(json.dumps(cmp.get('new_zip_content'), ensure_ascii=False, indent=2))}</pre></td></tr>
    <tr><td>Files</td><td><a href="{html.escape(rel(str(cmp.get('legacy_path')), output_dir))}">.47 file</a><br><a href="{html.escape(rel(str(cmp.get('new_path')), output_dir))}">.192 file</a></td></tr>
  </table>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp", default="http://127.0.0.1:9222")
    parser.add_argument("--legacy-host", default="192.168.167.47")
    parser.add_argument("--new-host", default="192.168.167.192")
    parser.add_argument("--output-dir", default="output/edge_compare/GazetteChooseSaveDoc_edge")
    args = parser.parse_args()

    output_dir = (ROOT / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp)
        legacy_page = open_choose_save_popup(browser, args.legacy_host)
        new_page = open_choose_save_popup(browser, args.new_host)
        legacy = run_download_case(legacy_page, "legacy_47", output_dir)
        new = run_download_case(new_page, "new_192", output_dir)
        browser.close()

    compare = compare_downloads(legacy, new)
    result = {
        "status": compare["status"],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "browser": "Microsoft Edge via CDP",
        "target_path": "GazetteChooseSaveDoc.do",
        "action": "出力",
        "legacy": legacy,
        "new": new,
        "compare": compare,
    }
    json_path = output_dir / "download_compare_result.json"
    report_path = output_dir / "download_compare_report.html"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(html_report(result, output_dir), encoding="utf-8")
    print(json.dumps({"status": result["status"], "report": str(report_path), "json": str(json_path), "compare": compare}, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
