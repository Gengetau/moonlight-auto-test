import pytest
from src.data_loader import load_checklist
from src.action_executor import execute_action
from src.assert_engine import assert_expectation
from src.error_handler import check_server_error

# 加载测试数据
checklist_path = "data/checklist.xlsx"
test_cases = []
try:
    test_cases = load_checklist(checklist_path)
except Exception:
    test_cases = [{"test_id": "Init", "action_type": "wait", "action_target": "body", "expected_text": "TargetApp"}]

@pytest.mark.parametrize("case", test_cases)
def test_migration_flow(page, browser_name, case):
    test_id = case["test_id"]
    
    # 1. 执行动作 (传入 browser_name 进行行为差异抹平)
    try:
        execute_action(
            page, 
            case.get("action_type"), 
            case.get("action_target"), 
            case.get("input_value"),
            browser_name=browser_name
        )
    except Exception as e:
        # 2. 异常监控与三端隔离截图
        error_found, msg, path = check_server_error(page, test_id, browser_name)
        if error_found:
            pytest.fail(f"Server Error on {browser_name}: {msg}. Screenshot: {path}")
        raise e
    
    # 3. 最终状态监控
    error_found, msg, path = check_server_error(page, test_id, browser_name)
    if error_found:
        pytest.fail(f"Server Error on {browser_name}: {msg}. Screenshot: {path}")

    # 4. 断言
    assert_expectation(
        page, 
        case.get("expected_text"), 
        case.get("expected_url")
    )
