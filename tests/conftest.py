import pytest
from playwright.sync_api import sync_playwright
from src.config_parser import Config
import os

def pytest_addoption(parser):
    parser.addoption(
        "--test-browser", 
        action="store", 
        default="chrome_port", 
        help="支持的参数: edge / firefox / chrome_port"
    )

@pytest.fixture(scope="session")
def browser_name(request):
    return request.config.getoption("--test-browser")

@pytest.fixture(scope="session")
def page(browser_name):
    """
    提供一个带有登录态的浏览器页面 Page 对象，支持多端适配。
    """
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

        # 2. 创建 Context (允许下载，隔离三端)
        context = browser.new_context(
            accept_downloads=True,
            user_agent="Patlics-Automation-Agent"
        )
        page = context.new_page()

        # 3. 执行登录逻辑 (示例)
        page.goto(f"{Config.NEW_URL}/login")
        page.fill("input[name='username']", Config.USERNAME)
        page.fill("input[name='password']", Config.PASSWORD)
        page.click("button[type='submit']")
        
        # 等待登录态建立
        page.wait_for_load_state("networkidle")
        
        yield page
        
        # 4. 清理
        context.close()
        browser.close()

def pytest_html_report_title(report):
    report.title = "Patlics UI 自动化多端适配测试报告"
