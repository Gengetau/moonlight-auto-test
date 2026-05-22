import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page, sync_playwright

from src.config_parser import Config
from src.route_runtime_verifier import verify_candidate_route, write_usable_route_map


CHROMIUM_ARGS = [
    "--start-maximized",
    "--window-size=1920,1080",
    "--window-position=0,0",
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

FIREFOX_ARGS = [
    "--width=1920",
    "--height=1080",
]


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
        return playwright.chromium.launch(channel="msedge", headless=False, args=CHROMIUM_ARGS)
    if browser_name == "firefox":
        return playwright.firefox.launch(headless=False, args=FIREFOX_ARGS)
    if browser_name == "chrome_port":
        if not Config.CHROME_PORTABLE_PATH:
            raise ValueError("CHROME_PORTABLE_PATH not set in .env")
        return playwright.chromium.launch(
            executable_path=Config.CHROME_PORTABLE_PATH,
            headless=False,
            args=CHROMIUM_ARGS,
        )
    raise ValueError(f"不支持的浏览器类型: {browser_name}")


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
    print("[路径建图] 首页自动登录已关闭")
    print("  请在浏览器中完成登录、入口选择或必要输入。")
    raw = input("  页面准备好后按 Enter 继续；输入 q 中止路径建图: ").strip().lower()
    if raw == "q":
        raise InterruptedError("用户中止路径建图")
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
    context.on("dialog", _safe_accept)
    return context


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
        # 部分入口已登录，或者登录表单结构不同；这里不阻断建图。
        pass


def _prepare_page(browser, page: Optional[Page], entry_url: str, timeout: int, *, auto_login: bool) -> Page:
    if page is None or _page_is_closed(page):
        context = _new_context(browser)
        page = context.new_page()
    else:
        for extra_page in list(page.context.pages):
            if extra_page is page:
                continue
            try:
                extra_page.close()
            except Exception:
                pass
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

    catalog = _load_candidates(args.candidates)
    routes = _select_routes(catalog, target=args.target, limit=args.limit, start_index=args.start_index)
    login_entry = Config.select_login_entry(args.login_entry, interactive=True)
    entry_url = login_entry[f"{args.side}_url"]

    results: List[Dict[str, Any]] = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.capture_dir.mkdir(parents=True, exist_ok=True)

    print(f"[路径建图] 环境={args.side} 入口={login_entry['name']} URL={entry_url}")
    print(f"[路径建图] 候选路径数={len(routes)} 输出={args.output}")

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright, args.browser)
        page: Optional[Page] = None
        try:
            for index, route in enumerate(routes, start=args.start_index):
                route_id = route.get("route_id") or f"route_{index}"
                print()
                print(f"[路径建图] {index}/{args.start_index + len(routes) - 1} 开始 {route_id} -> {route.get('target_page')}")

                try:
                    page = _prepare_page(browser, page, entry_url, args.timeout, auto_login=args.auto_login)
                    result = verify_candidate_route(
                        page,
                        route,
                        capture_dir=args.capture_dir / f"{index:04d}_{route_id}",
                        browser_name=args.browser,
                        timeout=args.timeout,
                        manual_data=args.manual_data,
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
                results.append(result)
                write_usable_route_map(results, args.output)

                print(
                    f"[路径建图] 结束 {route_id}: "
                    f"{result.get('status')} "
                    f"人工步骤数={result.get('manual_steps', 0)} "
                    f"原因={result.get('reason') or '-'}"
                )
        finally:
            browser.close()

    write_usable_route_map(results, args.output)
    return args.output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从一个登录入口验证候选路径，并生成可用路径 map。")
    parser.add_argument("--candidates", type=Path, default=Path("generated/valid/route_candidates.json"))
    parser.add_argument("--output", type=Path, default=Path("generated/valid/usable_route_map.json"))
    parser.add_argument("--capture-dir", type=Path, default=Path("output/route_map"))
    parser.add_argument("--side", choices=["legacy", "new"], default="legacy", help="一次只运行一个环境。")
    parser.add_argument("--login-entry", default=None, help="登录入口名称或序号；未指定且存在多个入口时会交互选择。")
    parser.add_argument("--browser", choices=["edge", "firefox", "chrome_port"], default="chrome_port")
    parser.add_argument("--target", default=None, help="可选：只验证指定目标页面。")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start-index", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=15000)
    parser.add_argument("--auto-login", action="store_true", help="打开入口后自动填写账号密码并点击登录；默认关闭。")
    parser.add_argument("--manual-data", action="store_true", help="允许在输入、检索、选择、上传等场景由人工判断或接管。")
    return parser


def main() -> None:
    output = run_route_map(build_parser().parse_args())
    print(f"[路径建图] 已写入 {output}")


if __name__ == "__main__":
    main()
