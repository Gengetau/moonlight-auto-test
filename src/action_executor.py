from playwright.sync_api import Page, Error as PlaywrightError
import os

def execute_action(page: Page, action_type: str, selector: str, value: str = None, browser_name: str = "chrome"):
    """
    根据操作类型执行 Playwright UI 操作，包含多端适配容错。
    """
    # Firefox 渲染延迟容错
    if browser_name == "firefox":
        page.wait_for_load_state("networkidle", timeout=15000)
    else:
        page.wait_for_load_state("load", timeout=10000)

    if action_type == "click":
        # 增加元素可见性检查
        page.wait_for_selector(selector, state="visible", timeout=10000)
        page.click(selector)
    elif action_type == "fill":
        page.fill(selector, value)
    elif action_type == "upload":
        page.set_input_files(selector, value)
    elif action_type == "download":
        try:
            with page.expect_download(timeout=45000) as download_info:
                page.click(selector)
            download = download_info.value
            # 隔离三端下载文件
            original_filename = download.suggested_filename
            name, ext = os.path.splitext(original_filename)
            save_path = f"./output/downloads/{name}_{browser_name}{ext}"
            download.save_as(save_path)
            return save_path
        except PlaywrightError as e:
            # Edge/Windows 安全拦截补丁
            print(f"Download blocked or failed on {browser_name}: {str(e)}")
            raise e
    elif action_type == "wait":
        page.wait_for_selector(selector, timeout=20000)
    else:
        raise ValueError(f"Unsupported action type: {action_type}")
