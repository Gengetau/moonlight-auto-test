import os
from typing import Iterable

import pandas as pd


def _first_present(row, names: Iterable[str], default=None):
    for name in names:
        if name in row:
            value = row.get(name)
            if pd.notna(value):
                return value
    return default


def load_checklist(file_path):
    """Load an Excel checklist and convert rows into test-case dictionaries."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Checklist not found at {file_path}")

    df = pd.read_excel(file_path)

    test_cases = []
    for _, row in df.iterrows():
        case = {
            "test_id": _first_present(row, ["No.", "case_id", "id"]),
            "test_category": _first_present(row, ["content", "case_type", "test_category", "テスト観点"]),
            "action_target": _first_present(row, ["locator", "selector", "セレクタ"]),
            "action_type": _first_present(row, ["action_type", "action", "アクション"]),
            "input_value": _first_present(row, ["input_value", "test_data", "value", "テストデータ"]),
            "expected_text": _first_present(row, ["expected_text", "expected_value", "期待値"]),
            "expected_url": _first_present(row, ["expected_url", "expected_url_pattern"]),
            "target_env": _first_present(row, ["target_env", "environment"], "new"),
        }
        test_cases.append(case)

    return test_cases
