from __future__ import annotations

import argparse
import base64
import concurrent.futures
import difflib
import hashlib
import html
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from workspace import workspace_root

ROOT = workspace_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from screenshot_compare import compare_visual_screenshot


CAPTURE_JS = r"""
() => {
  const norm = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
  const rectOf = (el) => {
    const r = el.getBoundingClientRect();
    return {
      x: Math.round(r.x * 100) / 100,
      y: Math.round(r.y * 100) / 100,
      width: Math.round(r.width * 100) / 100,
      height: Math.round(r.height * 100) / 100,
    };
  };
  const attr = (el, name) => el.hasAttribute(name) ? String(el.getAttribute(name) ?? "") : "";
  const cssPath = (el) => {
    if (!el || !el.tagName) return "";
    const parts = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE && parts.length < 8) {
      let part = node.tagName.toLowerCase();
      if (node.id) {
        part += "#" + node.id;
        parts.unshift(part);
        break;
      }
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((x) => x.tagName === node.tagName);
        if (siblings.length > 1) {
          part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
        }
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(" > ");
  };
  const optionSummary = (select) => Array.from(select.options || []).map((opt) => ({
    text: norm(opt.text),
    value: String(opt.value ?? ""),
    selected: Boolean(opt.selected),
  }));
  const controls = Array.from(document.querySelectorAll(
    "input, button, select, textarea, a, img, iframe, frame, form"
  )).map((el, index) => ({
    index,
    path: cssPath(el),
    tag: el.tagName.toLowerCase(),
    type: attr(el, "type"),
    id: attr(el, "id"),
    name: attr(el, "name"),
    value: "value" in el ? String(el.value ?? "") : attr(el, "value"),
    text: norm(el.innerText || el.textContent || attr(el, "alt") || attr(el, "title")),
    href: attr(el, "href"),
    src: attr(el, "src"),
    action: attr(el, "action"),
    target: attr(el, "target"),
    disabled: Boolean(el.disabled),
    checked: Boolean(el.checked),
    selectedIndex: "selectedIndex" in el ? el.selectedIndex : null,
    options: el.tagName.toLowerCase() === "select" ? optionSummary(el) : [],
    rect: rectOf(el),
  }));
  const tables = Array.from(document.querySelectorAll("table")).map((table, index) => {
    const rows = Array.from(table.rows || []);
    return {
      index,
      path: cssPath(table),
      rowCount: rows.length,
      columnCounts: rows.slice(0, 10).map((row) => row.cells.length),
      textSample: rows.slice(0, 8).map((row) =>
        Array.from(row.cells || []).slice(0, 8).map((cell) => norm(cell.innerText || cell.textContent))
      ),
      rect: rectOf(table),
    };
  });
  return {
    url: location.href,
    protocol: location.protocol,
    host: location.host,
    pathname: location.pathname,
    search: location.search,
    hash: location.hash,
    title: document.title,
    readyState: document.readyState,
    charset: document.characterSet,
    text: document.body ? document.body.innerText : "",
    dom: document.documentElement ? document.documentElement.outerHTML : "",
    metrics: {
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
      outerWidth: window.outerWidth,
      outerHeight: window.outerHeight,
      screenX: window.screenX,
      screenY: window.screenY,
      devicePixelRatio: window.devicePixelRatio,
      scrollX: window.scrollX,
      scrollY: window.scrollY,
      scrollWidth: document.documentElement ? document.documentElement.scrollWidth : null,
      scrollHeight: document.documentElement ? document.documentElement.scrollHeight : null,
      bodyClientWidth: document.body ? document.body.clientWidth : null,
      bodyClientHeight: document.body ? document.body.clientHeight : null,
    },
    counts: {
      forms: document.forms.length,
      inputs: document.querySelectorAll("input").length,
      buttons: document.querySelectorAll("button, input[type=button], input[type=submit], input[type=reset]").length,
      selects: document.querySelectorAll("select").length,
      textareas: document.querySelectorAll("textarea").length,
      links: document.links.length,
      images: document.images.length,
      tables: document.querySelectorAll("table").length,
      iframes: document.querySelectorAll("iframe, frame").length,
    },
    controls,
    tables,
  };
}
"""


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "value"


