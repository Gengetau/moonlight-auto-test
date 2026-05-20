from src.page_mapping import build_mapping


def test_build_mapping_detects_common_navigation_and_locator_change():
    legacy = {
        "root": "legacy",
        "pages": [
            {
                "source": r"C:\legacy\Sample.jsp",
                "counts": {"form": 1, "button": 1},
                "elements": [
                    {
                        "kind": "form",
                        "tag": "html:form",
                        "attributes": {"action": "/SaveAction"},
                        "locator": None,
                    },
                    {
                        "kind": "button",
                        "tag": "input",
                        "attributes": {"name": "save", "onClick": "submitSave()"},
                        "locator": "[name='save']",
                    },
                ],
            }
        ],
    }
    new = {
        "root": "new",
        "pages": [
            {
                "source": r"C:\new\Sample.jsp",
                "counts": {"form": 1, "button": 1},
                "elements": [
                    {
                        "kind": "form",
                        "tag": "form",
                        "attributes": {"action": "/SaveAction"},
                        "locator": "[name='SampleForm']",
                    },
                    {
                        "kind": "button",
                        "tag": "input",
                        "attributes": {"name": "save", "onClick": "submitSave()"},
                        "locator": "#saveButton",
                    },
                ],
            }
        ],
    }

    mapping = build_mapping(legacy, new)

    assert mapping["summary"]["matched_pages"] == 1
    assert mapping["common_navigation_paths"] == ["SaveAction"]
    page = mapping["page_mappings"][0]
    assert page["risk"] == "Medium"
    assert page["locator_changes"][0]["legacy_locator"] == "[name='save']"
    assert page["locator_changes"][0]["new_locator"] == "#saveButton"


def test_build_mapping_reports_legacy_only_elements():
    legacy = {
        "pages": [
            {
                "source": "/legacy/Upload.jsp",
                "counts": {"file": 1},
                "elements": [
                    {
                        "kind": "file",
                        "tag": "html:file",
                        "attributes": {"property": "trainFile"},
                        "locator": "[name='trainFile']",
                    }
                ],
            }
        ]
    }
    new = {
        "pages": [
            {
                "source": "/new/Upload.jsp",
                "counts": {},
                "elements": [],
            }
        ]
    }

    mapping = build_mapping(legacy, new)

    page = mapping["page_mappings"][0]
    assert page["risk"] == "High"
    assert page["missing_legacy_elements"][0]["kind"] == "file"
    assert page["missing_legacy_elements"][0]["locator"] == "[name='trainFile']"
