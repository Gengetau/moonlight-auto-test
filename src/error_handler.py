import os

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError


def check_server_error(page: Page, test_id: str, browser_name: str):
    """Detect common server errors and capture an isolated evidence screenshot."""
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass

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
        screenshot_path = f"./output/screenshots/error_{test_id}_{browser_name}.png"
        os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
        page.screenshot(path=screenshot_path, full_page=True)
        return True, error_msg, screenshot_path

    return False, None, None
