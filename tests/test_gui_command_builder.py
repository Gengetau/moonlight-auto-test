import json
from pathlib import Path

import pytest

from src.gui_command_builder import (
    bounded_console_output,
    build_regression_command,
    claim_regression_queue_configs,
    create_regression_queue_run,
    current_regression_queue_config,
    effective_checklist_path_for_page,
    expand_regression_queue_configs,
    guided_checklist_path_for,
    html_report_path,
    load_negative_profile_options,
    load_upload_case_options,
    load_page_options,
    negative_profile_labels,
    pause_regression_queue_run,
    regression_output_dir,
    record_claimed_regression_queue_result,
    record_regression_queue_result,
    resume_regression_queue_run,
    run_regression_queue,
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


def test_run_regression_queue_continues_after_failed_page():
    calls = []

    def run_page(config):
        calls.append(config["target_page"])
        if config["target_page"] == "Second.jsp":
            return {"return_code": 1}
        if config["target_page"] == "Third.jsp":
            raise RuntimeError("route setup failed")
        return {"return_code": 0}

    results = run_regression_queue(
        [
            {"target_page": "First.jsp"},
            {"target_page": "Second.jsp"},
            {"target_page": "Third.jsp"},
            {"target_page": "Fourth.jsp"},
        ],
        run_page,
    )

    assert calls == ["First.jsp", "Second.jsp", "Third.jsp", "Fourth.jsp"]
    assert [item["return_code"] for item in results] == [0, 1, None, 0]
    assert results[2]["error"] == "route setup failed"
    assert [item["target_page"] for item in results] == calls


def test_expand_regression_queue_configs_runs_all_pages_per_browser_in_order():
    configs = expand_regression_queue_configs(
        [
            {"target_page": "First.jsp", "enabled": True},
            {"target_page": "Second.jsp", "enabled": True},
        ],
        ["edge", "chrome_port"],
    )

    assert [(item["browser"], item["target_page"]) for item in configs] == [
        ("edge", "First.jsp"),
        ("edge", "Second.jsp"),
        ("chrome_port", "First.jsp"),
        ("chrome_port", "Second.jsp"),
    ]
    assert configs[0]["html_path"] == html_report_path("edge", "First.jsp")
    assert configs[2]["regression_output_dir"] == regression_output_dir("chrome_port")


def test_expand_regression_queue_configs_deduplicates_browsers():
    configs = expand_regression_queue_configs(
        [{"target_page": "First.jsp"}],
        ["edge", "edge", "firefox"],
    )

    assert [(item["browser"], item["target_page"]) for item in configs] == [
        ("edge", "First.jsp"),
        ("firefox", "First.jsp"),
    ]


def test_persistent_regression_queue_run_advances_all_cards_after_failures():
    queue_run = create_regression_queue_run(
        {"target_page": f"Page{index}.jsp"}
        for index in range(1, 9)
    )

    while queue_run["status"] == "running":
        config = current_regression_queue_config(queue_run)
        assert config is not None
        return_code = 1 if config["target_page"] == "Page4.jsp" else 0
        queue_run = record_regression_queue_result(queue_run, {"return_code": return_code})

    assert queue_run["status"] == "complete"
    assert queue_run["next_index"] == 8
    assert [item["target_page"] for item in queue_run["results"]] == [
        "Page1.jsp",
        "Page2.jsp",
        "Page3.jsp",
        "Page4.jsp",
        "Page5.jsp",
        "Page6.jsp",
        "Page7.jsp",
        "Page8.jsp",
    ]
    assert [item["return_code"] for item in queue_run["results"]] == [0, 0, 0, 1, 0, 0, 0, 0]


def test_persistent_regression_queue_result_records_browser_context():
    queue_run = create_regression_queue_run(
        [{"target_page": "First.jsp", "browser": "edge", "browser_label": "Microsoft Edge"}]
    )

    queue_run = record_regression_queue_result(queue_run, {"return_code": 0})

    assert queue_run["results"][0]["target_page"] == "First.jsp"
    assert queue_run["results"][0]["browser"] == "edge"
    assert queue_run["results"][0]["browser_label"] == "Microsoft Edge"


def test_claim_regression_queue_configs_reserves_parallel_jobs_without_serial_increment():
    queue_run = create_regression_queue_run(
        [{"target_page": "First.jsp"}, {"target_page": "Second.jsp"}, {"target_page": "Third.jsp"}],
        max_parallel=2,
        run_id="run-1",
    )

    queue_run, claimed = claim_regression_queue_configs(queue_run, 2)

    assert queue_run["run_id"] == "run-1"
    assert queue_run["max_parallel"] == 2
    assert queue_run["next_index"] == 2
    assert [(item["queue_index"], item["target_page"]) for item in claimed] == [
        (0, "First.jsp"),
        (1, "Second.jsp"),
    ]


def test_record_claimed_regression_queue_result_keeps_parallel_claim_cursor():
    queue_run = create_regression_queue_run(
        [
            {"target_page": "First.jsp", "browser": "edge"},
            {"target_page": "Second.jsp", "browser": "chrome_port"},
        ],
        max_parallel=2,
    )
    queue_run, claimed = claim_regression_queue_configs(queue_run, 2)

    queue_run = record_claimed_regression_queue_result(
        queue_run,
        {"queue_index": claimed[1]["queue_index"], "return_code": 0},
    )
    assert queue_run["status"] == "running"
    assert queue_run["next_index"] == 2
    assert queue_run["results"][0]["target_page"] == "Second.jsp"
    assert queue_run["results"][0]["browser"] == "chrome_port"

    queue_run = record_claimed_regression_queue_result(
        queue_run,
        {"queue_index": claimed[0]["queue_index"], "return_code": 1},
    )
    assert queue_run["status"] == "complete"
    assert queue_run["next_index"] == 2
    assert [item["target_page"] for item in queue_run["results"]] == ["Second.jsp", "First.jsp"]


def test_paused_regression_queue_does_not_expose_next_card_until_resumed():
    queue_run = create_regression_queue_run(
        [{"target_page": "First.jsp"}, {"target_page": "Second.jsp"}]
    )
    queue_run = record_regression_queue_result(queue_run, {"return_code": 0})
    queue_run = pause_regression_queue_run(queue_run, reason="report_preview")

    assert queue_run["status"] == "paused"
    assert queue_run["pause_reason"] == "report_preview"
    assert current_regression_queue_config(queue_run) is None

    queue_run = resume_regression_queue_run(queue_run)

    assert queue_run["status"] == "running"
    assert "pause_reason" not in queue_run
    assert current_regression_queue_config(queue_run)["target_page"] == "Second.jsp"


def test_bounded_console_output_keeps_recent_tail():
    rendered = bounded_console_output("0123456789", max_chars=4)

    assert "6 earlier character(s) omitted" in rendered
    assert rendered.endswith("6789")


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


def test_guided_checklist_path_uses_generic_screen_id_filename(tmp_path):
    path = guided_checklist_path_for(
        "NonjavaScreeningMainFrame.jsp",
        base_dir=tmp_path,
    )
    assert path == tmp_path / "NonjavaScreeningMainFrame_checklist.json"

    person_path = guided_checklist_path_for("WwPersonAidMain.jsp", base_dir=tmp_path)
    assert person_path == tmp_path / "WwPersonAidMain_checklist.json"

    do_path = guided_checklist_path_for("JpGazetteForNumberSearch.do?method=print", base_dir=tmp_path)
    assert do_path == tmp_path / "JpGazetteForNumberSearch_checklist.json"

    generic_path = guided_checklist_path_for("WwSearchAid.jsp", base_dir=tmp_path)
    assert generic_path == tmp_path / "WwSearchAid_checklist.json"


def test_effective_checklist_path_prefers_existing_page_guided_json(tmp_path):
    guided = tmp_path / "WwSearchAid_checklist.json"
    guided.write_text("{}", encoding="utf-8")

    assert effective_checklist_path_for_page("WwSearchAid.jsp", "", base_dir=tmp_path) == str(guided)
    assert (
        effective_checklist_path_for_page(
            "WwSearchAid.jsp",
            "generated/valid/migration_checklist.xlsx",
            base_dir=tmp_path,
        )
        == str(guided)
    )
    assert effective_checklist_path_for_page("Missing.jsp", "", base_dir=tmp_path) == "generated/valid/migration_checklist.xlsx"


def test_effective_checklist_path_preserves_custom_existing_file(tmp_path):
    guided = tmp_path / "WwSearchAid_checklist.json"
    guided.write_text("{}", encoding="utf-8")
    custom = tmp_path / "custom_cases.json"
    custom.write_text("{}", encoding="utf-8")
    stale_guided = tmp_path / "OtherPage_checklist.json"
    stale_guided.write_text("{}", encoding="utf-8")

    assert effective_checklist_path_for_page("WwSearchAid.jsp", custom, base_dir=tmp_path) == str(custom)
    assert effective_checklist_path_for_page("WwSearchAid.jsp", stale_guided, base_dir=tmp_path) == str(guided)


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


def test_load_upload_case_options_reads_guided_json(tmp_path):
    checklist = tmp_path / "guided.json"
    checklist.write_text(
        json.dumps(
            {
                "schema": "moonlight.guided_checklist.v1",
                "page_id": "Upload.do",
                "cases": [
                    {
                        "case_id": "guided-upload",
                        "title": "Guided upload",
                        "automation_mode": "auto",
                        "steps": [
                            {
                                "action_type": "upload",
                                "locator": "input[type='file']",
                            },
                            {
                                "action_type": "click",
                                "locator": "input[value='Submit']",
                            },
                        ],
                    },
                    {
                        "case_id": "guided-snapshot",
                        "title": "Snapshot",
                        "automation_mode": "auto",
                        "steps": [{"action_type": "snapshot", "locator": "__page__"}],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    cases = load_upload_case_options(checklist, "Upload.jsp")

    assert [case["case_id"] for case in cases] == ["guided-upload"]
    assert cases[0]["locator"] == "input[type='file']"
    assert cases[0]["submit_locator"] == "input[value='Submit']"


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


def test_load_negative_profile_options_reads_guided_json(tmp_path):
    checklist = tmp_path / "guided.json"
    checklist.write_text(
        json.dumps(
            {
                "schema": "moonlight.guided_checklist.v1",
                "page_id": "Upload.do",
                "cases": [
                    {
                        "case_id": "neg-js",
                        "title": "Guided JS error",
                        "case_type": "negative_js_error",
                        "action_type": "negative_js_error",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    options = load_negative_profile_options(checklist, "Upload.jsp")
    profiles = {item["profile"]: item for item in options}

    assert profiles["negative_js_error"]["description"] == "Guided JS error"
    assert "negative_http_500" in profiles
