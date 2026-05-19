import pytest
from playwright.sync_api import Playwright, Page, expect

# 登录凭证，建议使用环境变量或秘密管理工具
BASE_URL = "https://your-patentsquare-url.com"
USERNAME = "testuser"
PASSWORD = "your_password"

@pytest.fixture(scope="session")
def page(playwright: Playwright) -> Page:
    """
    提供一个带有登录态的浏览器页面 Page 对象。
    """
    browser = playwright.chromium.launch(headless=False) # 可设置为 True
    context = browser.new_context()
    page = context.new_page()

    # --- 登录逻辑 ---
    # 1. 导航到登录页
    page.goto(f"{BASE_URL}/login")
    
    # 2. 输入用户名和密码
    page.fill("input[name='username']", USERNAME)
    page.fill("input[name='password']", PASSWORD)
    
    # 3. 点击登录
    page.click("button[type='submit']")
    
    # 4. 等待登录成功（例如，等待某个特定元素出现）
    page.wait_for_selector("#user-profile-menu")
    
    print("Login successful, session established.")
    
    yield page
    
    # --- 清理逻辑 ---
    context.close()
    browser.close()

def pytest_html_report_title(report):
    """
    自定义 pytest-html 报告标题。
    """
    report.title = "Patlics UI 自动化迁移测试报告"
