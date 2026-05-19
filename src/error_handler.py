from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
import os

def check_server_error(page: Page, test_id: str):
    """
    监控并处理服务器异常，执行截图。
    """
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except PlaywrightTimeoutError:
        pass # 继续检查内容
    
    content = page.content()
    error_found = False
    error_msg = ""
    
    if "504 Gateway Time-out" in content:
        error_found = True
        error_msg = "504 Gateway Time-out"
    elif "500 Internal Server Error" in content:
        error_found = True
        error_msg = "500 Internal Server Error"
    
    if error_found:
        screenshot_path = f"./output/screenshots/error_{test_id}.png"
        os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
        page.screenshot(path=screenshot_path, full_page=True)
        return True, error_msg, screenshot_path
    
    return False, None, None
