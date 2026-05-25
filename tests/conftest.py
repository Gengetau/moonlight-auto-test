import pytest
from playwright.sync_api import sync_playwright
from src.config_parser import Config


def _make_process_dpi_aware():
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


CHROMIUM_FULLSCREEN_ARGS = [
    "--start-maximized",
    "--window-size=1920,1080",
    "--window-position=0,0",
    "--force-device-scale-factor=1",
    "--high-dpi-support=1",
    "--disable-popup-blocking",
]

FIREFOX_FULLSCREEN_ARGS = [
    "--width=1920",
    "--height=1080",
]


def pytest_addoption(parser):
    parser.addoption(
        "--test-browser", 
        action="store", 
        default="chrome_port", 
        help="支持的参数: edge / firefox / chrome_port"
    )
    parser.addoption(
        "--mapping-path",
        action="store",
        default="generated/valid/page_mapping.json",
        help="page_mapping.py 生成的映射文件"
    )
    parser.addoption(
        "--risk-only",
        action="store_true",
        default=False,
        help="只执行 High/Medium 风险页面"
    )
    parser.addoption(
        "--regression-limit",
        action="store",
        type=int,
        default=None,
        help="限制回归页面数量，便于冒烟验证"
    )
    parser.addoption(
        "--target-page",
        action="store",
        default=None,
        help="只执行指定 JSP 文件名的回归测试；指定后无视风险等级"
    )
    parser.addoption(
        "--manual",
        action="store_true",
        default=False,
        help="开启半自动接管模式：由人工在浏览器中导航至目标页面，自动化脚本负责后续接管执行"
    )
    parser.addoption(
        "--login-entry",
        action="store",
        default=None,
        help="登录入口名称或序号；未指定且 .env 配置多个入口时启动时人工选择"
    )
    parser.addoption(
        "--struts-config",
        action="store",
        default="data/",
        help="Struts 配置文件路径或目录。支持逗号分隔的列表，若是目录则递归搜索所有 .xml"
    )
    parser.addoption(
        "--checklist-path",
        action="store",
        default=None,
        help="可选：自动化执行用 checklist xlsx。存在 automation_mode=auto 时优先执行 Excel case"
    )
    parser.addoption(
        "--route-map-path",
        action="store",
        default=None,
        help="可选：usable_route_map JSON、目录或 glob；未指定时自动查找 generated/valid/usable_route_map*.json"
    )
    parser.addoption(
        "--run-migration",
        action="store_true",
        default=False,
        help="显式运行需要真实浏览器和登录环境的迁移回归测试"
    )


def pytest_runtest_setup(item):
    if item.name == "test_migration_regression" and not item.config.getoption("--run-migration"):
        pytest.skip("requires --run-migration with real browser/login environment")

@pytest.fixture(scope="session")
def browser_name(request):
    return request.config.getoption("--test-browser")

@pytest.fixture(scope="session")
def mapping_path(request):
    return request.config.getoption("--mapping-path")

@pytest.fixture(scope="session")
def risk_only(request):
    return request.config.getoption("--risk-only")

@pytest.fixture(scope="session")
def regression_limit(request):
    return request.config.getoption("--regression-limit")

@pytest.fixture(scope="session")
def target_page(request):
    return request.config.getoption("--target-page")

@pytest.fixture(scope="session")
def manual(request):
    return request.config.getoption("--manual")

@pytest.fixture(scope="session")
def struts_config(request):
    return request.config.getoption("--struts-config")

@pytest.fixture(scope="session")
def route_map_path(request):
    return request.config.getoption("--route-map-path")

@pytest.fixture(scope="session")
def login_entry(request):
    return Config.select_login_entry(request.config.getoption("--login-entry"), interactive=True)

@pytest.fixture(scope="session")
def browser(browser_name, login_entry):
    _make_process_dpi_aware()
    with sync_playwright() as p:
        # 1. 启动特定浏览器
        if browser_name == "edge":
            browser = p.chromium.launch(
                channel="msedge",
                headless=False,
                args=CHROMIUM_FULLSCREEN_ARGS,
            )
        elif browser_name == "firefox":
            browser = p.firefox.launch(
                headless=False,
                args=FIREFOX_FULLSCREEN_ARGS,
            )
        elif browser_name == "chrome_port":
            if not Config.CHROME_PORTABLE_PATH:
                raise ValueError("CHROME_PORTABLE_PATH not set in .env")
            browser = p.chromium.launch(
                executable_path=Config.CHROME_PORTABLE_PATH, 
                headless=False,
                args=[
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
            )
        else:
            raise ValueError(f"不支持的浏览器类型: {browser_name}")

        yield browser
        browser.close()


def _authenticated_page(browser, base_url):
    context = browser.new_context(
        accept_downloads=True,
        no_viewport=True,
        user_agent="Moonlight-Automation-Agent"
    )
    # 自动处理旧系统的 Alert/Confirm 弹窗
    # 增加 try-except 保护，防止 Dialog 已经关闭时的 ProtocolError
    def _safe_accept(dialog):
        try:
            dialog.accept()
        except:
            pass
    context.on("dialog", _safe_accept)
    
    # 自动处理新页面打开
    def _on_page(new_page):
        try:
            new_page.wait_for_load_state("domcontentloaded", timeout=10000)
        except:
            pass
    context.on("page", _on_page)

    page = context.new_page()
    try:
        page.keyboard.press("Control+0")
        page.goto(base_url, wait_until="load", timeout=30000)
        page.keyboard.press("Control+0")
        page.fill("input[name='user']", Config.USERNAME)
        page.fill("input[name='password']", Config.PASSWORD)
        page.click("input[type='button']")

        # 等待登录态建立
        page.wait_for_load_state("networkidle", timeout=30000)
        page.keyboard.press("Control+0")
    except Exception:
        # 部分环境使用预置会话或基础认证，登录页缺失时不阻断页面创建。
        pass
    return context, page


@pytest.fixture(scope="session")
def legacy_page(browser, login_entry):
    context, page = _authenticated_page(browser, login_entry["legacy_url"])
    yield page
    context.close()


@pytest.fixture(scope="session")
def new_page(browser, login_entry):
    context, page = _authenticated_page(browser, login_entry["new_url"])
    yield page
    context.close()


@pytest.fixture(scope="session")
def page(new_page):
    """
    保留旧入口：提供 New 环境登录态页面。
    """
    yield new_page

def pytest_html_report_title(report):
    report.title = "Moonlight UI 自动化多端适配测试报告"


@pytest.fixture(scope="session")
def checklist_path(request):
    return request.config.getoption("--checklist-path")
