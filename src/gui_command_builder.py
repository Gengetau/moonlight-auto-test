import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_CHECKLIST_PATH = "generated/valid/migration_checklist.xlsx"
DEFAULT_ROUTE_MAP_PATH = "generated/valid/route"


def quote(value: Any) -> str:
    text = str(value)
    return '"' + text.replace('"', '\\"') + '"'


def browser_key(browser_name: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "_", str(browser_name or "browser").lower()).strip("_") or "browser"


def safe_page_key(page_id: str) -> str:
    text = Path(str(page_id or "selected_pages").replace("\\", "/")).name
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "selected_pages"


def split_case_types(value: Any) -> List[str]:
    return [item.strip() for item in re.split(r"[\r\n,;]+", str(value or "")) if item.strip()]


def html_report_path(browser_name: str, page_id: str) -> Path:
    return Path("output/gui") / browser_key(browser_name) / safe_page_key(page_id) / "gui_report.html"


def regression_output_dir(browser_name: str) -> Path:
    return Path("output/regression") / browser_key(browser_name)


def upload_profile_config_path(browser_name: str, page_id: str) -> Path:
    return Path("generated/gui/upload_profiles") / browser_key(browser_name) / f"{safe_page_key(page_id)}.json"


def build_regression_command(config: Dict[str, Any], *, pytest_cmd: str) -> str:
    browser = str(config.get("browser") or "chrome_port")
    page_id = str(config.get("target_page") or "").strip()
    if not page_id:
        raise ValueError("target_page is required")

    cmd = f"{pytest_cmd} tests/test_migration.py --run-migration --test-browser={browser}"
    cmd += f" --target-page={quote(page_id)}"
    cmd += f" --regression-output-dir={quote(config.get('regression_output_dir') or regression_output_dir(browser))}"

    login_entry = str(config.get("login_entry") or "").strip()
    if login_entry:
        cmd += f" --login-entry={quote(login_entry)}"

    checklist_path = str(config.get("checklist_path") or "").strip()
    if checklist_path:
        cmd += f" --checklist-path={quote(checklist_path)}"

    route_map_path = str(config.get("route_map_path") or "").strip()
    if config.get("force_route_map"):
        cmd += " --force-route-map"
        if route_map_path:
            cmd += f" --route-map-path={quote(route_map_path)}"

    if config.get("manual"):
        cmd += " --manual"
    if config.get("risk_only"):
        cmd += " --risk-only"
    if config.get("include_semi_auto"):
        cmd += " --include-semi-auto"
    if config.get("include_destructive"):
        cmd += " --include-destructive"
    if config.get("include_negative"):
        cmd += " --include-negative"
        negative_profile = str(config.get("negative_profile") or "").strip()
        if negative_profile:
            cmd += f" --negative-profile={quote(negative_profile)}"

    upload_file = str(config.get("upload_file") or "").strip()
    if upload_file:
        cmd += f" --upload-file={quote(upload_file)}"

    upload_profile_config = str(config.get("upload_profile_config") or "").strip()
    if upload_profile_config:
        cmd += f" --upload-profile-config={quote(upload_profile_config)}"

    html_path = config.get("html_path") or html_report_path(browser, page_id)
    cmd += f" --html={quote(html_path)}"
    return cmd


def _page_name(value: Any) -> str:
    return Path(str(value or "").replace("\\", "/")).name


def _add_option(options: Dict[str, Dict[str, Any]], page_id: Any, **meta: Any) -> None:
    page = _page_name(page_id)
    if not page:
        return
    key = page.lower()
    existing = options.setdefault(key, {"page_id": page, "sources": []})
    for field in ("entry_url", "action", "risk", "route_map_path"):
        if meta.get(field) and not existing.get(field):
            existing[field] = meta[field]
    source = meta.get("source")
    if source and source not in existing["sources"]:
        existing["sources"].append(source)


def load_page_options(
    *,
    mapping_path: Path = Path("generated/valid/page_mapping.json"),
    route_dir: Path = Path("generated/valid/route"),
    report_dir: Path = Path("output/regression"),
) -> List[Dict[str, Any]]:
    options: Dict[str, Dict[str, Any]] = {}

    if mapping_path.exists():
        try:
            payload = json.loads(mapping_path.read_text(encoding="utf-8"))
            for item in payload.get("page_mappings") or []:
                _add_option(
                    options,
                    item.get("page_id") or item.get("target_page"),
                    entry_url=item.get("entry_url") or item.get("resolved_entry_url"),
                    action=item.get("entry_action") or item.get("action") or item.get("action_path"),
                    risk=item.get("risk"),
                    source="mapping",
                )
        except Exception:
            pass

    if route_dir.exists():
        for path in route_dir.rglob("usable_route_map*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for route in list(payload.get("verified") or []) + list(payload.get("manual_verified") or []):
                source_route = route.get("source_route") or {}
                target = route.get("target_page") or route.get("target_page_name") or source_route.get("target_page") or source_route.get("target_page_name")
                _add_option(options, target, route_map_path=str(path), source="route")

    if report_dir.exists():
        for report in report_dir.rglob("regression_report.html"):
            parent = report.parent.name
            page = re.sub(r"^\d+_", "", parent)
            _add_option(options, page, source="recent")

    return sorted(options.values(), key=lambda item: item["page_id"].lower())


def page_option_labels(options: Iterable[Dict[str, Any]]) -> List[str]:
    labels = []
    for option in options:
        details = []
        if option.get("entry_url"):
            details.append(f"entry: {option['entry_url']}")
        if option.get("action"):
            details.append(f"action: {option['action']}")
        if option.get("risk"):
            details.append(f"risk: {option['risk']}")
        if option.get("route_map_path"):
            details.append("route: yes")
        suffix = f"    {' / '.join(details)}" if details else ""
        labels.append(f"{option['page_id']}{suffix}")
    return labels
