import json

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


def test_file_output_button_generates_file_download_not_template_or_form_download():
    cases, _, profile = PageCasePlanner().plan(
        {
            "schema": "moonlight.runtime_page_profile.v1",
            "page_id": "JpGazettePDFDownloadDisp.jsp",
            "controls": [
                {
                    "tag": "form",
                    "name": "fmPDFDownload",
                    "selector": "form[name=\"fmPDFDownload\"]",
                    "visible": True,
                },
                {
                    "tag": "input",
                    "type": "checkbox",
                    "name": "cbKind",
                    "value": "10",
                    "selector": "input[name=\"cbKind\"][value=\"10\"]",
                    "visible": True,
                },
                {
                    "tag": "input",
                    "type": "button",
                    "name": "btSave",
                    "value": "出　　力",
                    "text": "出　　力",
                    "onclick": "fnDownload('/JpGazettePDFDownload.do?method=forList')",
                    "selector": "input[name=\"btSave\"][type=\"button\"]",
                    "visible": True,
                },
                {
                    "tag": "input",
                    "type": "button",
                    "name": "btCancel",
                    "value": "キャンセル",
                    "onclick": "fnCancel()",
                    "selector": "input[name=\"btCancel\"][type=\"button\"]",
                    "visible": True,
                },
            ],
        }
    )

    by_type = {case["case_type"]: case for case in cases}

    assert "download_template" not in by_type
    assert by_type["file_download"]["locator"] == 'input[name="btSave"][type="button"]'
    assert by_type["file_download"]["action_type"] == "download"
    assert by_type["file_download"]["expected_type"] == "download"
    assert profile["capabilities"]["template_download"] is False
    assert profile["capabilities"]["file_download"] is True
    assert profile["capabilities"]["output_options"] is True
    assert profile["option_controls"][0]["locator"] == 'input[name="cbKind"][type="checkbox"][value="10"]'
    assert [item["locator"] for item in profile["file_download_actions"]] == ['input[name="btSave"][type="button"]']
    assert profile["capabilities"]["back_action"] is True
    option_case = next(case for case in cases if case.get("viewpoint_id") == "option_matrix_representative")
    assert json.loads(option_case["pre_steps"])[0]["action_type"] == "check"
    assert json.loads(option_case["main_step"]) == {"action_type": "download", "locator": 'input[name="btSave"][type="button"]'}


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

    assert {case["case_type"] for case in cases} == {"initial_display", "negative_js_error"}
    assert {case.get("viewpoint_id") for case in cases if case.get("parent_case_id")} == {"layout_text", "script_error"}
    assert next(case for case in cases if case["case_type"] == "negative_js_error")["automation_mode"] == "auto-negative"
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


def test_runtime_profile_generates_back_and_delete_cases():
    cases, _, profile = PageCasePlanner().plan(
        {
            "schema": "moonlight.runtime_page_profile.v1",
            "page_id": "UopcUploadListDispJP.jsp",
            "controls": [
                {
                    "tag": "input",
                    "type": "button",
                    "value": "削除",
                    "onclick": "deleteFile('20260424161638~w1~NON~UopcSampleDataJp (1).csv')",
                    "selector": "input[onclick*=\"deleteFile('20260424161638\"]",
                    "visible": True,
                },
                {
                    "tag": "input",
                    "type": "button",
                    "value": "削除",
                    "onclick": "deleteFile('20260424160800~w1~NON~UopcSampleDataJp.csv')",
                    "selector": "input[onclick*=\"deleteFile('20260424160800\"]",
                    "visible": True,
                },
                {
                    "tag": "input",
                    "type": "button",
                    "value": "戻る",
                    "onclick": "submitForm('PatlicsMenuForm','./UopcUploadDispJP.do','')",
                    "selector": "input[onclick*=\"UopcUploadDispJP\"]",
                    "visible": True,
                },
            ],
        }
    )

    by_type = {case["case_type"]: case for case in cases}

    assert profile["capabilities"]["delete_action"] is True
    assert profile["capabilities"]["back_action"] is True
    assert len(profile["delete_actions"]) == 1
    assert by_type["delete_action"]["automation_mode"] == "auto-db"
    assert by_type["delete_action"]["destructive"] == "true"
    assert by_type["delete_action"]["locator"] == 'input[onclick^="deleteFile("]'
    assert by_type["back_action"]["automation_mode"] == "auto"
    assert by_type["back_action"]["locator"] == 'input[onclick*="UopcUploadDispJP"]'


def test_runtime_profile_generates_create_and_update_db_cases():
    cases, _, profile = PageCasePlanner().plan(
        {
            "schema": "moonlight.runtime_page_profile.v1",
            "page_id": "Edit.jsp",
            "controls": [
                {
                    "tag": "input",
                    "type": "button",
                    "value": "登録",
                    "onclick": "submitForm('EditForm','./Entry.do','')",
                    "selector": "input[onclick*=\"Entry\"]",
                    "visible": True,
                },
                {
                    "tag": "input",
                    "type": "button",
                    "value": "更新",
                    "onclick": "submitForm('EditForm','./Update.do','')",
                    "selector": "input[onclick*=\"Update\"]",
                    "visible": True,
                },
            ],
        }
    )

    by_type = {case["case_type"]: case for case in cases}

    assert profile["capabilities"]["create_action"] is True
    assert profile["capabilities"]["update_action"] is True
    assert by_type["create_action"]["automation_mode"] == "auto-db"
    assert by_type["update_action"]["automation_mode"] == "auto-db"
    assert by_type["create_action"]["locator"] == 'input[onclick*="Entry"]'
    assert by_type["update_action"]["locator"] == 'input[onclick*="Update"]'


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


def test_runtime_profile_generates_negative_http_and_network_cases():
    cases, _, profile = PageCasePlanner().plan(
        {
            "schema": "moonlight.runtime_page_profile.v1",
            "page_id": "NegativeUpload.jsp",
            "controls": [
                {
                    "tag": "input",
                    "type": "file",
                    "name": "uploadFile",
                    "selector": "input[name=\"uploadFile\"]",
                    "action": "/patlics/NegativeUploadConf.do",
                    "ownerFormName": "UploadForm",
                    "visible": True,
                },
                {
                    "tag": "input",
                    "type": "button",
                    "value": "確認",
                    "selector": "input[onclick*=\"NegativeUploadConf\"]",
                    "onclick": "submitForm('UploadForm','./NegativeUploadConf.do','')",
                    "action": "/patlics/NegativeUploadConf.do",
                    "ownerFormName": "UploadForm",
                    "visible": True,
                },
            ],
        }
    )

    by_type = {case["case_type"]: case for case in cases}

    assert profile["capabilities"]["form_submit"] is True
    assert by_type["negative_http_500"]["automation_mode"] == "auto-negative"
    assert by_type["negative_http_500"]["expected_value"] == "**/NegativeUploadConf.do*"
    assert by_type["negative_http_500"]["main_step"]
    assert by_type["negative_network_abort"]["expected_type"] == "network_abort"