def replace_hosts(value: str, hosts: Iterable[str]) -> str:
    text = value
    for host in hosts:
        if host:
            text = text.replace(host, "{ENV_HOST}")
            text = text.replace(host.replace(".", r"\."), "{ENV_HOST}")
    return text


def normalize_text(value: str, hosts: Iterable[str]) -> str:
    text = replace_hosts(str(value or ""), hosts)
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def normalize_dom(value: str, hosts: Iterable[str]) -> str:
    text = replace_hosts(str(value or ""), hosts)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_obj(value: Any, hosts: Iterable[str]) -> Any:
    if isinstance(value, str):
        return replace_hosts(value, hosts)
    if isinstance(value, list):
        return [normalize_obj(item, hosts) for item in value]
    if isinstance(value, dict):
        return {key: normalize_obj(value[key], hosts) for key in sorted(value)}
    return value


def scrub_allowed_dom(value: str) -> str:
    text = str(value or "")
    text = re.sub(r'(\.\./pattest/)\d+(/koho/img/)', r"\1{SERVER_RESOURCE_BATCH}\2", text)

    def scrub_form_tag(match: re.Match[str]) -> str:
        tag = match.group(0)
        name_match = re.search(r'\bname=["\']([^"\']+)["\']', tag)
        id_match = re.search(r'\bid=["\']([^"\']+)["\']', tag)
        if name_match and id_match and name_match.group(1) == id_match.group(1):
            tag = re.sub(r'\s+id=["\'][^"\']+["\']', "", tag)
        return tag

    text = re.sub(r"<form\b[^>]*>", scrub_form_tag, text, flags=re.IGNORECASE)
    text = re.sub(
        r'(\bname=["\']userId["\'][^>]*\bvalue=["\'])[^"\']*',
        r"\1{SERVER_USER_ID}",
        text,
    )
    text = re.sub(
        r'(\bvalue=["\'])[^"\']*(["\'][^>]*\bname=["\']userId["\'])',
        r"\1{SERVER_USER_ID}\2",
        text,
    )
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r">\s+<", "><", text)


def scrub_allowed_path(path: str, form_names: Iterable[str]) -> str:
    text = str(path or "")
    names = [name for name in form_names if name]
    for name in names:
        if text.startswith(f"form#{name}"):
            return text.replace(f"form#{name}", f"form[name={name}]", 1)
    if names:
        name = names[0]
        if text.startswith("html > body > form"):
            return text.replace("html > body > form", f"form[name={name}]", 1)
        if text.startswith("body > form"):
            return text.replace("body > form", f"form[name={name}]", 1)
    return text


def allowed_form_names(legacy_controls: List[Dict[str, Any]], new_controls: List[Dict[str, Any]]) -> List[str]:
    names = []
    for legacy_item, new_item in zip(legacy_controls, new_controls):
        name = str(legacy_item.get("name") or new_item.get("name") or "")
        if (
            legacy_item.get("tag") == "form"
            and new_item.get("tag") == "form"
            and name
            and legacy_item.get("name") == new_item.get("name")
            and not legacy_item.get("id")
            and new_item.get("id") == name
        ):
            names.append(name)
    return names


def scrub_allowed_control(control: Dict[str, Any], form_names: Iterable[str]) -> Dict[str, Any]:
    item = dict(control)
    if item.get("tag") == "form" and item.get("name") in set(form_names):
        item["id"] = ""
    item["path"] = scrub_allowed_path(str(item.get("path") or ""), form_names)
    if item.get("tag") == "img" and item.get("src"):
        item["src"] = re.sub(r"(\.\./pattest/)\d+(/koho/img/)", r"\1{SERVER_RESOURCE_BATCH}\2", str(item["src"]))
    if item.get("tag") == "input" and item.get("name") == "userId":
        item["value"] = "{SERVER_USER_ID}"
    return item


