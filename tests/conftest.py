import pytest
from playwright.sync_api import sync_playwright
from src.config_parser import Config

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
        "--struts-config",
        action="store",
        default="data/struts-config.xml",
        help="Struts 配置文件路径，用于解析 Action 路由"
    )

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
def browser(browser_name):
    with sync_playwright() as p:
        # 1. 启动特定浏览器
        if browser_name == "edge":
            browser = p.chromium.launch(channel="msedge", headless=False)
        elif browser_name == "firefox":
            browser = p.firefox.launch(headless=False)
        elif browser_name == "chrome_port":
            if not Config.CHROME_PORTABLE_PATH:
                raise ValueError("CHROME_PORTABLE_PATH not set in .env")
            browser = p.chromium.launch(
                executable_path=Config.CHROME_PORTABLE_PATH, 
                headless=False,
                args=["--incognito"] # 便携版强制无痕模式
            )
        else:
            raise ValueError(f"不支持的浏览器类型: {browser_name}")

        yield browser
        browser.close()


def _authenticated_page(browser, base_url):
    context = browser.new_context(
        accept_downloads=True,
        user_agent="Moonlight-Automation-Agent"
    )
    page = context.new_page()
    try:
        page.goto(base_url, wait_until="load", timeout=30000)
        page.fill("input[name='user']", Config.USERNAME)
        page.fill("input[name='password']", Config.PASSWORD)
        page.click("input[type='button']")

        # 等待登录态建立
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        # 部分环境使用预置会话或基础认证，登录页缺失时不阻断页面创建。
        pass
    return context, page


@pytest.fixture(scope="session")
def legacy_page(browser):
    context, page = _authenticated_page(browser, Config.LEGACY_URL)
    yield page
    context.close()


@pytest.fixture(scope="session")
def new_page(browser):
    context, page = _authenticated_page(browser, Config.NEW_URL)
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
