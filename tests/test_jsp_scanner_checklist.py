import json

from src.checklist_generator import generate_cases, page_entries, plan_page_specific_cases, universal_checklist_markdown_lines, write_excel
from src.jsp_scanner import scan_jsp_source
from src.page_evidence_builder import PageEvidenceBuilder
from src.page_spec_checklist_generator import page_spec_to_cases
from src.page_spec_generator import page_spec_cache_path


def test_spring_form_tags_are_classified_without_inflating_forms():
    source = """
    <form:form id="searchForm" action="/search" method="post">
      <form:input path="keyword" />
      <form:hidden path="token" />
      <form:select path="status">
        <form:option value="A" />
      </form:select>
      <html:file property="uploadFile" />
      <input type="button" name="search" value="Search" />
    </form:form>
    """

    result = scan_jsp_source(source, "Search.jsp")

    assert result["counts"] == {
        "form": 1,
        "field": 4,
        "file": 1,
        "button": 1,
    }

    by_tag = {element["tag"]: element["kind"] for element in result["elements"]}
    assert by_tag["form:form"] == "form"
    assert by_tag["form:input"] == "field"
    assert by_tag["form:hidden"] == "field"
    assert by_tag["form:select"] == "field"
    assert by_tag["form:option"] == "field"
    assert by_tag["html:file"] == "file"


def test_plain_anchor_links_are_scanned_and_runtime_alternatives_are_deduped():
    source = """
    <span class="t12"><a href="#" onclick="fnSubmit('/ProjectMemberUploadTemplateDownload.do');return false"><bean:message key="label.patlics.all456" bundle="PATLICS_MESSAGE" /></a></span>
    <% if(enl.equals("en")) { %>
      <a href="/help_en/project_106_01.html" target="winHelp"><span class="t12"><bean:message bundle='PATLICS_MESSAGE' key='label.mapCitationHead.comment3'/></span></a>
    <% } else { %>
      <a href="/help/project_106_01.html" target="winHelp"><span class="t12"><bean:message bundle='PATLICS_MESSAGE' key='label.mapCitationHead.comment3'/></span></a>
    <% } %>
    <input type="button" name="entry" value="upload" onClick="fnSubmit('/ProjectMemberUpload.do')" />
    <input type="button" name="cancell" value="cancel" onClick="javascript:window.close()" />
    """

    result = scan_jsp_source(source, "ProjectMemberUploadDisp.jsp")
    links = [element for element in result["elements"] if element["kind"] == "link"]
    buttons = [element for element in result["elements"] if element["kind"] == "button"]

    assert result["counts"]["link"] == 2
    assert result["counts"]["button"] == 2
    assert len(links) == 2
    assert len(buttons) == 2
    assert links[0]["locator"] == 'a[onclick="fnSubmit(\'/ProjectMemberUploadTemplateDownload.do\');return false"]'
    assert links[0]["label_key"] == "label.patlics.all456"
    assert links[1]["locator"] == "a[href='/help/project_106_01.html']"
    assert links[1]["label_key"] == "label.mapCitationHead.comment3"


def test_field_elements_generate_automation_ready_cases_and_form_evidence():
    scan_data = {
        "source": "Search.jsp",
        "counts": {"form": 1, "field": 2, "file": 1},
        "elements": [
            {
                "kind": "form",
                "tag": "form:form",
                "line": 1,
                "attributes": {"id": "searchForm", "action": "/search"},
                "locator": "#searchForm",
            },
            {
                "kind": "field",
                "tag": "form:input",
                "line": 2,
                "attributes": {"path": "keyword"},
                "locator": None,
            },
            {
                "kind": "field",
                "tag": "form:hidden",
                "line": 3,
                "attributes": {"path": "token"},
                "locator": None,
            },
            {
                "kind": "file",
                "tag": "html:file",
                "line": 4,
                "attributes": {"property": "uploadFile"},
                "locator": "[name='uploadFile']",
            },
        ],
    }

    cases = generate_cases(scan_data)

    assert len(cases) > 8
    assert {"page", "form", "field", "file"}.issubset({case.kind for case in cases})
    assert any(case.kind == "field" for case in cases)
    assert any(case.automation_mode == "auto" for case in cases)
    form_cases = [case for case in cases if case.kind == "form"]
    assert all("Field completeness check" in case.evidence for case in form_cases)
    assert all("form:input `keyword`" in case.evidence for case in form_cases)