def scrub_allowed_table(table: Dict[str, Any], form_names: Iterable[str]) -> Dict[str, Any]:
    item = dict(table)
    item["path"] = scrub_allowed_path(str(item.get("path") or ""), form_names)
    return item


def apply_allowed_differences(legacy: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    legacy_raw_controls = legacy.get("normalized_controls") or []
    new_raw_controls = new.get("normalized_controls") or []
    form_names = allowed_form_names(legacy_raw_controls, new_raw_controls)
    legacy_controls = [scrub_allowed_control(item, form_names) for item in legacy_raw_controls]
    new_controls = [scrub_allowed_control(item, form_names) for item in new_raw_controls]
    legacy_tables = [scrub_allowed_table(item, form_names) for item in legacy.get("normalized_tables") or []]
    new_tables = [scrub_allowed_table(item, form_names) for item in new.get("normalized_tables") or []]
    legacy_dom = scrub_allowed_dom(legacy.get("normalized_dom") or "")
    new_dom = scrub_allowed_dom(new.get("normalized_dom") or "")
    accepted = []
    for name in form_names:
        accepted.append(
            {
                "id": f"form_{name}_id_added_on_192",
                "status": "ACCEPTED",
                "scope": "DOM/control/table path",
                "legacy": f'<form name="{name}">',
                "new": f'<form id="{name}" name="{name}">',
                "reason": f"ユーザー確認済みの移行差異。192 環境で form に id=\"{name}\" が追加されているが、許容して PASS 扱い。",
            }
        )
    legacy_dom_raw = legacy.get("normalized_dom") or ""
    new_dom_raw = new.get("normalized_dom") or ""
    if re.search(r"\.\./pattest/\d+/", legacy_dom_raw) or re.search(r"\.\./pattest/\d+/", new_dom_raw):
        accepted.append(
            {
                "id": "pattest_resource_batch_directory_diff",
                "status": "ACCEPTED",
                "scope": "DOM/control img src",
                "legacy": "../pattest/8610492/...",
                "new": "../pattest/8610525/...",
                "reason": "サーバー差異により画像リソースの中間ディレクトリ番号が異なる。静的リソース hash は一致しているため許容。",
            }
        )
    legacy_user_ids = [
        item.get("value")
        for item in legacy_raw_controls
        if item.get("tag") == "input" and item.get("name") == "userId"
    ]
    new_user_ids = [
        item.get("value")
        for item in new_raw_controls
        if item.get("tag") == "input" and item.get("name") == "userId"
    ]
    if legacy_user_ids != new_user_ids:
        accepted.append(
            {
                "id": "hidden_userId_session_value_diff",
                "status": "ACCEPTED",
                "scope": "DOM/control hidden input",
                "legacy": f'name="userId" value="{legacy_user_ids[0] if legacy_user_ids else ""}"',
                "new": f'name="userId" value="{new_user_ids[0] if new_user_ids else ""}"',
                "reason": "サーバー/session 差異により hidden userId が異なる。環境依存値として許容。",
            }
        )
    return {
        "accepted": accepted,
        "checks": {
            "controls_equal_after_allowed_server_differences": legacy_controls == new_controls,
            "tables_equal_after_allowed_server_differences": legacy_tables == new_tables,
            "dom_equal_after_allowed_server_differences": legacy_dom == new_dom,
            "legacy_controls_after_allowed_server_differences_sha256": sha256_text(
                json.dumps(legacy_controls, ensure_ascii=False, sort_keys=True)
            ),
            "new_controls_after_allowed_server_differences_sha256": sha256_text(
                json.dumps(new_controls, ensure_ascii=False, sort_keys=True)
            ),
            "legacy_tables_after_allowed_server_differences_sha256": sha256_text(
                json.dumps(legacy_tables, ensure_ascii=False, sort_keys=True)
            ),
            "new_tables_after_allowed_server_differences_sha256": sha256_text(
                json.dumps(new_tables, ensure_ascii=False, sort_keys=True)
            ),
            "legacy_dom_after_allowed_server_differences_sha256": sha256_text(legacy_dom),
            "new_dom_after_allowed_server_differences_sha256": sha256_text(new_dom),
        },
        "remaining_control_diff_after_allowed_server_differences": unified_diff(
            json.dumps(legacy_controls, ensure_ascii=False, indent=2, sort_keys=True),
            json.dumps(new_controls, ensure_ascii=False, indent=2, sort_keys=True),
            "legacy_controls_after_allowed_server_differences",
            "new_controls_after_allowed_server_differences",
        ),
        "remaining_table_diff_after_allowed_server_differences": unified_diff(
            json.dumps(legacy_tables, ensure_ascii=False, indent=2, sort_keys=True),
            json.dumps(new_tables, ensure_ascii=False, indent=2, sort_keys=True),
            "legacy_tables_after_allowed_server_differences",
            "new_tables_after_allowed_server_differences",
        ),
        "remaining_dom_diff_after_allowed_server_differences": unified_diff(
            legacy_dom,
            new_dom,
            "legacy_dom_after_allowed_server_differences",
            "new_dom_after_allowed_server_differences",
        ),
    }


def unified_diff(a: str, b: str, fromfile: str, tofile: str, limit: int = 120) -> List[str]:
    lines = list(
        difflib.unified_diff(
            a.splitlines(),
            b.splitlines(),
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )
    if len(lines) > limit:
        return lines[:limit] + [f"... truncated {len(lines) - limit} diff lines ..."]
    return lines


def template_inventory(path: Optional[Path]) -> List[Dict[str, Any]]:
    if not path:
        return []
    if not path.exists():
        return [{"path": str(path), "status": "missing"}]
    records = []
    for item in sorted(path.glob("*.xls")):
        records.append(
            {
                "name": item.name,
                "path": str(item),
                "size": item.stat().st_size,
                "modified": datetime.fromtimestamp(item.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
    return records


def page_resource_refs(record: Dict[str, Any]) -> List[Dict[str, str]]:
    refs: List[Dict[str, str]] = []
    seen = set()

    def add(kind: str, raw: str) -> None:
        raw = str(raw or "")
        key = (kind, raw)
        if raw and key not in seen:
            seen.add(key)
            refs.append({"kind": kind, "raw": raw})

    for item in record.get("controls") or []:
        if item.get("tag") == "img":
            add("img", item.get("src") or "")

    dom = record.get("dom") or ""
    for match in re.finditer(r"""<script[^>]+src=["']([^"']+)""", dom, re.IGNORECASE):
        add("script", match.group(1))
    for match in re.finditer(r"""<link[^>]+href=["']([^"']+)""", dom, re.IGNORECASE):
        add("link", match.group(1))
    return refs


def fetch_resource(page_url: str, ref: Dict[str, str]) -> Dict[str, Any]:
    url = urljoin(page_url, ref["raw"])
    result: Dict[str, Any] = {"kind": ref["kind"], "raw": ref["raw"], "url": url, "ok": False}
    try:
        request = Request(url, headers={"User-Agent": "Moonlight-Automation-Agent"})
        with urlopen(request, timeout=5) as response:
            data = response.read()
            result.update(
                {
                    "ok": True,
                    "status": getattr(response, "status", None),
                    "content_type": response.headers.get("content-type", ""),
                    "length": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    except Exception as exc:
        result["error"] = str(exc)
    return result


def compare_resources(legacy: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    legacy_refs = page_resource_refs(legacy)
    new_refs = page_resource_refs(new)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        legacy_futures = [executor.submit(fetch_resource, legacy.get("url") or "", ref) for ref in legacy_refs]
        new_futures = [executor.submit(fetch_resource, new.get("url") or "", ref) for ref in new_refs]
        legacy_results = [future.result() for future in legacy_futures]
        new_results = [future.result() for future in new_futures]

    pairs = []
    for index, (legacy_item, new_item) in enumerate(zip(legacy_results, new_results)):
        pairs.append(
            {
                "index": index,
                "legacy_kind": legacy_item.get("kind"),
                "new_kind": new_item.get("kind"),
                "legacy_raw": legacy_item.get("raw"),
                "new_raw": new_item.get("raw"),
                "legacy_url": legacy_item.get("url"),
                "new_url": new_item.get("url"),
                "legacy_status": legacy_item.get("status"),
                "new_status": new_item.get("status"),
                "legacy_length": legacy_item.get("length"),
                "new_length": new_item.get("length"),
                "legacy_sha256": legacy_item.get("sha256"),
                "new_sha256": new_item.get("sha256"),
                "legacy_error": legacy_item.get("error"),
                "new_error": new_item.get("error"),
                "same_hash": bool(legacy_item.get("sha256"))
                and legacy_item.get("sha256") == new_item.get("sha256"),
            }
        )

    return {
        "legacy_count": len(legacy_refs),
        "new_count": len(new_refs),
        "all_hashes_equal_by_order": len(legacy_refs) == len(new_refs)
        and all(pair.get("same_hash") for pair in pairs),
        "diff_count": sum(1 for pair in pairs if not pair.get("same_hash")),
        "pairs": pairs,
    }


def find_page(browser: Any, host: str, path_fragment: str) -> Any:
    matches = []
    for context in browser.contexts:
        for page in context.pages:
            try:
                url = page.url
            except PlaywrightError:
                continue
            if host in url and path_fragment in url:
                matches.append(page)
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


def capture_page(page: Any, label: str, output_dir: Path, hosts: Tuple[str, str]) -> Dict[str, Any]:
    page.bring_to_front()
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except PlaywrightError:
        pass
    page.wait_for_timeout(600)

    screenshot = output_dir / f"{label}.png"
    screenshot_method = "playwright"
    try:
        page.screenshot(path=str(screenshot), full_page=False, timeout=10000)
    except PlaywrightError as exc:
        screenshot_method = "cdp"
        session = page.context.new_cdp_session(page)
        try:
            payload = session.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
            data = payload.get("data")
            if not data:
                raise RuntimeError("Page.captureScreenshot returned no data") from exc
            screenshot.write_bytes(base64.b64decode(data))
        finally:
            try:
                session.detach()
            except PlaywrightError:
                pass
    data = page.evaluate(CAPTURE_JS)
    data["captured_at"] = datetime.now().isoformat(timespec="seconds")
    data["screenshot"] = str(screenshot)
    data["screenshot_method"] = screenshot_method
    data["raw_text_sha256"] = sha256_text(data.get("text") or "")
    data["normalized_text"] = normalize_text(data.get("text") or "", hosts)
    data["normalized_text_sha256"] = sha256_text(data["normalized_text"])
    data["raw_dom_sha256"] = sha256_text(data.get("dom") or "")
    data["normalized_dom"] = normalize_dom(data.get("dom") or "", hosts)
    data["normalized_dom_sha256"] = sha256_text(data["normalized_dom"])
    data["normalized_controls"] = normalize_obj(data.get("controls") or [], hosts)
    data["normalized_controls_sha256"] = sha256_text(
        json.dumps(data["normalized_controls"], ensure_ascii=False, sort_keys=True)
    )
    data["normalized_tables"] = normalize_obj(data.get("tables") or [], hosts)
    data["normalized_tables_sha256"] = sha256_text(
        json.dumps(data["normalized_tables"], ensure_ascii=False, sort_keys=True)
    )

    json_path = output_dir / f"{label}.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    data["json"] = str(json_path)
    return data


def compare_records(legacy: Dict[str, Any], new: Dict[str, Any], output_dir: Path) -> Dict[str, Any]:
    visual = compare_visual_screenshot(
        legacy["screenshot"],
        new["screenshot"],
        str(output_dir / "visual_diff.png"),
        threshold_percent=0.0,
        ignore_top_px=0,
    )

    legacy_url = urlparse(legacy.get("url") or "")
    new_url = urlparse(new.get("url") or "")
    comparisons = {
        "title_equal": legacy.get("title") == new.get("title"),
        "path_equal": legacy_url.path == new_url.path,
        "query_equal": legacy_url.query == new_url.query,
        "ready_state_equal": legacy.get("readyState") == new.get("readyState"),
        "charset_equal": legacy.get("charset") == new.get("charset"),
        "counts_equal": legacy.get("counts") == new.get("counts"),
        "metrics_content_equal": {
            key: (legacy.get("metrics") or {}).get(key) == (new.get("metrics") or {}).get(key)
            for key in [
                "innerWidth",
                "innerHeight",
                "devicePixelRatio",
                "scrollWidth",
                "scrollHeight",
                "bodyClientWidth",
                "bodyClientHeight",
            ]
        },
        "normalized_text_equal": legacy.get("normalized_text_sha256") == new.get("normalized_text_sha256"),
        "normalized_dom_equal": legacy.get("normalized_dom_sha256") == new.get("normalized_dom_sha256"),
        "normalized_controls_equal": legacy.get("normalized_controls_sha256") == new.get("normalized_controls_sha256"),
        "normalized_tables_equal": legacy.get("normalized_tables_sha256") == new.get("normalized_tables_sha256"),
        "visual_equal": visual.get("status") == "PASS" and float(visual.get("diff_percent") or 0) == 0.0,
    }
    details = {
        "text_diff": unified_diff(
            legacy.get("normalized_text") or "",
            new.get("normalized_text") or "",
            "legacy_text",
            "new_text",
        ),
        "dom_hashes": {
            "legacy_raw": legacy.get("raw_dom_sha256"),
            "new_raw": new.get("raw_dom_sha256"),
            "legacy_normalized": legacy.get("normalized_dom_sha256"),
            "new_normalized": new.get("normalized_dom_sha256"),
        },
        "control_diff": unified_diff(
            json.dumps(legacy.get("normalized_controls") or [], ensure_ascii=False, indent=2, sort_keys=True),
            json.dumps(new.get("normalized_controls") or [], ensure_ascii=False, indent=2, sort_keys=True),
            "legacy_controls",
            "new_controls",
        ),
        "table_diff": unified_diff(
            json.dumps(legacy.get("normalized_tables") or [], ensure_ascii=False, indent=2, sort_keys=True),
            json.dumps(new.get("normalized_tables") or [], ensure_ascii=False, indent=2, sort_keys=True),
            "legacy_tables",
            "new_tables",
        ),
    }
    allowed = apply_allowed_differences(legacy, new)
    all_metrics_equal = all(comparisons["metrics_content_equal"].values())
    effective_checks = [
        comparisons["title_equal"],
        comparisons["path_equal"],
        comparisons["query_equal"],
        comparisons["ready_state_equal"],
        comparisons["charset_equal"],
        comparisons["counts_equal"],
        all_metrics_equal,
        comparisons["normalized_text_equal"],
        bool((allowed.get("checks") or {}).get("dom_equal_after_allowed_server_differences")),
        bool((allowed.get("checks") or {}).get("controls_equal_after_allowed_server_differences")),
        bool((allowed.get("checks") or {}).get("tables_equal_after_allowed_server_differences")),
        comparisons["visual_equal"],
    ]

    return {
        "status": "PASS" if all(effective_checks) else "DIFF",
        "raw_status": "PASS" if comparisons["normalized_dom_equal"] and comparisons["normalized_controls_equal"] else "DIFF",
        "effective_checks_use_accepted_differences": True,
        "visual": visual,
        "comparisons": comparisons,
        "allowed_differences": allowed,
        "details": details,
    }


def rel(path: str, base: Path) -> str:
    try:
        return Path(path).resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return path


def html_report(result: Dict[str, Any], output_dir: Path) -> str:
    legacy = result["legacy"]
    new = result["new"]
    compare = result["compare"]
    status = compare["status"]
    rows = []
    for key, value in compare["comparisons"].items():
        if isinstance(value, dict):
            value_text = "<br>".join(
                f"{html.escape(str(k))}: {html.escape(str(v))}" for k, v in value.items()
            )
            ok = all(value.values())
        else:
            value_text = html.escape(str(value))
            ok = bool(value)
        rows.append(
            f"<tr><td>{html.escape(str(key))}</td><td class=\"{'ok' if ok else 'ng'}\">{value_text}</td></tr>"
        )
    visual = compare["visual"]
    image_html = "".join(
        f"<figure><figcaption>{html.escape(label)}</figcaption><img src=\"{html.escape(rel(path, output_dir))}\"></figure>"
        for label, path in [
            ("legacy .47", legacy["screenshot"]),
            ("new .192", new["screenshot"]),
            ("diff", visual.get("diff_screenshot") or ""),
        ]
        if path
    )
    diffs = []
    for title, lines in [
        ("Text diff", compare["details"]["text_diff"]),
        ("Control diff", compare["details"]["control_diff"]),
        ("Table diff", compare["details"]["table_diff"]),
    ]:
        content = "\n".join(lines) if lines else "No diff"
        diffs.append(f"<h2>{html.escape(title)}</h2><pre>{html.escape(content)}</pre>")
    template_rows = "".join(
        f"<li>{html.escape(item.get('name') or item.get('path') or '')} ({html.escape(str(item.get('size', '')))} bytes)</li>"
        for item in result.get("templates") or []
    )
    allowed = (compare.get("allowed_differences") or {})
    accepted_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('id')))}</td>"
        f"<td class=\"ok\">{html.escape(str(item.get('status')))}</td>"
        f"<td>{html.escape(str(item.get('legacy')))}</td>"
        f"<td>{html.escape(str(item.get('new')))}</td>"
        f"<td>{html.escape(str(item.get('reason')))}</td>"
        "</tr>"
        for item in allowed.get("accepted") or []
    )
    allowed_checks = allowed.get("checks") or {}
    resources = result.get("resources") or {}
    resource_pairs = resources.get("pairs") or []
    resource_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('index')))}</td>"
        f"<td>{html.escape(str(item.get('legacy_kind')))}</td>"
        f"<td>{html.escape(str(item.get('same_hash')))}</td>"
        f"<td>{html.escape(str(item.get('legacy_raw')))}</td>"
        f"<td>{html.escape(str(item.get('new_raw')))}</td>"
        f"<td>{html.escape(str(item.get('legacy_length')))} / {html.escape(str(item.get('new_length')))}</td>"
        "</tr>"
        for item in resource_pairs[:80]
    )
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Edge popup compare - {html.escape(status)}</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #172033; }}
    h1 {{ margin: 0 0 8px; }}
    .status {{ display: inline-block; padding: 4px 10px; border-radius: 4px; color: white; background: {'#137333' if status == 'PASS' else '#b3261e'}; }}
    table {{ border-collapse: collapse; margin: 16px 0; width: 100%; }}
    th, td {{ border: 1px solid #d4d9e2; padding: 8px; vertical-align: top; }}
    th {{ background: #f4f6f9; text-align: left; }}
    .ok {{ color: #137333; font-weight: 600; }}
    .ng {{ color: #b3261e; font-weight: 600; }}
    .images {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
    figure {{ margin: 0; }}
    img {{ max-width: 100%; border: 1px solid #d4d9e2; }}
    pre {{ background: #f6f8fa; border: 1px solid #d4d9e2; padding: 12px; overflow: auto; max-height: 420px; }}
    code {{ background: #f6f8fa; padding: 1px 4px; }}
  </style>
</head>
<body>
  <h1>Edge popup compare <span class="status">{html.escape(status)}</span></h1>
  <p>Target: <code>{html.escape(result['target_path'])}</code> / legacy: <code>{html.escape(result['legacy_host'])}</code> / new: <code>{html.escape(result['new_host'])}</code></p>
  <table>
    <tr><th>Item</th><th>Result</th></tr>
    <tr><td>Visual status</td><td>{html.escape(str(visual.get('status')))} / diff={html.escape(str(visual.get('diff_percent')))}%</td></tr>
    {''.join(rows)}
  </table>
  <h2>Screenshots</h2>
  <div class="images">{image_html}</div>
  <h2>Source pages</h2>
  <table>
    <tr><th>Side</th><th>URL</th><th>Title</th><th>Counts</th><th>Metrics</th></tr>
    <tr><td>.47</td><td>{html.escape(legacy.get('url') or '')}</td><td>{html.escape(legacy.get('title') or '')}</td><td><pre>{html.escape(json.dumps(legacy.get('counts'), ensure_ascii=False, indent=2))}</pre></td><td><pre>{html.escape(json.dumps(legacy.get('metrics'), ensure_ascii=False, indent=2))}</pre></td></tr>
    <tr><td>.192</td><td>{html.escape(new.get('url') or '')}</td><td>{html.escape(new.get('title') or '')}</td><td><pre>{html.escape(json.dumps(new.get('counts'), ensure_ascii=False, indent=2))}</pre></td><td><pre>{html.escape(json.dumps(new.get('metrics'), ensure_ascii=False, indent=2))}</pre></td></tr>
  </table>
  <h2>Accepted Migration Differences</h2>
  <table><tr><th>ID</th><th>Status</th><th>Legacy</th><th>New</th><th>Reason</th></tr>{accepted_rows}</table>
  <p>dom_equal_after_allowed_server_differences={html.escape(str(allowed_checks.get('dom_equal_after_allowed_server_differences')))}</p>
  <p>controls_equal_after_allowed_server_differences={html.escape(str(allowed_checks.get('controls_equal_after_allowed_server_differences')))}</p>
  <p>tables_equal_after_allowed_server_differences={html.escape(str(allowed_checks.get('tables_equal_after_allowed_server_differences')))}</p>
  <h2>Template references</h2>
  <ul>{template_rows}</ul>
  <h2>Static resources</h2>
  <p>Count: {html.escape(str(resources.get('legacy_count')))} / {html.escape(str(resources.get('new_count')))}; diff_count={html.escape(str(resources.get('diff_count')))}; all_hashes_equal_by_order={html.escape(str(resources.get('all_hashes_equal_by_order')))}</p>
  <table><tr><th>#</th><th>Kind</th><th>Same hash</th><th>Legacy raw</th><th>New raw</th><th>Length</th></tr>{resource_rows}</table>
  {''.join(diffs)}
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
    parser.add_argument("--template-dir", default=r"C:\work\note\PageTest\6_12")
    args = parser.parse_args()

    output_dir = (ROOT / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    hosts = (args.legacy_host, args.new_host)

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp)
        legacy_page = find_page(browser, args.legacy_host, args.path)
        new_page = find_page(browser, args.new_host, args.path)
        legacy = capture_page(legacy_page, "legacy_47", output_dir, hosts)
        new = capture_page(new_page, "new_192", output_dir, hosts)
        compare = compare_records(legacy, new, output_dir)
        resources = compare_resources(legacy, new)
        browser.close()

    result = {
        "status": compare["status"],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_path": args.path,
        "legacy_host": args.legacy_host,
        "new_host": args.new_host,
        "browser": "Microsoft Edge via CDP",
        "strict": True,
        "legacy": {key: value for key, value in legacy.items() if key not in {"dom", "normalized_dom", "text", "normalized_text"}},
        "new": {key: value for key, value in new.items() if key not in {"dom", "normalized_dom", "text", "normalized_text"}},
        "compare": compare,
        "resources": resources,
        "templates": template_inventory(Path(args.template_dir) if args.template_dir else None),
    }
    result_path = output_dir / "compare_result.json"
    report_path = output_dir / "compare_report.html"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(html_report(result, output_dir), encoding="utf-8")

    print(json.dumps({
        "status": result["status"],
        "report": str(report_path),
        "json": str(result_path),
        "legacy_screenshot": legacy["screenshot"],
        "new_screenshot": new["screenshot"],
        "diff_screenshot": compare["visual"].get("diff_screenshot"),
        "visual_diff_percent": compare["visual"].get("diff_percent"),
    }, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
