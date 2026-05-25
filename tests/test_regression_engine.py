import json
from pathlib import Path

from PIL import Image

from src.action_executor import (
    _capture_state,
    _opens_popup_hint,
    _safe_download_filename,
    build_steps_from_page_mapping,
    infer_semantic_action,
)
from src.assert_engine import compare_visual_screenshot
from src.regression_engine import RegressionEngine
from src.route_navigator import RouteMapCatalog


def test_compare_visual_screenshot_writes_diff(tmp_path):
    legacy = tmp_path / "legacy.png"
    new = tmp_path / "new.png"
    diff = tmp_path / "diff.png"
    Image.new("RGB", (2, 2), "white").save(legacy)
    image = Image.new("RGB", (2, 2), "white")
    image.putpixel((0, 0), (0, 0, 0))
    image.save(new)

    result = compare_visual_screenshot(str(legacy), str(new), str(diff), threshold_percent=0)

    assert result["status"] == "DIFF"
    assert result["diff_percent"] == 25.0
    assert diff.exists()


def test_select_pages_orders_high_then_medium(tmp_path):
    mapping = {
        "page_mappings": [
            {"page_id": "low.jsp", "risk": "Low"},
            {"page_id": "medium.jsp", "risk": "Medium"},
            {"page_id": "high.jsp", "risk": "High"},
        ]
    }
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"))
    pages = engine.select_pages(risk_only=True)

    assert [page["page_id"] for page in pages] == ["high.jsp", "medium.jsp"]


def test_select_pages_target_page_ignores_risk(tmp_path):
    mapping = {
        "page_mappings": [
            {"page_id": "low.jsp", "risk": "Low"},
            {"page_id": "high.jsp", "risk": "High"},
        ]
    }
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"))
    pages = engine.select_pages(risk_only=True, target_page="low.jsp")

    assert [page["page_id"] for page in pages] == ["low.jsp"]


def test_select_pages_target_page_reports_missing_mapping(tmp_path):
    mapping = {"page_mappings": [{"page_id": "exists.jsp", "risk": "High"}]}
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"))

    try:
        engine.select_pages(target_page="missing.jsp")
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected missing target page to raise ValueError")

    assert "Target JSP page not found" in message
    assert "missing.jsp" in message
    assert "exists.jsp" in message