def test_page_mapping_input_uses_full_mappings_and_missing_elements():
    scan_data = {
        "high_risk_pages": [
            {
                "page_id": "HighOnly.jsp",
                "missing_legacy_elements": [
                    {
                        "kind": "button",
                        "line": 10,
                        "locator": "#high",
                        "label": "high",
                    }
                ],
            }
        ],
        "medium_risk_pages": [
            {
                "page_id": "MediumOnly.jsp",
                "missing_legacy_elements": [
                    {
                        "kind": "button",
                        "line": 20,
                        "locator": "#medium",
                        "label": "medium",
                    }
                ],
            }
        ],
        "page_mappings": [
            {
                "page_id": "MatchedA.jsp",
                "elements": [
                    {
                        "kind": "form",
                        "tag": "form",
                        "line": 1,
                        "attributes": {"id": "matchedForm", "action": "/save"},
                        "locator": "#matchedForm",
                    }
                ],
                "missing_legacy_elements": [
                    {
                        "kind": "button",
                        "line": 2,
                        "locator": "#missing",
                        "label": "missing",
                    }
                ],
            },
            {
                "page_id": "MatchedB.jsp",
                "missing_legacy_elements": [
                    {
                        "kind": "link",
                        "line": 3,
                        "locator": "a.details",
                        "label": "details",
                    }
                ],
            },
            {
                "page_id": "MatchedC.jsp",
                "missing_legacy_elements": [],
            },
        ],
    }

    pages = list(page_entries(scan_data))
    cases = generate_cases(scan_data)

    assert [page["page_id"] for page in pages] == ["MatchedA.jsp", "MatchedB.jsp", "MatchedC.jsp"]
    assert {case.page for case in cases} == {"MatchedA.jsp", "MatchedB.jsp", "MatchedC.jsp"}
    assert {"initial_display", "negative_js_error"}.issubset({case.case_type for case in cases})
    assert {"auto", "auto-negative"}.issuperset({case.automation_mode for case in cases})
    assert all(case.generated_by in {"PageCasePlanner", "CaseExpansionRules"} for case in cases)


def test_universal_checklist_lines_include_xls_categories():
    lines = universal_checklist_markdown_lines()
    text = "\n".join(lines)

    assert "1-1 Screen layout" in text
    assert "7 File upload" in text
    assert "13 Multi-browser compatibility" in text


def test_page_specific_excel_contains_profile_and_skipped_sheets(tmp_path):
    from openpyxl import load_workbook

    scan_data = {
        "page_mappings": [
            {
                "page_id": "Upload.jsp",
                "elements": [
                    {"kind": "file", "tag": "html:file", "attributes": {"property": "uploadFile"}, "locator": "input[name='uploadFile']"},
                    {"kind": "button", "tag": "input", "attributes": {"onclick": "fnSubmit('/Upload.do')"}, "locator": "input[name='entry']"},
                ],
            }
        ]
    }
    output = tmp_path / "migration_checklist.xlsx"

    write_excel(output, scan_data, generate_cases(scan_data))

    workbook = load_workbook(output, read_only=True)
    assert "Checklist" in workbook.sheetnames
    assert "PageProfile" in workbook.sheetnames
    assert "SkippedTemplates" in workbook.sheetnames


def test_checklist_generation_appends_existing_runtime_profile_pages(tmp_path):
    runtime_dir = tmp_path / "runtime_profile"
    runtime_dir.mkdir()
    profile_path = runtime_dir / "legacy_runtimeupload_route1.json"
    profile_path.write_text(
        json.dumps(
            {
                "schema": "moonlight.runtime_page_profile.v1",
                "page_id": "RuntimeUpload.jsp",
                "target_page": "RuntimeUpload.jsp",
                "route_id": "route1",
                "side": "legacy",
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
                ],
            }
        ),
        encoding="utf-8",
    )
    scan_data = {"page_mappings": [{"page_id": "StaticOnly.jsp", "elements": []}]}

    without_runtime = generate_cases(scan_data, runtime_profile_dir=runtime_dir, include_runtime_profile_dir=False)
    with_runtime = generate_cases(scan_data, runtime_profile_dir=runtime_dir, include_runtime_profile_dir=True)
    _, _, profiles = plan_page_specific_cases(scan_data, runtime_profile_dir=runtime_dir, include_runtime_profile_dir=True)

    assert {case.page for case in without_runtime} == {"StaticOnly.jsp"}
    assert {"StaticOnly.jsp", "RuntimeUpload.jsp"}.issubset({case.page for case in with_runtime})
    assert any(case.page == "RuntimeUpload.jsp" and case.case_type == "upload_submit" for case in with_runtime)
    runtime_profiles = [profile for profile in profiles if profile.get("page_id") == "RuntimeUpload.jsp"]
    assert runtime_profiles
    assert runtime_profiles[0]["runtime_profile_path"] == str(profile_path)


