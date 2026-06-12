import argparse
import json
import re
import sys
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page, sync_playwright

from src.browser_print import install_print_suppression
from src.browser_window import set_main_window_bounds
from src.config_parser import Config
from src.route_runtime_verifier import record_manual_route, verify_candidate_route, write_usable_route_map


CHROMIUM_ARGS = [
    "--start-maximized",
    "--force-device-scale-factor=1",
    "--high-dpi-support=1",
    "--disable-popup-blocking",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-site-isolation-trials",
    "--disable-web-security",
    "--allow-running-insecure-content",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
]

FIREFOX_ARGS = []


def _download_launch_kwargs() -> Dict[str, str]:
    download_dir = Path(Config.DOWNLOAD_DIR).expanduser()
    download_dir.mkdir(parents=True, exist_ok=True)
    return {"downloads_path": str(download_dir)}


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _make_process_dpi_aware() -> None:
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _load_candidates(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _route_matches(route: Dict[str, Any], target: Optional[str]) -> bool:
    if not target:
        return True
    target_lower = target.lower()
    return target_lower in str(route.get("target_page") or "").lower() or target_lower in str(route.get("target_page_name") or "").lower()


def _launch_browser(playwright, browser_name: str):
    if browser_name == "edge":
        return playwright.chromium.launch(
            channel="msedge",
            headless=False,
            args=CHROMIUM_ARGS,
            **_download_launch_kwargs(),
        )
    if browser_name == "firefox":
        return playwright.firefox.launch(
            headless=False,
            args=FIREFOX_ARGS,
            **_download_launch_kwargs(),
        )
    if browser_name == "chrome_port":
        chrome_kwargs = {
            **Config.chrome_launch_kwargs(),
            **_download_launch_kwargs(),
            "headless": False,
            "args": CHROMIUM_ARGS,
        }
        return playwright.chromium.launch(**chrome_kwargs)
    raise ValueError(f"Unsupported browser backend: {browser_name}")


def _page_is_closed(page: Page) -> bool:
    try:
        return page.is_closed()
    except Exception:
        return True


def _safe_accept(dialog) -> None:
    try:
        dialog.accept()
    except Exception:
        pass


def _safe_file_stem(value: Any, default: str = "target") -> str:
    text = str(value or default).replace("\\", "/").rsplit("/", 1)[-1]
    text = re.sub(r"\.[A-Za-z0-9]+$", "", text)
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")
    return text[:120] or default


def _target_page_name(value: Any) -> str:
    text = str(value or "").strip().replace("/", "\\")
    name = PureWindowsPath(text).name
    if name.lower().endswith(".do"):
        name = name[:-3] + ".jsp"
    return name.lower()


def _manual_route_candidate(target: str, *, reason: str) -> Dict[str, Any]:
    target_name = _target_page_name(target)
    route_id = f"manual_{_safe_file_stem(target_name or target, 'target')}"
    return {
        "route_id": route_id,
        "target_page": target,
        "target_page_name": target_name or target,
        "target_node": target,
        "nodes": [],
        "length": 0,
        "status": "manual_candidate",
        "manual_route": True,
        "reason": reason,
    }


def _persist_runtime_profile(result: Dict[str, Any], args: argparse.Namespace, login_entry_name: str) -> Optional[Path]:
    profile = result.pop("runtime_profile", None)
    if not isinstance(profile, dict):
        return None

    profile["side"] = args.side
    profile["login_entry"] = login_entry_name
    profile["route_map_path"] = str(args.output)
    profile["candidates_path"] = str(args.candidates)

    target_stem = _safe_file_stem(
        profile.get("target_page_name")
        or profile.get("target_page")
        or result.get("target_page_name")
        or result.get("target_page")
        or args.target
    )
    route_stem = _safe_file_stem(result.get("route_id") or profile.get("route_id"), "route")
    output_path = args.runtime_profile_dir / f"{args.side}_{target_stem}_{route_stem}.json"
    args.runtime_profile_dir.mkdir(parents=True, exist_ok=True)
    profile["runtime_profile_path"] = str(output_path)
    output_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    result["runtime_profile_path"] = str(output_path)
    result["runtime_profile_summary"] = {
        "url": profile.get("url"),
        "counts": profile.get("counts") or {},
        "control_count": len(profile.get("controls") or []),
    }
    return output_path


def _looks_like_login_entry(page: Page) -> bool:
    selectors = (
        "input[type='password']",
        "input[onclick*='PatlicsTopMain']",
        "input[value*='ログイン']",
        "button:has-text('ログイン')",
    )
    for selector in selectors:
        try:
            if page.locator(selector).first.is_visible(timeout=1000):
                return True
        except Exception:
            continue
    return False


def _wait_for_manual_entry_ready(page: Page) -> None:
    print()
    print("[ROUTE MAP] Automatic login is disabled for the entry page.")
    print("  Complete login, entry selection, or required input in the browser.")
    print("  Press Enter when the page is ready; enter q to abort route mapping.")
    raw = input("> ").strip().lower()
    if raw == "q":
        raise InterruptedError("User aborted route mapping")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass


def _new_context(browser):
    context = browser.new_context(
        accept_downloads=True,
        no_viewport=True,
        user_agent="Moonlight-Automation-Agent",
    )
    install_print_suppression(context)
    context.on("dialog", _safe_accept)
    return context


def _close_browser_safely(browser) -> None:
    """Detach dialog callbacks before contexts close to avoid late accept tasks."""
    try:
        contexts = list(browser.contexts)
    except Exception:
        contexts = []

    for context in contexts:
        try:
            context.remove_listener("dialog", _safe_accept)
        except Exception:
            pass

    for context in contexts:
        try:
            context.close()
        except Exception:
            pass

    try:
        browser.close()
    except Exception:
        pass


def _open_or_login(page: Page, entry_url: str, timeout: int, *, auto_login: bool) -> None:
    page.bring_to_front()
    try:
        page.keyboard.press("Control+0")
    except Exception:
        pass

    page.goto(entry_url, wait_until="load", timeout=max(timeout, 30000))

    try:
        page.keyboard.press("Control+0")
    except Exception:
        pass

    if not auto_login:
        if _looks_like_login_entry(page):
            _wait_for_manual_entry_ready(page)
        return

    try:
        page.fill("input[name='user']", Config.USERNAME, timeout=3000)
        page.fill("input[name='password']", Config.PASSWORD, timeout=3000)
        page.click("input[type='button']", timeout=5000)
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        # Some entries are already authenticated or use a different login form.
        pass


def _prepare_page(browser, page: Optional[Page], entry_url: str, timeout: int, *, auto_login: bool) -> Page:
    if page is None or _page_is_closed(page):
        context = _new_context(browser)
        page = context.new_page()
    else:
        install_print_suppression(page.context)
        for extra_page in list(page.context.pages):
            if extra_page is page:
                continue
            try:
                extra_page.close()
            except Exception:
                pass
    set_main_window_bounds(page)
    _open_or_login(page, entry_url, timeout, auto_login=auto_login)
    return page


def _select_routes(catalog: Dict[str, Any], *, target: Optional[str], limit: Optional[int], start_index: int) -> List[Dict[str, Any]]:
    routes = [route for route in catalog.get("routes", []) if _route_matches(route, target)]
    if start_index > 1:
        routes = routes[start_index - 1 :]
    if limit:
        routes = routes[:limit]
    return routes


def run_route_map(args: argparse.Namespace) -> Path:
    _make_process_dpi_aware()

    candidates_missing = not args.candidates.exists()
    if not candidates_missing:
        catalog = _load_candidates(args.candidates)
    else:
        if not (args.manual_route or (args.manual_data and args.target)):
            raise FileNotFoundError(f"candidate file not found: {args.candidates}")
        catalog = {
            "schema": "moonlight.route_candidates.v1",
            "routes": [],
            "warnings": [{"target": args.target, "warnings": [f"candidate file not found: {args.candidates}"]}],
        }
    routes = _select_routes(catalog, target=args.target, limit=args.limit, start_index=args.start_index)
    selected_route_count = len(routes)
    manual_route_reason = ""
    if args.manual_route:
        manual_route_reason = "User requested --manual-route"
        if not args.target:
            raise ValueError("--manual-route requires --target")
        routes = [_manual_route_candidate(args.target, reason=manual_route_reason)]
    elif not routes and args.manual_data and args.target:
        manual_route_reason = "No candidate routes were found; starting full manual recording"
        routes = [_manual_route_candidate(args.target, reason=manual_route_reason)]
    login_entry = Config.select_login_entry(args.login_entry, interactive=True)
    entry_url = login_entry[f"{args.side}_url"]

    results: List[Dict[str, Any]] = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.capture_dir.mkdir(parents=True, exist_ok=True)

    print(f"[ROUTE MAP] side={args.side} entry={login_entry['name']} URL={entry_url}")
    print(f"[ROUTE MAP] candidate_count={selected_route_count} output={args.output}")
    if manual_route_reason:
        print(f"[ROUTE MAP] {manual_route_reason}")
    if args.upload_file:
        print(f"[ROUTE MAP] upload_file={args.upload_file}")

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright, args.browser)
        page: Optional[Page] = None
        try:
            for index, route in enumerate(routes, start=args.start_index):
                route_id = route.get("route_id") or f"route_{index}"
                print()
                print(f"[ROUTE MAP] {index}/{args.start_index + len(routes) - 1} start {route_id} -> {route.get('target_page')}")

                try:
                    page = _prepare_page(browser, page, entry_url, args.timeout, auto_login=args.auto_login)
                    route_capture_dir = args.capture_dir / f"{index:04d}_{route_id}"
                    if route.get("manual_route"):
                        result = record_manual_route(
                            page,
                            route,
                            capture_dir=route_capture_dir,
                            browser_name=args.browser,
                            timeout=args.timeout,
                            upload_file=str(args.upload_file) if args.upload_file else None,
                        )
                    else:
                        result = verify_candidate_route(
                            page,
                            route,
                            capture_dir=route_capture_dir,
                            browser_name=args.browser,
                            timeout=args.timeout,
                            manual_data=args.manual_data,
                            upload_file=str(args.upload_file) if args.upload_file else None,
                        )
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    result = {
                        "route_id": route_id,
                        "target_page": route.get("target_page"),
                        "target_page_name": route.get("target_page_name"),
                        "source_route": route,
                        "status": "ERROR",
                        "reason": str(exc),
                    }

                result["run_side"] = args.side
                result["login_entry"] = login_entry["name"]
                try:
                    profile_path = _persist_runtime_profile(result, args, login_entry["name"])
                    if profile_path:
                        print(f"[ROUTE MAP] runtime_profile={profile_path}")
                except Exception as exc:
                    result["runtime_profile_error"] = str(exc)
                results.append(result)
                write_usable_route_map(results, args.output)

                print(
                    f"[ROUTE MAP] end {route_id}: "
                    f"{result.get('status')} "
                    f"manual_steps={result.get('manual_steps', 0)} "
                    f"reason={result.get('reason') or '-'}"
                )
        finally:
            _close_browser_safely(browser)

    write_usable_route_map(results, args.output)
    return args.output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate candidate routes from one login entry and generate a usable route map.")
    parser.add_argument("--candidates", type=Path, default=Path("generated/valid/route_candidates.json"))
    parser.add_argument("--output", type=Path, default=Path("generated/valid/usable_route_map.json"))
    parser.add_argument("--capture-dir", type=Path, default=Path("output/route_map"))
    parser.add_argument("--runtime-profile-dir", type=Path, default=Path("generated/valid/runtime_profile"))
    parser.add_argument("--side", choices=["legacy", "new"], default="legacy", help="Run one environment at a time.")
    parser.add_argument("--login-entry", default=None, help="Login entry name or index; prompts when multiple entries exist.")
    parser.add_argument("--browser", choices=["edge", "firefox", "chrome_port"], default="chrome_port")
    parser.add_argument("--target", default=None, help="Optional target page to validate.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start-index", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=15000)
    parser.add_argument("--auto-login", action="store_true", help="Fill credentials and click login after opening the entry page; disabled by default.")
    parser.add_argument("--manual-data", action="store_true", help="Allow manual judgment or takeover for input, search, selection, and upload flows.")
    parser.add_argument("--manual-route", action="store_true", help="Record a replayable route manually from the entry page without static candidates.")
    parser.add_argument("--upload-file", type=Path, default=None, help="Optional local file used when manual recording or route replay reaches an upload control.")
    return parser


def main() -> None:
    _configure_stdio()
    output = run_route_map(build_parser().parse_args())
    print(f"[ROUTE MAP] wrote {output}")


if __name__ == "__main__":
    main()
