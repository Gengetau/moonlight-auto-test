import json
from pathlib import Path

import pytest

from src.gui_command_builder import (
    build_regression_command,
    html_report_path,
    load_negative_profile_options,
    load_upload_case_options,
    load_page_options,
    negative_profile_labels,
    regression_output_dir,
    upload_case_option_labels,
)


def test_build_regression_command_is_page_and_browser_scoped():
    cmd = build_regression_command(
        {
            "target_page": "ProjectListUploadDisp.jsp",
            "browser": "edge",
            "login_entry": "dev-admin",
            "checklist_path": "generated/valid/migration_checklist.xlsx",
            "route_map_path": "generated/valid/route",
            "force_route_map": True,
            "include_semi_auto": True,
            "include_destructive": True,
            "include_negative": True,
            "negative_profile": "invalid_file",
            "upload_profile_config": "generated/gui/upload_profiles/edge/ProjectListUploadDisp.jsp.json",
        },
        pytest_cmd='"pytest"',
    )

    assert "--test-browser=edge" in cmd
    assert '--target-page="ProjectListUploadDisp.jsp"' in cmd
    assert f'--regression-output-dir="{regression_output_dir("edge")}"' in cmd
    assert f'--html="{html_report_path("edge", "ProjectListUploadDisp.jsp")}"' in cmd
    assert "--include-semi-auto" in cmd
    assert "--include-destructive" in cmd
    assert "--include-negative" in cmd
    assert '--negative-profile="invalid_file"' in cmd
    assert "--upload-profile-config=" in cmd


def test_load_page_options_merges_mapping_routes_and_recent_reports(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(
        json.dumps(
            {
                "page_mappings": [
                    {
                        "page_id": "ProjectListUploadDisp.jsp",
                        "risk": "High",
                        "entry_url": "ProjectListUploadDisp.do",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    route_dir = tmp_path / "route"
    route_dir.mkdir()
    route_map = route_dir / "usable_route_map_legacy.json"
    route_map.write_text(
        json.dumps(
            {
                "verified": [
                    {
                        "target_page": "ProjectListUploadErr.jsp",
                        "target_page_name": "projectlistuploaderr.jsp",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report_dir = tmp_path / "reports"
    recent_dir = report_dir / "edge" / "0001_UopcUploadListDispJP.jsp"
    recent_dir.mkdir(parents=True)
    (recent_dir / "regression_report.html").write_text("<html></html>", encoding="utf-8")

    options = load_page_options(mapping_path=mapping_path, route_dir=route_dir, report_dir=report_dir)
    by_page = {item["page_id"]: item for item in options}

    assert by_page["ProjectListUploadDisp.jsp"]["risk"] == "High"
    assert by_page["ProjectListUploadErr.jsp"]["route_map_path"] == str(route_map)
    assert "recent" in by_page["UopcUploadListDispJP.jsp"]["sources"]


def test_load_upload_case_options_filters_page_upload_cases(tmp_path):
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    checklist = tmp_path / "migration_checklist.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Checklist"
    sheet.append(
        [
            "case_id",
            "page_id",
            "test_title",
            "automation_mode",
            "case_type",
            "action_type",
            "locator",
            "submit_locator",
            "main_step",
            "destructive",
            "enabled",
        ]
    )
    sheet.append(
        [
            "project-upload-valid",
            "ProjectListUploadDisp.jsp",
            "アップロード確認",
            "auto",
            "upload_submit",
            "upload_submit",
            "input[name='uploadFile']",
            "input[value='アップロード']",
            "",
            "false",
            "true",
        ]
    )
    sheet.append(
        [
            "project-snapshot",
            "ProjectListUploadDisp.jsp",
            "初期表示",
            "auto",
            "snapshot",
            "snapshot",
            "__page__",
            "",
            "",
            "false",
            "true",
        ]
    )
    sheet.append(
        [
            "other-upload",
            "Other.jsp",
            "アップロード確認",
            "auto",
            "upload_submit",
            "upload_submit",
            "input[type='file']",
            "",
            "",
            "false",
            "true",
        ]
    )
    workbook.save(checklist)

    cases = load_upload_case_options(checklist, "ProjectListUploadDisp.jsp")
    labels = upload_case_option_labels(cases)

    assert [case["case_id"] for case in cases] == ["project-upload-valid"]
    assert cases[0]["locator"] == "input[name='uploadFile']"
    assert "project-upload-valid" in labels[0]


def test_load_negative_profile_options_reads_page_checklist_cases(tmp_path):
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    checklist = tmp_path / "checklist.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Checklist"
    sheet.append(["case_id", "page_id", "automation_mode", "case_type", "action_type", "test_title"])
    sheet.append(["neg-js", "Upload.jsp", "auto-negative", "negative_js_error", "negative_js_error", "JS error evidence"])
    sheet.append(["neg-http", "Upload.jsp", "auto-negative", "negative_http_500", "negative_http_500", "HTTP 500 evidence"])
    sheet.append(["other", "Other.jsp", "auto-negative", "negative_network_abort", "negative_network_abort", "Other page"])
    workbook.save(checklist)

    options = load_negative_profile_options(checklist, "Upload.jsp")
    profiles = {item["profile"]: item for item in options}
    labels = negative_profile_labels(options)

    assert "negative_js_error" in profiles
    assert profiles["negative_http_500"]["description"] == "HTTP 500 evidence"
    assert any(label.startswith("negative_js_error") for label in labels)
