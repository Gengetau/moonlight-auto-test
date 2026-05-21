from src.checklist_generator import generate_cases
from src.jsp_scanner import scan_jsp_source


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


def test_field_elements_are_form_evidence_not_independent_cases():
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

    assert len(cases) == 8
    assert {case.kind for case in cases} == {"form", "file"}
    assert all(case.kind != "field" for case in cases)
    form_cases = [case for case in cases if case.kind == "form"]
    assert all("字段完整性校验" in case.evidence for case in form_cases)
    assert all("form:input `keyword`" in case.evidence for case in form_cases)
