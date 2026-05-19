from playwright.sync_api import Page
import re

def assert_expectation(page: Page, expected_text: str = None, expected_url: str = None):
    """
    封装断言逻辑，比对文本与 URL。
    """
    if expected_text:
        # 简单比对内容是否在页面中，可扩展为精确选择器比对
        content = page.content()
        assert expected_text in content, f"Expected text '{expected_text}' not found in page."
    
    if expected_url:
        current_url = page.url
        # 使用正则部分匹配，忽略后缀差异
        pattern = re.escape(expected_url).replace(r"\.do", r"(\.do)?")
        assert re.search(pattern, current_url), f"URL mismatch. Expected: {expected_url}, Actual: {current_url}"
