import pandas as pd
import os

def load_checklist(file_path):
    """
    解析 Excel 检查单并转换为测试用例字典列表。
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Checklist not found at {file_path}")
    
    # 读取 Excel
    df = pd.read_excel(file_path)
    
    test_cases = []
    for _, row in df.iterrows():
        case = {
            "test_id": row.get("No."),
            "test_category": row.get("内容"),
            "action_target": row.get("定位器"),  # 扩展列
            "action_type": row.get("操作类型"), # 扩展列
            "input_value": row.get("确认事项"),
            "expected_text": row.get("期待值文本"),
            "expected_url": row.get("期待值URL"),
            "target_env": row.get("环境", "new")
        }
        test_cases.append(case)
    
    return test_cases
