from src.page_case_planner import PageCasePlanner


def planned_case_types(page_mapping):
    cases, skipped, profile = PageCasePlanner().plan(page_mapping)
    return {case["case_type"] for case in cases}, skipped, profile


def test_file_and_submit_page_generates_upload_cases_only():
    case_types, skipped, profile = planned_case_types(
        {
            "page_id": "Upload.jsp",
            "elements": [
                {
                    "kind": "form",
                    "tag": "form",
                    "attributes": {"action": "/Upload.do", "method": "post"},
                    "locator": "form[action*='Upload']",
                },
                {
                    "kind": "file",
                    "tag": "html:file",
                    "attributes": {"property": "uploadFile"},
                    "locator": "input[name='uploadFile']",
                },
                {
                    "kind": "button",
                    "tag": "input",
                    "attributes": {"name": "entry", "type": "button", "onclick": "fnSubmit('/Upload.do')"},
                    "locator": "input[name='entry']",
                },
            ],
        }
    )

    assert {"initial_display", "upload_select", "upload_submit", "upload_without_file"}.issubset(case_types)
    assert "search_normal" not in case_types
    assert "result_table_verify" not in case_types
    assert "download_template" not in case_types
    assert profile["capabilities"]["upload_submit"] is True
    assert any(item["template_id"] == "search_normal" for item in skipped)


def test_file_download_and_close_without_submit_does_not_generate_upload_submit():
    case_types, _, profile = planned_case_types(
        {
            "page_id": "UploadDownload.jsp",
            "elements": [
                {
                    "kind": "file",
                    "tag": "html:file",
                    "attributes": {"property": "uploadFile"},
                    "locator": "input[name='uploadFile']",
                },
                {
                    "kind": "link",
                    "tag": "a",
                    "attributes": {"onclick": "fnSubmit('/ProjectMemberUploadTemplateDownload.do');return false"},
                    "locator": "a[onclick*='TemplateDownload']",
                },
                {
                    "kind": "button",
                    "tag": "input",
                    "attributes": {"type": "button", "onclick": "javascript:window.close();"},
                    "locator": "input[onclick*='window.close']",
                },
            ],
        }
    )

    assert {"initial_display", "upload_select", "download_template", "close_window"}.issubset(case_types)
    assert "upload_submit" not in case_types
    assert "upload_without_file" not in case_types
    assert profile["capabilities"]["template_download"] is True
    assert profile["capabilities"]["close_window"] is True


def test_search_form_and_result_table_generates_search_and_table_cases():
    case_types, _, profile = planned_case_types(
        {
            "page_id": "Search.jsp",
            "elements": [
                {
                    "kind": "form",
                    "tag": "form",
                    "attributes": {"action": "/Search.do"},
                    "locator": "form[action*='Search']",
                },
                {
                    "kind": "field",
                    "tag": "input",
                    "attributes": {"name": "keyword", "type": "text"},
                    "locator": "input[name='keyword']",
                },
                {
                    "kind": "button",
                    "tag": "input",
                    "attributes": {"name": "search", "type": "button", "value": "検索", "onclick": "fnSubmit('/Search.do')"},
                    "locator": "input[name='search']",
                },
                {"kind": "table", "tag": "table", "locator": "table.result"},
            ],
        }
    )

    assert {"initial_display", "search_normal", "result_table_verify"}.issubset(case_types)
    assert "upload_select" not in case_types
    assert "upload_submit" not in case_types
    assert "download_template" not in case_types
    assert profile["capabilities"]["search"] is True
    assert profile["capabilities"]["result_table"] is True


def test_plain_page_generates_initial_display_and_skips_other_templates():
    cases, skipped, profile = PageCasePlanner().plan({"page_id": "Plain.jsp", "elements": []})

    assert {case["case_type"] for case in cases} == {"initial_display"}
    assert {case.get("viewpoint_id") for case in cases if case.get("parent_case_id")} == {"layout_text", "script_error"}
    assert profile["capabilities"]["initial_display"] is True
    assert any(item["template_id"] == "upload_select" and item["missing_capabilities"] == "file_upload" for item in skipped)