def test_route_map_catalog_selects_verified_route_for_target(tmp_path):
    route_map = tmp_path / "usable_route_map.json"
    route_map.write_text(
        json.dumps(
            {
                "schema": "moonlight.usable_route_map.v1",
                "verified": [
                    {
                        "route_id": "r1",
                        "status": "verified",
                        "target_page": "ProjectMemberUploadDisp.jsp",
                        "target_page_name": "projectmemberuploaddisp.jsp",
                        "source_route": {"length": 3},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    catalog = RouteMapCatalog([route_map])
    route = catalog.find_for_target("ProjectMemberUploadDisp.jsp")

    assert route["route_id"] == "r1"
    assert route["route_map_path"] == str(route_map)


def test_page_matches_mapping_checks_frame_urls(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"))

    class Frame:
        def __init__(self, url):
            self.url = url

    class Page:
        url = "https://legacy.example/patlics/PatlicsTopMain.do"
        frames = [
            Frame("about:blank"),
            Frame("https://legacy.example/patlics/ProjectMemberUploadDisp.do"),
        ]

    assert engine._page_matches_mapping(
        Page(),
        {"page_id": "ProjectMemberUploadDisp.jsp", "entry_url": "ProjectMemberUploadDisp.do"},
    )


def test_render_report_contains_side_by_side_sections(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    screenshot = output_dir / "shot.png"
    Image.new("RGB", (1, 1), "white").save(screenshot)

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(output_dir))
    report = engine.render_report(
        [
            {
                "page_id": "A.jsp",
                "risk": "High",
                "action": "page_snapshot",
                "status": "PASS",
                "url_match": True,
                "dom_match": True,
                "legacy_screenshot": str(screenshot),
                "new_screenshot": str(screenshot),
                "diff_screenshot": str(screenshot),
                "visual": {"diff_percent": 0.0},
            }
        ]
    )

    html = Path(report).read_text(encoding="utf-8")
    assert "Legacy" in html
    assert "New" in html
    assert "Diff" in html
    assert "Checklist Coverage Matrix" in html
    assert "1-1 画面レイアウト" in html


def test_render_report_for_single_page_goes_next_to_screenshots(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    output_dir = tmp_path / "out"
    page_dir = output_dir / "0001_ProjectListUploadDisp.jsp"
    page_dir.mkdir(parents=True)
    screenshot = page_dir / "00_legacy_initial.png"
    Image.new("RGB", (1, 1), "white").save(screenshot)

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(output_dir))
    report = engine.render_report(
        [
            {
                "page_id": "ProjectListUploadDisp.jsp",
                "risk": "High",
                "action": "page_snapshot",
                "status": "PASS",
                "url_match": True,
                "legacy_screenshot": str(screenshot),
                "new_screenshot": str(screenshot),
                "diff_screenshot": str(screenshot),
                "visual": {"diff_percent": 0.0},
            }
        ]
    )

    assert Path(report).parent == page_dir
    html = Path(report).read_text(encoding="utf-8")
    assert "Page: ProjectListUploadDisp.jsp" in html
    assert 'src="00_legacy_initial.png"' in html


def test_build_steps_from_page_mapping_is_risk_first():
    steps = build_steps_from_page_mapping(
        {
            "page_mappings": [
                {"page_id": "m.jsp", "risk": "Medium", "locator_changes": []},
                {"page_id": "h.jsp", "risk": "High", "locator_changes": []},
                {"page_id": "l.jsp", "risk": "Low", "locator_changes": []},
            ]
        }
    )

    assert [step["page_id"] for step in steps] == ["h.jsp", "m.jsp"]


def test_infer_semantic_action_uses_scanner_hints():
    assert infer_semantic_action("click", {"kind": "form", "locator": "[name='SearchForm']"}) == "submit"
    assert infer_semantic_action(None, {"kind": "file", "action_hint": "upload"}) == "upload"
    assert infer_semantic_action("click", {"raw": '<form:select path="country">'}) == "select"
    assert infer_semantic_action(None, {"kind": "link", "raw": '<html:link href="/next">'}) == "navigate"


def test_action_dedupe_prefers_locator_change_over_full_action_fallback(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"))
    actions, skipped = engine._dedupe_actions(
        [
            {
                "kind": "button",
                "label": "save",
                "semantic_key": "action:save",
                "legacy_locator": "[name='save']",
                "new_locator": "#save",
            },
            {
                "kind": "button",
                "label": "save",
                "semantic_key": "action:save",
                "legacy_locator": "[name='save']",
                "new_locator": "[name='save']",
            },
            {
                "kind": "link",
                "label": "help",
                "semantic_key": "locator:a[href='/help.html']",
                "legacy_locator": "a[href='/help.html']",
                "new_locator": "a[href='/help.html']",
            },
        ]
    )

    assert len(actions) == 2
    assert len(skipped) == 1
    assert actions[0]["new_locator"] == "#save"


def test_popup_hint_detects_targeted_links():
    assert _opens_popup_hint({"attributes": {"target": "winHelp"}}) is True
    assert _opens_popup_hint({"attributes": {"target": "_self"}}) is False


def test_download_filename_is_windows_safe():
    name = _safe_download_filename('a[onclick="x"]?.xls', "chrome:port")

    assert name == "a_onclick_x_chrome_port.xls"


def test_report_coverage_matrix_classifies_upload_and_download(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    screenshot = output_dir / "shot.png"
    Image.new("RGB", (1, 1), "white").save(screenshot)

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(output_dir))
    report = engine.render_report(
        [
            {
                "page_id": "Upload.jsp",
                "risk": "High",
                "action": "uploadFile",
                "action_type": "upload",
                "status": "PASS",
                "url_match": True,
                "dom_match": True,
                "legacy_screenshot": str(screenshot),
                "new_screenshot": str(screenshot),
                "diff_screenshot": str(screenshot),
                "visual": {"diff_percent": 0.0},
            },
            {
                "page_id": "Download.jsp",
                "risk": "High",
                "action": "TemplateDownload",
                "action_type": "download",
                "status": "PASS",
                "url_match": True,
                "dom_match": True,
                "legacy_screenshot": str(screenshot),
                "new_screenshot": str(screenshot),
                "diff_screenshot": str(screenshot),
                "visual": {"diff_percent": 0.0},
            },
        ]
    )

    html = Path(report).read_text(encoding="utf-8")
    assert "6 ファイル出力" in html
    assert "7 ファイルアップロード" in html
    assert "AUTO" in html


def test_render_report_contains_semantic_diagnostics(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    screenshot = output_dir / "shot.png"
    Image.new("RGB", (1, 1), "white").save(screenshot)

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(output_dir))
    report = engine.render_report(
        [
            {
                "page_id": "FramePage.jsp",
                "risk": "High",
                "action": "submitForm",
                "action_type": "submit",
                "status": "BLOCKED",
                "url_match": False,
                "dom_match": False,
                "legacy_locator": "[name='LegacyForm']",
                "new_locator": "[name='NewForm']",
                "legacy_url": "https://legacy.example/app/FramePage.jsp",
                "new_url": "https://new.example/app/FramePage.jsp",
                "legacy_screenshot": str(screenshot),
                "new_screenshot": str(screenshot),
                "diff_screenshot": str(screenshot),
                "visual": {"diff_percent": 0.0},
                "legacy_frame": {"name": "frEditFrame", "url": "https://legacy.example/app/inner.do"},
                "new_action": {"reason": "Timeout waiting for locator", "selector_found": False},
            }
        ]
    )

    html = Path(report).read_text(encoding="utf-8")
    assert "Action type: submit" in html
    assert "frEditFrame" in html
    assert "new_locator" in html
    assert "Timeout waiting for locator" in html


def test_capture_state_handles_closed_page(tmp_path):
    class ClosedPage:
        def is_closed(self):
            return True

    state = _capture_state(ClosedPage(), tmp_path, "closed")

    assert state["page_closed"] is True
    assert state["url"] == "about:closed"
    assert Path(state["screenshot"]).exists()


def test_compare_state_treats_matching_closed_pages_as_pass(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    class ClosedPage:
        def is_closed(self):
            return True

    legacy_state = _capture_state(ClosedPage(), output_dir, "legacy_closed")
    new_state = _capture_state(ClosedPage(), output_dir, "new_closed")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(output_dir))

    result = engine._compare_state(
        "ClosePage.jsp",
        "High",
        "cancel",
        legacy_state,
        new_state,
        output_dir / "diff.png",
        legacy_action={"status": "PASS", "page_closed_after_action": True},
        new_action={"status": "PASS", "page_closed_after_action": True},
    )

    assert result["status"] == "PASS"
    assert result["url_match"] is True
    assert result["dom_match"] is True
