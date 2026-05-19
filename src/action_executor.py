from playwright.sync_api import Page

def execute_action(page: Page, action_type: str, selector: str, value: str = None):
    """
    根据操作类型执行 Playwright UI 操作。
    """
    if action_type == "click":
        page.click(selector)
    elif action_type == "fill":
        page.fill(selector, value)
    elif action_type == "upload":
        page.set_input_files(selector, value)
    elif action_type == "download":
        with page.expect_download(timeout=30000) as download_info:
            page.click(selector)
        download = download_info.value
        save_path = f"./output/downloads/{download.suggested_filename}"
        download.save_as(save_path)
        return save_path
    elif action_type == "wait":
        page.wait_for_selector(selector)
    else:
        raise ValueError(f"Unsupported action type: {action_type}")