def test_runtime_profile_controls_generate_page_specific_cases():
    case_types, _, profile = planned_case_types(
        {
            "schema": "moonlight.runtime_page_profile.v1",
            "page_id": "RuntimeUpload.jsp",
            "url": "http://example.test/patlics/RuntimeUpload.do",
            "controls": [
                {
                    "tag": "input",
                    "type": "file",
                    "name": "uploadFile",
                    "selector": "input[name=\"uploadFile\"][type=\"file\"]",
                    "visible": True,
                },
                {
                    "tag": "input",
                    "type": "button",
                    "name": "entry",
                    "value": "Upload",
                    "onclick": "fnSubmit('/RuntimeUpload.do')",
                    "selector": "input[name=\"entry\"][type=\"button\"]",
                    "visible": True,
                },
                {
                    "tag": "table",
                    "id": "resultTable",
                    "selector": "#resultTable",
                    "visible": True,
                    "attributes": {"id": "resultTable"},
                },
            ],
        }
    )

    assert {"initial_display", "upload_select", "upload_submit", "result_table_verify"}.issubset(case_types)
    assert profile["profile_source"] == "moonlight.runtime_page_profile.v1"
    assert profile["counts"]["file"] == 1
    assert profile["capabilities"]["upload_submit"] is True


def test_runtime_profile_ignores_container_tables_when_selecting_upload_controls():
    cases, _, profile = PageCasePlanner().plan(
        {
            "schema": "moonlight.runtime_page_profile.v1",
            "page_id": "ProjectListUploadDisp.jsp",
            "controls": [
                {
                    "tag": "form",
                    "name": "PatlicsMenuForm",
                    "selector": "form[name=\"PatlicsMenuForm\"]",
                    "action": "./ProjectListUploadMain.do",
                    "visible": True,
                    "raw": "<form name=\"PatlicsMenuForm\"></form>",
                },
                {
                    "tag": "a",
                    "text": "ログアウト",
                    "selector": "a[onclick*=\"PatlicsAdminLogout\"]",
                    "onclick": "submitForm('PatlicsMenuForm','./PatlicsAdminLogout.do','logout'); return false;",
                    "ownerFormName": "PatlicsMenuForm",
                    "visible": True,
                },
                {
                    "tag": "form",
                    "name": "ProjectListUploadForm",
                    "selector": "form[name=\"ProjectListUploadForm\"]",
                    "action": "/patlics/ProjectListUploadConf.do",
                    "visible": True,
                    "raw": "<form name=\"ProjectListUploadForm\"><input type=\"file\"></form>",
                },
                {
                    "tag": "table",
                    "selector": "form > table",
                    "text": "ファイルの場所",
                    "visible": True,
                    "raw": "<table><input type=\"file\"></table>",
                },
                {
                    "tag": "a",
                    "text": "テンプレートダウンロード",
                    "selector": "a[onclick*=\"TemplateDownload\"]",
                    "onclick": "fnSubmit('/ProjectListUploadTemplateDownload.do');return false",
                    "ownerFormName": "ProjectListUploadForm",
                    "visible": True,
                },
                {
                    "tag": "a",
                    "text": "こちら",
                    "href": "../help/search_format/search_format_projectListUpload.html",
                    "selector": "a[href=\"../help/search_format/search_format_projectListUpload.html\"]",
                    "target": "winHelp",
                    "visible": True,
                },
                {
                    "tag": "input",
                    "type": "file",
                    "name": "uploadFile",
                    "selector": "input[name=\"uploadFile\"][type=\"file\"]",
                    "action": "/patlics/ProjectListUploadConf.do",
                    "ownerFormName": "ProjectListUploadForm",
                    "visible": True,
                },
                {
                    "tag": "input",
                    "type": "button",
                    "value": "ファイルをアップロードして確認画面へ",
                    "selector": "input[onclick*=\"ProjectListUploadConf\"]",
                    "onclick": "submitForm('ProjectListUploadForm','./ProjectListUploadConf.do','')",
                    "action": "/patlics/ProjectListUploadConf.do",
                    "ownerFormName": "ProjectListUploadForm",
                    "visible": True,
                },
            ],
        }
    )

    by_type = {case["case_type"]: case for case in cases}

    assert by_type["upload_select"]["locator"] == "input[name='uploadFile']"
    assert by_type["upload_submit"]["submit_locator"] == 'input[onclick*="ProjectListUploadConf"]'
    assert by_type["download_template"]["locator"] == 'a[onclick*="TemplateDownload"]'
    assert by_type["link_navigation"]["locator"] == 'a[href="../help/search_format/search_format_projectListUpload.html"]'
    assert "result_table_verify" not in by_type
    assert profile["capabilities"]["result_table"] is False