def test_cached_manual_page_spec_generates_preconditioned_cases(tmp_path):
    page_spec_dir = tmp_path / "page_specs"
    spec_path = page_spec_cache_path("Search.jsp", page_spec_dir)
    spec_path.parent.mkdir()
    spec_path.write_text(
        json.dumps(
            {
                "schema": "moonlight.page_spec.v1",
                "page_id": "Search.jsp",
                "page_type": "search_page",
                "business_summary": "Search page with a result export.",
                "capabilities": {
                    "initial_display": True,
                    "search": True,
                    "result_table": True,
                    "file_download": True,
                },
                "states": [
                    {"id": "initial", "description": "loaded"},
                    {"id": "search_ready", "description": "condition entered"},
                    {"id": "result_ready", "description": "results shown"},
                ],
                "operations": [
                    {
                        "id": "input_keyword",
                        "type": "fill",
                        "from_state": "initial",
                        "to_state": "search_ready",
                        "steps": [{"action_type": "fill", "locator": "input[name='keyword']", "value": "${SEARCH_KEYWORD}"}],
                    },
                    {
                        "id": "execute_search",
                        "type": "search",
                        "title": "Search main path",
                        "from_state": "search_ready",
                        "to_state": "result_ready",
                        "requires": ["input_keyword"],
                        "steps": [{"action_type": "click", "locator": "input[name='search']"}],
                        "expected": {"type": "result_update", "value": "result_ready"},
                    },
                    {
                        "id": "file_output",
                        "type": "file_download",
                        "title": "Export results",
                        "from_state": "result_ready",
                        "requires": ["execute_search"],
                        "steps": [{"action_type": "download", "locator": "input[name='fileOutput']"}],
                        "expected": {"type": "download", "value": ""},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    scan_data = {
        "page_mappings": [
            {
                "page_id": "Search.jsp",
                "elements": [
                    {"kind": "field", "tag": "input", "locator": "input[name='keyword']", "attributes": {"name": "keyword", "type": "text"}},
                    {"kind": "button", "tag": "input", "locator": "input[name='search']", "attributes": {"name": "search", "type": "button"}},
                    {"kind": "button", "tag": "input", "locator": "input[name='fileOutput']", "attributes": {"name": "fileOutput", "type": "button"}},
                ],
            }
        ]
    }

    cases, skipped, profiles = plan_page_specific_cases(
        scan_data,
        use_page_spec=True,
        page_spec_dir=page_spec_dir,
    )

    assert not skipped
    by_type = {case.case_type: case for case in cases}
    assert by_type["search_normal"].generated_by == "ManualPageSpec"
    assert json.loads(by_type["search_normal"].pre_steps)[0]["locator"] == "input[name='keyword']"
    assert json.loads(by_type["file_download"].pre_steps)[-1]["locator"] == "input[name='search']"
    assert json.loads(by_type["file_download"].main_step)["locator"] == "input[name='fileOutput']"
    assert profiles[0]["page_spec_path"] == str(spec_path)


def test_manual_page_spec_target_page_filters_before_export(tmp_path):
    page_spec_dir = tmp_path / "page_specs"
    spec_path = page_spec_cache_path("Second.jsp", page_spec_dir)
    spec_path.parent.mkdir()
    spec_path.write_text(
        json.dumps(
            {
                "schema": "moonlight.page_spec.v1",
                "page_id": "Second.jsp",
                "capabilities": {"initial_display": True},
                "operations": [
                    {
                        "id": "initial_display",
                        "type": "initial_display",
                        "steps": [{"action_type": "snapshot", "locator": "__page__"}],
                        "expected": {"type": "visual", "value": ""},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    scan_data = {
        "page_mappings": [
            {"page_id": "First.jsp", "elements": []},
            {"page_id": "Second.jsp", "elements": []},
        ]
    }

    cases, _, profiles = plan_page_specific_cases(
        scan_data,
        target_pages=["Second.jsp"],
        use_page_spec=True,
        page_spec_dir=page_spec_dir,
    )

    assert {case.page for case in cases} == {"Second.jsp"}
    assert profiles[0]["page_id"] == "Second.jsp"
    assert not (page_spec_dir / "first.page_evidence.json").exists()
    assert (page_spec_dir / "second.page_evidence.json").exists()


def test_page_evidence_includes_static_dom_and_validation_layers():
    html = """
    <html><head><script>
    function validateWwEasySearchForm(form) {
      return validateDate(form) && validateWordLength(form);
    }
    function DateValidations () {
      this.aa = new Array("i7040[0]", "公報日(from) は正しい日付ではありません", new Function ("varName", "this.datePatternStrict='yyyyMMdd'; return this[varName];"));
    }
    function wordLength () {
      this.aa = new Array("i0790", "全文 で一単語の最短制限は 2文字です", new Function ("varName", "this.kind='moji'; this.check='min'; this.wlength='2'; return this[varName];"));
    }
    </script></head>
    <body>
      <form action="./WwEasySearch.do">
        <table>
          <tr><td><a href="javaScript:showTree('0')">日付系</a></td></tr>
          <tr style="display:none"><td><input type="checkbox" name="itemId" value="7040" onclick="setTxtfield(this, '7040')"></td><td>公報日</td></tr>
          <tr><td><a href="javaScript:showTree('2')">文章系</a></td></tr>
          <tr style="display:none"><td><input type="checkbox" name="itemId" value="0790" onclick="setTxtfield(this, '0790')"></td><td>全文</td></tr>
        </table>
        <input type="button" name="search" value="検索" onclick="fnSubmit('./WwEasySearch.do')">
      </form>
    </body></html>
    """

    evidence = PageEvidenceBuilder().build({"page_id": "WwEasySearchMain.jsp", "html": html})
    static_profile = evidence["static_dom_profile"]
    validation = evidence["validation_profile"]

    assert static_profile["hidden_control_count"] >= 2
    assert static_profile["search_item_category_count"] == 2
    assert static_profile["search_item_categories"][0]["items"][0]["item_id"] == "7040"
    assert static_profile["dynamic_input_mapping"][0]["input_locators"] == ['input[name="i7040[0]"]']
    assert validation["validation_sequence"] == ["validateDate", "validateWordLength"]
    assert {rule["rule"] for rule in validation["validation_rules"]} >= {"date", "word_length"}


def test_page_spec_conversion_handles_navigation_and_popup_downloads():
    spec = {
        "page_id": "Search.jsp",
        "capabilities": {"initial_display": True, "file_download": True, "popup": True},
        "operations": [
            {
                "id": "file_output",
                "type": "download",
                "steps": [{"action_type": "click", "locator": "input[name='fileOutput']"}],
                "expected": {"type": "download", "value": ""},
            },
            {
                "id": "reserve_download",
                "type": "download",
                "steps": [{"action_type": "click", "locator": "input[name='reserve']"}],
                "expected": {"type": "popup_or_navigation", "value": "reserve screen opened"},
            },
            {
                "id": "show_biblio_list",
                "type": "navigation",
                "steps": [{"action_type": "click", "locator": "input[name='biblio']"}],
                "expected": {"type": "navigation", "value": "list displayed"},
            },
            {
                "id": "clear_conditions",
                "type": "form",
                "steps": [{"action_type": "click", "locator": "input[name='clear']"}],
                "expected": {"type": "form_reset", "value": "cleared"},
            },
            {
                "id": "save_confirm",
                "type": "browser_dialog",
                "steps": [{"action_type": "browser_dialog", "locator": "input[name='save']"}],
                "expected": {"type": "browser_dialog", "value": "saved"},
            },
            {
                "id": "print_list",
                "type": "print",
                "steps": [{"action_type": "print", "locator": "input[name='print']"}],
                "expected": {"type": "print_invocation", "value": ""},
            },
        ],
    }

    cases = {case["case_id"]: case for case in page_spec_to_cases(spec)}

    assert cases["search-file_output-001"]["case_type"] == "file_download"
    assert cases["search-reserve_download-002"]["case_type"] == "link_navigation"
    assert cases["search-show_biblio_list-003"]["case_type"] == "link_navigation"
    assert cases["search-clear_conditions-004"]["case_type"] == "form_action"
    assert cases["search-save_confirm-005"]["case_type"] == "browser_dialog"
    assert cases["search-save_confirm-005"]["action_type"] == "browser_dialog"
    assert cases["search-save_confirm-005"]["expected_type"] == "browser_dialog"
    assert cases["search-print_list-006"]["case_type"] == "print_output"
    assert cases["search-print_list-006"]["action_type"] == "print"
    assert cases["search-print_list-006"]["expected_type"] == "print_invocation"
