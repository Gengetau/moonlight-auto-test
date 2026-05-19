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
    # 占位数据，用于演示
    test_cases = [{"test_id": "Init", "action_type": "wait", "action_target": "body", "expected_text": "PatentSQUARE"}]

@pytest.mark.parametrize("case", test_cases)
def test_migration_flow(page, case):
    test_id = case["test_id"]
    
    # 1. 执行动作
    try:
        execute_action(
            page, 
            case.get("action_type"), 
            case.get("action_target"), 
            case.get("input_value")
        )
    except Exception as e:
        # 2. 异常监控与截图
        error_found, msg, path = check_server_error(page, test_id)
        if error_found:
            pytest.fail(f"Server Error detected: {msg}. Screenshot: {path}")
        raise e
    
    # 3. 最终状态监控 (防止动作执行完但页面报错)
    error_found, msg, path = check_server_error(page, test_id)
    if error_found:
        pytest.fail(f"Server Error detected: {msg}. Screenshot: {path}")

    # 4. 断言
    assert_expectation(
        page, 
        case.get("expected_text"), 
        case.get("expected_url")
    )
