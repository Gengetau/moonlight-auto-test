import json
from pathlib import Path

import pytest
from PIL import Image

from src.action_executor import (
    _accept_dialog_safely,
    _attach_cdp_download_observer,
    _capture_state,
    _console_font,
    _configure_browser_download_dir,
    _dialog_action_from_context,
    _completed_cdp_download_file_from_dirs,
    _download_dir_snapshot,
    _download_expect_timeout_ms,
    _download_save_path,
    _download_watch_dirs,
    _frame_urls_changed,
    _handle_dialog_safely,
    _is_pdf_child_navigation_context,
    _negative_visual_evidence_payload,
    _normalize_transition_url,
    _opens_popup_hint,
    _pump_playwright_events,
    _record_download_result,
    _record_filesystem_download_result,
    _render_console_evidence_image,
    _resolve_upload_file_value,
    _safe_download_filename,
    _safe_opener_page,
    _should_close_capture_page,
    _should_close_opened_popup_page,
    _should_capture_browser_dialogs,
    _state_capture_page_after_child_navigation,
    _stable_download_from_dirs,
    build_steps_from_page_mapping,
    infer_semantic_action,
)
import src.regression_engine as regression_engine_module
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


def test_empty_url_fragment_does_not_count_as_navigation():
    base_url = "http://example.test/patlics/GazetteMenuFrame.do?method=initialDisplay"

    assert _normalize_transition_url(base_url) == _normalize_transition_url(f"{base_url}#")
    assert not _frame_urls_changed([base_url, "about:blank"], [f"{base_url}#", "about:blank"])
    assert _frame_urls_changed([base_url], [f"{base_url}#details"])


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


def test_select_pages_accepts_business_action_alias_for_actual_frame(tmp_path):
    mapping = {
        "page_mappings": [
            {"page_id": "GazetteMainFrame.jsp", "risk": "High"},
            {"page_id": "NonjavaScreeningMainFrame.jsp", "risk": "High"},
            {"page_id": "WwPersonAidMain.jsp", "risk": "High"},
        ]
    }
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"))

    gazette = engine.select_pages(target_page="JpGazetteForNumberSearch.do")
    screening = engine.select_pages(target_page="JpNonjavaScreeningForEasySearch.do?method=unRead")
    person_aid = engine.select_pages(target_page="WwPersonalNameDicDispForEasySearch.do")

    assert [page["page_id"] for page in gazette] == ["GazetteMainFrame.jsp"]
    assert [page["page_id"] for page in screening] == ["NonjavaScreeningMainFrame.jsp"]
    assert [page["page_id"] for page in person_aid] == ["WwPersonAidMain.jsp"]


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


def test_target_page_name_normalizes_case_and_action_suffix():
    assert RegressionEngine._target_page_name("/docroot/ProjectListUploadDisp.do") == "projectlistuploaddisp.jsp"
    assert RegressionEngine._target_page_name("projectlistuploaddisp.jsp") == "projectlistuploaddisp.jsp"


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


def test_route_map_catalog_accepts_business_action_alias(tmp_path):
    route_map = tmp_path / "usable_route_map.json"
    route_map.write_text(
        json.dumps(
            {
                "schema": "moonlight.usable_route_map.v1",
                "verified": [
                    {
                        "route_id": "gazette",
                        "status": "verified",
                        "target_page": "GazetteMainFrame.jsp",
                        "target_page_name": "gazettemainframe.jsp",
                        "source_route": {"length": 3},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    catalog = RouteMapCatalog([route_map])
    route = catalog.find_for_target("JpGazetteForNumberSearch.do")

    assert route["route_id"] == "gazette"


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


def test_page_matches_mapping_accepts_business_action_alias(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"))

    class Page:
        url = "https://legacy.example/patlics/JpGazetteForNumberSearch.do"
        frames = []

    assert engine._page_matches_mapping(Page(), {"page_id": "GazetteMainFrame.jsp"})


def test_passive_window_close_assertion_does_not_force_target_reopen():
    action_case = {
        "case_type": "assert_visible",
        "action_type": "assert_visible",
        "label": "Result panel confirm/cancel controls are visible",
        "pre_steps": [
            {"action_type": "assert_visible", "locator": "input[name='rbExpand'][value='+']"},
        ],
        "main_step": {
            "action_type": "assert_visible",
            "locator": "input[onclick*='window.close']",
        },
    }

    assert not RegressionEngine._requires_target_reopen_after_action(
        action_case,
        "assert_visible",
        "assert_visible",
        {"status": "PASS"},
        {"status": "PASS"},
    )


def test_takeover_recovery_prefers_open_target_popup_over_parent_route_step(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"))

    class Frame:
        def __init__(self, url):
            self.url = url

    class Context:
        def __init__(self):
            self.pages = []

    class Page:
        def __init__(self, url, context, frames=None):
            self.url = url
            self.context = context
            self.frames = frames or []
            self.front = False

        def is_closed(self):
            return False

        def bring_to_front(self):
            self.front = True

        def wait_for_load_state(self, *_args, **_kwargs):
            return None

    context = Context()
    parent = Page(
        "https://legacy.example/patlics/PatlicsTopMain.do",
        context,
        frames=[Frame("https://legacy.example/patlics/WwEasySearchMain.do")],
    )
    popup = Page("https://legacy.example/patlics/WwPersonalNameDicDispForEasySearch.do", context)
    context.pages = [parent, popup]

    selected, nav = engine._takeover_recovery_page(
        parent,
        {
            "target_page": "WwPersonAidMain.jsp",
            "source_route": {"entry_url": "PatlicsTopMain.do"},
        },
    )

    assert selected is popup
    assert popup.front
    assert nav["target_page_detected"] is True


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
    assert "Result JSON" in html
    assert (Path(report).parent / "regression_results.json").exists()
    assert "Checklist Cases" in html
    assert "No checklist cases were loaded for this report." in html


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
    assert infer_semantic_action("negative_http_500", {}) == "negative_http_500"
    assert infer_semantic_action("negative_js_error", {}) == "negative_js_error"
    assert infer_semantic_action("check", {"kind": "checkbox"}) == "check"
    assert infer_semantic_action("uncheck", {"kind": "checkbox"}) == "uncheck"
    assert infer_semantic_action("file_download", {"locator": 'input[name="btSave"][type="button"]'}) == "download"
    assert infer_semantic_action("download_template", {"kind": "link"}) == "download"
    assert infer_semantic_action("browser_dialog", {"expected_type": "browser_dialog"}) == "browser_dialog"
    assert infer_semantic_action("click", {"onclick": "window.print()", "expected_type": "print_invocation"}) == "print"
    assert infer_semantic_action("child_navigation", {}) == "click"
    assert infer_semantic_action("open_child_page", {}) == "click"
    assert infer_semantic_action("assert_visible", {}) == "assert_visible"
    assert infer_semantic_action("assert_attached", {}) == "assert_attached"
    assert infer_semantic_action("expect_text", {}) == "assert_text"
    assert infer_semantic_action("expect_value", {}) == "assert_value"
    assert infer_semantic_action("assert_url", {}) == "assert_url"
    assert infer_semantic_action("fill", {"label": "Selected result can be confirmed"}) == "fill"
    assert infer_semantic_action("fill", {"expected_type": "browser_dialog"}) == "fill"
    assert infer_semantic_action("select", {"expected_type": "browser_dialog"}) == "select"
    assert (
        infer_semantic_action(
            "manual_assert",
            {"locator": "select[name='drawingOut']", "value": "Representative drawing output is disabled."},
        )
        == "assert_disabled"
    )
    assert (
        infer_semantic_action(
            "manual_assert",
            {"locator": "browser dialog", "value": "The native dialog is shown."},
        )
        == "manual_assert"
    )


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
    assert _opens_popup_hint({"action_type": "child_navigation"}) is True
    assert _opens_popup_hint({"action_type": "open_child_page", "opens_popup": True}) is True
    assert _opens_popup_hint({"recorded_onclick": "submitForm('WwHistoryForm','./WwExpPrint.do','winPrintView')"}) is True
    assert _opens_popup_hint({"onclick": "submitForm('WwEasySearchForm','./WwEasySearch.do','frHistoryFrame')"}) is False


def test_child_navigation_compare_is_url_only_and_accepts_page_aliases(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"))

    result = engine._compare_state(
        "WwBiblioList.jsp",
        "High",
        "open detail",
        {"url": "http://legacy.test/patlics/WwClassCodeDetail.do", "screenshot": str(tmp_path / "legacy.png")},
        {"url": "http://new.test/patlics/WwClassCodeDetail.jsp", "screenshot": str(tmp_path / "new.png")},
        tmp_path / "diff.png",
        action_type="child_navigation",
        legacy_action={"status": "PASS"},
        new_action={"status": "PASS"},
    )

    assert result["status"] == "PASS"
    assert result["visual"]["status"] == "SKIPPED"
    assert result["url_match"] is True
    assert result["child_navigation_match"] is True


def test_child_navigation_requires_target_reopen_only_for_same_window_navigation():
    action_case = {
        "case_type": "child_navigation",
        "action_type": "child_navigation",
        "main_step": {"action_type": "child_navigation"},
    }

    assert RegressionEngine._requires_target_reopen_after_action(
        action_case,
        "child_navigation",
        "click",
        {"status": "PASS", "frame_changed": True, "popup_detected": False},
        {"status": "PASS", "frame_changed": True, "popup_detected": False},
    )
    assert not RegressionEngine._requires_target_reopen_after_action(
        action_case,
        "child_navigation",
        "click",
        {"status": "PASS", "frame_changed": True, "popup_detected": True},
        {"status": "PASS", "frame_changed": True, "popup_detected": True},
    )


def test_action_left_target_page_requires_reopen_for_same_window_navigation(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"))

    class Frame:
        def __init__(self, url):
            self.url = url

    class Page:
        url = "http://legacy.test/patlics/PatlicsTopMain.do"
        frames = [Frame("http://legacy.test/patlics/WwClassCodeDetail.do")]

        def is_closed(self):
            return False

    state = engine._action_left_target_page(
        {"page_id": "WwBiblioList.jsp"},
        Page(),
        Page(),
        {"status": "PASS", "frame_changed": True, "popup_detected": False},
        {"status": "PASS", "frame_changed": True, "popup_detected": False},
    )

    assert state["requires_reopen"] is True
    assert state["legacy_matches_target"] is False
    assert state["new_matches_target"] is False


def test_action_left_target_page_ignores_popup_when_parent_stays_on_target(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"))

    class Frame:
        def __init__(self, url):
            self.url = url

    class Page:
        url = "http://legacy.test/patlics/PatlicsTopMain.do"
        frames = [Frame("http://legacy.test/patlics/WwBiblioList.do")]

        def is_closed(self):
            return False

    state = engine._action_left_target_page(
        {"page_id": "WwBiblioList.jsp"},
        Page(),
        Page(),
        {"status": "PASS", "navigation_detected": True, "popup_detected": True},
        {"status": "PASS", "navigation_detected": True, "popup_detected": True},
    )

    assert state["requires_reopen"] is False


def test_action_left_target_page_ignores_popup_when_alias_mapping_misses_preserved_parent(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"))

    class Page:
        def __init__(self, url):
            self.url = url
            self.frames = []

        def is_closed(self):
            return False

    legacy_url = "http://legacy.test/patlics/GazetteForBiblioList.do"
    new_url = "http://new.test/patlics/GazetteForBiblioList.do"
    state = engine._action_left_target_page(
        {"page_id": "GazetteMainFrame.jsp"},
        Page(legacy_url),
        Page(new_url),
        {
            "status": "PASS",
            "before_url": legacy_url,
            "after_url": "http://legacy.test/patlics/EvalListForJpGazetteHTML.do",
            "navigation_detected": True,
            "frame_changed": True,
            "popup_detected": True,
        },
        {
            "status": "PASS",
            "before_url": new_url,
            "after_url": "http://new.test/patlics/EvalListForJpGazetteHTML.do",
            "navigation_detected": True,
            "frame_changed": True,
            "popup_detected": True,
        },
    )

    assert state["requires_reopen"] is False
    assert state["popup_parent_preserved"] is True


def test_download_filename_is_windows_safe():
    name = _safe_download_filename('a[onclick="x"]?.xls', "chrome:port")

    assert name == "a_onclick_x_chrome_port.xls"


def test_download_save_path_uses_env_and_preserves_suggested_filename(tmp_path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path))

    path = _download_save_path("プロジェクトリストアップロード.tsv")

    assert path == tmp_path / "プロジェクトリストアップロード.tsv"


def test_download_save_path_does_not_overwrite_existing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path))
    existing = tmp_path / "report.pdf"
    existing.write_text("legacy", encoding="utf-8")

    path = _download_save_path("report.pdf")

    assert path == tmp_path / "report (1).pdf"


def test_download_watch_dir_detects_new_stable_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path))
    watch_dirs = _download_watch_dirs({})
    started_at = 1.0
    before = _download_dir_snapshot(watch_dirs)
    created = tmp_path / "report.tsv"
    created.write_bytes(b"downloaded")
    stable_seen = {}

    assert _stable_download_from_dirs(
        watch_dirs,
        before,
        stable_seen,
        started_at=started_at,
        stability_ms=0,
    ) is None
    detected = _stable_download_from_dirs(
        watch_dirs,
        before,
        stable_seen,
        started_at=started_at,
        stability_ms=0,
    )

    assert detected == created


def test_download_watch_dir_ignores_browser_uuid_temp_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path))
    watch_dirs = _download_watch_dirs({})
    started_at = 1.0
    before = _download_dir_snapshot(watch_dirs)
    temp_file = tmp_path / "0ecdece6-6a98-4958-9ad0-bef1b668db12"
    temp_file.write_bytes(b"partial")
    stable_seen = {}

    assert _stable_download_from_dirs(
        watch_dirs,
        before,
        stable_seen,
        started_at=started_at,
        stability_ms=0,
    ) is None
    assert _stable_download_from_dirs(
        watch_dirs,
        before,
        stable_seen,
        started_at=started_at,
        stability_ms=0,
    ) is None


def test_download_watch_dir_allows_browser_uuid_fallback_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path))
    watch_dirs = _download_watch_dirs({})
    started_at = 1.0
    before = _download_dir_snapshot(watch_dirs)
    guid = "0ecdece6-6a98-4958-9ad0-bef1b668db12"
    completed_file = tmp_path / guid
    completed_file.write_bytes(b"zip")
    stable_seen = {}

    assert _stable_download_from_dirs(
        watch_dirs,
        before,
        stable_seen,
        started_at=started_at,
        stability_ms=0,
        allow_browser_uuid_names=True,
    ) is None
    detected = _stable_download_from_dirs(
        watch_dirs,
        before,
        stable_seen,
        started_at=started_at,
        stability_ms=0,
        allow_browser_uuid_names=True,
    )

    assert detected == completed_file


def test_download_watch_dir_allows_completed_cdp_uuid_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path))
    watch_dirs = _download_watch_dirs({})
    started_at = 1.0
    before = _download_dir_snapshot(watch_dirs)
    guid = "0ecdece6-6a98-4958-9ad0-bef1b668db12"
    completed_file = tmp_path / guid
    completed_file.write_bytes(b"zip")
    stable_seen = {}

    assert _stable_download_from_dirs(
        watch_dirs,
        before,
        stable_seen,
        started_at=started_at,
        stability_ms=0,
        allow_temp_names=[guid],
    ) is None
    detected = _stable_download_from_dirs(
        watch_dirs,
        before,
        stable_seen,
        started_at=started_at,
        stability_ms=0,
        allow_temp_names=[guid],
    )

    assert detected == completed_file


def test_completed_cdp_download_file_detects_guid_without_waiting_for_stability(tmp_path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path))
    watch_dirs = _download_watch_dirs({})
    started_at = 1.0
    before = _download_dir_snapshot(watch_dirs)
    guid = "0ecdece6-6a98-4958-9ad0-bef1b668db12"
    completed_file = tmp_path / guid
    completed_file.write_bytes(b"zip")

    detected, metadata = _completed_cdp_download_file_from_dirs(
        {
            guid: {
                "guid": guid,
                "state": "completed",
                "suggested_filename": "patent_PDF_20260608161131.zip",
                "completed_at": 2.0,
            }
        },
        watch_dirs,
        before,
        started_at=started_at,
    )

    assert detected == completed_file
    assert metadata["suggested_filename"] == "patent_PDF_20260608161131.zip"


def test_completed_cdp_download_file_ignores_preexisting_guid_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path))
    guid = "0ecdece6-6a98-4958-9ad0-bef1b668db12"
    (tmp_path / guid).write_bytes(b"old")
    watch_dirs = _download_watch_dirs({})
    before = _download_dir_snapshot(watch_dirs)

    detected, metadata = _completed_cdp_download_file_from_dirs(
        {guid: {"guid": guid, "state": "completed", "suggested_filename": "report.zip"}},
        watch_dirs,
        before,
        started_at=1.0,
    )

    assert detected is None
    assert metadata is None


def test_download_expect_timeout_keeps_filesystem_monitor_responsive():
    assert _download_expect_timeout_ms({}, action_timeout_ms=150000, download_timeout_ms=150000) == 10000
    assert _download_expect_timeout_ms(
        {"download_expect_timeout_ms": 3000},
        action_timeout_ms=150000,
        download_timeout_ms=150000,
    ) == 3000


def test_pump_playwright_events_uses_live_context_page_when_action_page_closed():
    class Context:
        pages = []

    class Page:
        def __init__(self, *, closed=False):
            self._closed = closed
            self.context = Context()
            self.waited = []

        def is_closed(self):
            return self._closed

        def wait_for_timeout(self, timeout_ms):
            self.waited.append(timeout_ms)

    closed_page = Page(closed=True)
    live_page = Page()
    context = Context()
    context.pages = [closed_page, live_page]
    closed_page.context = context
    live_page.context = context

    assert _pump_playwright_events(250, closed_page) is True
    assert live_page.waited == [250]


def test_configure_browser_download_dir_uses_cdp_browser_behavior(tmp_path):
    calls = []

    class Session:
        def send(self, method, payload):
            calls.append((method, payload))

        def detach(self):
            calls.append(("detach", {}))

    class Context:
        def __init__(self):
            self.pages = []

        def new_cdp_session(self, page):
            return Session()

    class Page:
        def __init__(self):
            self.context = Context()
            self.context.pages = [self]
            self.url = "http://example.test/download"

        def is_closed(self):
            return False

    state = _configure_browser_download_dir(Page(), tmp_path)

    assert state["status"] == "PASS"
    assert calls[0] == (
        "Browser.setDownloadBehavior",
        {
            "behavior": "allowAndName",
            "downloadPath": str(tmp_path),
            "eventsEnabled": True,
        },
    )


def test_attach_cdp_download_observer_registers_download_events(tmp_path):
    calls = []
    event_handlers = {}

    class Session:
        def send(self, method, payload):
            calls.append((method, payload))

        def on(self, event_name, handler):
            event_handlers[event_name] = handler

        def detach(self):
            calls.append(("detach", {}))

    class Context:
        def __init__(self):
            self.pages = []

        def new_cdp_session(self, page):
            return Session()

    class Page:
        def __init__(self):
            self.context = Context()
            self.context.pages = [self]
            self.url = "http://example.test/download"

        def is_closed(self):
            return False

    state, sessions = _attach_cdp_download_observer(
        Page(),
        tmp_path,
        on_will_begin=lambda params: None,
        on_progress=lambda params: None,
    )

    assert state["status"] == "PASS"
    assert len(sessions) == 1
    assert calls[0] == (
        "Browser.setDownloadBehavior",
        {
            "behavior": "allowAndName",
            "downloadPath": str(tmp_path),
            "eventsEnabled": True,
        },
    )
    assert "Browser.downloadWillBegin" in event_handlers
    assert "Browser.downloadProgress" in event_handlers


def test_record_filesystem_download_result_uses_existing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path))
    downloaded = tmp_path / "report.tsv"
    downloaded.write_bytes(b"downloaded")
    result = {}

    _record_filesystem_download_result(result, downloaded)

    assert result["download_source"] == "filesystem"
    assert result["download_filename"] == "report.tsv"
    assert result["download_path"] == str(downloaded)
    assert result["download_saved_path"] == str(downloaded)
    assert result["download_renamed"] is False
    assert result["download_size"] == len(b"downloaded")


def test_record_download_result_uses_playwright_suggested_filename(tmp_path, monkeypatch):
    download_dir = tmp_path / "downloads"
    monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))

    class Download:
        suggested_filename = "patent_PDF_20260605181936.zip"

        def save_as(self, path):
            Path(path).write_bytes(b"zip")

    result = {}

    _record_download_result(result, Download())

    saved_path = download_dir / "patent_PDF_20260605181936.zip"
    assert saved_path.exists()
    assert result["download_source"] == "playwright_event"
    assert result["download_filename"] == "patent_PDF_20260605181936.zip"
    assert result["saved_filename"] == "patent_PDF_20260605181936.zip"
    assert result["download_path"] == str(saved_path)


def test_record_download_result_uses_response_hint_when_playwright_name_is_generic(tmp_path, monkeypatch):
    download_dir = tmp_path / "downloads"
    monkeypatch.setenv("DOWNLOAD_DIR", str(download_dir))

    class Download:
        suggested_filename = "download"

        def save_as(self, path):
            Path(path).write_bytes(b"zip")

    result = {}

    _record_download_result(result, Download(), suggested_filename="patent_PDF_20260605181936.zip")

    saved_path = download_dir / "patent_PDF_20260605181936.zip"
    assert saved_path.exists()
    assert result["download_playwright_suggested_filename"] == "download"
    assert result["download_suggested_filename"] == "patent_PDF_20260605181936.zip"
    assert result["download_path"] == str(saved_path)


def test_record_filesystem_download_result_persists_temp_uuid_file_with_original_name(tmp_path, monkeypatch):
    download_dir = tmp_path / "browser_downloads"
    stable_dir = tmp_path / "stable_downloads"
    download_dir.mkdir()
    stable_dir.mkdir()
    monkeypatch.setenv("DOWNLOAD_DIR", str(stable_dir))
    downloaded = download_dir / "17e060f2-fec6-425c-9f98-3a66b2493823"
    downloaded.write_bytes(b"downloaded")
    report_dir = tmp_path / "report"
    result = {}

    _record_filesystem_download_result(
        result,
        downloaded,
        capture_dir=report_dir,
        test_id="20_case_legacy",
        browser_name="edge",
        suggested_filename="report.tsv",
    )

    saved_path = stable_dir / "report.tsv"
    archive_path = Path(result["download_archive_path"])
    assert saved_path.exists()
    assert saved_path.read_bytes() == b"downloaded"
    assert archive_path.parent == report_dir / "downloads"
    assert archive_path.exists()
    assert archive_path.read_bytes() == b"downloaded"
    assert result["download_original_path"] == str(downloaded)
    assert result["download_detected_filename"] == downloaded.name
    assert result["download_filename"] == "report.tsv"
    assert result["download_path"] == str(saved_path)
    assert result["download_saved_path"] == str(saved_path)
    assert result["download_archive_path"] == str(archive_path)
    assert result["download_size"] == len(b"downloaded")
    assert result["download_sha256"]


def test_record_filesystem_download_result_does_not_save_temp_uuid_as_test_id(tmp_path, monkeypatch):
    download_dir = tmp_path / "browser_downloads"
    stable_dir = tmp_path / "stable_downloads"
    report_dir = tmp_path / "report"
    download_dir.mkdir()
    stable_dir.mkdir()
    monkeypatch.setenv("DOWNLOAD_DIR", str(stable_dir))
    downloaded = download_dir / "17e060f2-fec6-425c-9f98-3a66b2493823"
    downloaded.write_bytes(b"downloaded")
    result = {}

    _record_filesystem_download_result(
        result,
        downloaded,
        capture_dir=report_dir,
        test_id="09_Default_public_and_registered_gazette_kinds_download_a_ZIP_legacy_step7",
        browser_name="edge",
    )

    assert result["download_filename_unknown"] is True
    assert result["download_filename"] == ""
    assert result["download_saved_path"] == ""
    assert not list(stable_dir.iterdir())
    assert Path(result["download_archive_path"]).exists()
    assert Path(result["download_path"]).parent == report_dir / "downloads"


def test_record_filesystem_download_result_does_not_reuse_generated_step_name(tmp_path, monkeypatch):
    stable_dir = tmp_path / "stable_downloads"
    report_dir = tmp_path / "report"
    stable_dir.mkdir()
    monkeypatch.setenv("DOWNLOAD_DIR", str(stable_dir))
    generated_name = "09_Default_public_and_registered_gazette_kinds_download_a_ZIP_legacy_step7"
    downloaded = stable_dir / generated_name
    downloaded.write_bytes(b"downloaded")
    result = {}

    _record_filesystem_download_result(
        result,
        downloaded,
        capture_dir=report_dir,
        test_id=generated_name,
        browser_name="edge",
    )

    assert result["download_filename_unknown"] is True
    assert result["download_filename"] == ""
    assert result["download_saved_path"] == ""
    assert Path(result["download_archive_path"]).exists()


def test_record_filesystem_download_result_uses_response_suggested_filename(tmp_path, monkeypatch):
    download_dir = tmp_path / "browser_downloads"
    stable_dir = tmp_path / "stable_downloads"
    download_dir.mkdir()
    stable_dir.mkdir()
    monkeypatch.setenv("DOWNLOAD_DIR", str(stable_dir))
    downloaded = download_dir / "17e060f2-fec6-425c-9f98-3a66b2493823"
    downloaded.write_bytes(b"downloaded")
    result = {}

    _record_filesystem_download_result(
        result,
        downloaded,
        test_id="20_case_legacy",
        browser_name="edge",
        suggested_filename="server_report.tsv",
    )

    assert result["download_filename"] == "server_report.tsv"
    assert result["download_path"] == str(stable_dir / "server_report.tsv")
    assert not (stable_dir / "20_case_legacy").exists()


def test_record_filesystem_download_result_renames_uuid_inside_download_dir(tmp_path, monkeypatch):
    stable_dir = tmp_path / "stable_downloads"
    stable_dir.mkdir()
    monkeypatch.setenv("DOWNLOAD_DIR", str(stable_dir))
    guid = "17e060f2-fec6-425c-9f98-3a66b2493823"
    downloaded = stable_dir / guid
    downloaded.write_bytes(b"downloaded")
    result = {}

    _record_filesystem_download_result(
        result,
        downloaded,
        test_id="20_case_legacy",
        browser_name="edge",
        suggested_filename="server_report.tsv",
    )

    assert result["download_filename"] == "server_report.tsv"
    assert result["download_path"] == str(stable_dir / "server_report.tsv")
    assert (stable_dir / "server_report.tsv").read_bytes() == b"downloaded"
    assert not downloaded.exists()


def test_record_filesystem_download_result_adds_suffix_for_existing_original_name(tmp_path, monkeypatch):
    download_dir = tmp_path / "browser_downloads"
    stable_dir = tmp_path / "stable_downloads"
    download_dir.mkdir()
    stable_dir.mkdir()
    monkeypatch.setenv("DOWNLOAD_DIR", str(stable_dir))
    (stable_dir / "report.tsv").write_bytes(b"old")
    downloaded = download_dir / "17e060f2-fec6-425c-9f98-3a66b2493823"
    downloaded.write_bytes(b"new")
    result = {}

    _record_filesystem_download_result(
        result,
        downloaded,
        test_id="20_case_legacy",
        browser_name="edge",
        suggested_filename="report.tsv",
    )

    assert result["download_filename"] == "report.tsv"
    assert result["saved_filename"] == "report (1).tsv"
    assert result["download_path"] == str(stable_dir / "report (1).tsv")
    assert (stable_dir / "report.tsv").read_bytes() == b"old"
    assert (stable_dir / "report (1).tsv").read_bytes() == b"new"


def test_record_filesystem_download_result_reuses_named_file_already_in_download_dir(tmp_path, monkeypatch):
    stable_dir = tmp_path / "stable_downloads"
    stable_dir.mkdir()
    monkeypatch.setenv("DOWNLOAD_DIR", str(stable_dir))
    downloaded = stable_dir / "report (1).tsv"
    downloaded.write_bytes(b"downloaded")
    result = {}

    _record_filesystem_download_result(
        result,
        downloaded,
        test_id="20_case_legacy",
        browser_name="edge",
        suggested_filename="report.tsv",
    )

    assert result["download_filename"] == "report.tsv"
    assert result["saved_filename"] == "report (1).tsv"
    assert result["download_path"] == str(downloaded)
    assert list(stable_dir.iterdir()) == [downloaded]


def test_prepare_page_output_dir_clears_previous_artifacts(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    output_dir = tmp_path / "out"
    page_dir = output_dir / "0001_WwSample.jsp"
    downloads_dir = page_dir / "downloads"
    route_dir = page_dir / "legacy_route"
    downloads_dir.mkdir(parents=True)
    route_dir.mkdir()
    (page_dir / "00_legacy_initial.png").write_bytes(b"old screenshot")
    (downloads_dir / "old.tsv").write_bytes(b"old download")
    (route_dir / "old.png").write_bytes(b"old route screenshot")

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(output_dir))
    summary = engine._prepare_page_output_dir(page_dir)

    assert summary["status"] == "PASS"
    assert summary["removed_files"] == 1
    assert summary["removed_dirs"] == 2
    assert page_dir.exists()
    assert list(page_dir.iterdir()) == []


def test_console_evidence_image_renders_events(tmp_path):
    image_path = _render_console_evidence_image(
        [
            {"time": "12:00:01", "type": "CONSOLE", "level": "error", "detail": "error: boom", "url": "http://example/js"},
            {"time": "12:00:02", "type": "HTTP_ERROR", "level": "http_error", "detail": "HTTP 500", "status": 500, "url": "http://example/api"},
        ],
        tmp_path,
        "case_001",
    )

    assert Path(image_path).exists()
    with Image.open(image_path) as image:
        assert image.width >= 1000
        assert image.height >= 200


def test_console_font_prefers_japanese_font_for_non_ascii_text():
    expected = Path(r"C:\Windows\Fonts\NotoSansJP-VF.ttf")
    if not expected.exists():
        pytest.skip("Windows Japanese font is not installed")

    font = _console_font(15, "項目を選択してください")

    assert Path(font.path).name == expected.name


def test_safe_opener_page_returns_open_parent():
    class Page:
        def __init__(self, *, closed=False, opener=None):
            self._closed = closed
            self._opener = opener

        def is_closed(self):
            return self._closed

        def opener(self):
            return self._opener

    parent = Page()

    assert _safe_opener_page(Page(opener=parent)) is parent
    assert _safe_opener_page(Page(opener=Page(closed=True))) is None


def test_opener_capture_page_is_not_closed_after_popup_reflection():
    popup = object()
    opener = object()
    opened_child = object()

    assert _should_close_capture_page(opener, popup, opener, keep_popup=False) is False
    assert _should_close_capture_page(opened_child, popup, opener, keep_popup=False) is True
    assert _should_close_capture_page(opened_child, popup, opener, keep_popup=True) is False


def test_opened_pdf_popup_closes_even_when_state_capture_uses_parent():
    class Page:
        def __init__(self, *, closed=False):
            self._closed = closed

        def is_closed(self):
            return self._closed

    action_page = Page()
    opener_page = Page()
    pdf_popup = Page()

    selected, scope = _state_capture_page_after_child_navigation(
        pdf_popup,
        action_page,
        opener_page,
        {"capture_opener_after_child": True},
    )

    assert selected is action_page
    assert scope == "action_page_after_child_navigation"
    assert _should_close_opened_popup_page(
        pdf_popup,
        action_page,
        opener_page,
        keep_popup=False,
        close_after=True,
    )
    assert not _should_close_opened_popup_page(
        pdf_popup,
        action_page,
        opener_page,
        keep_popup=True,
        close_after=True,
    )
    assert not _should_close_opened_popup_page(
        pdf_popup,
        action_page,
        opener_page,
        keep_popup=False,
        close_after=False,
    )


def test_child_navigation_state_capture_prefers_action_page_when_requested():
    class Page:
        def __init__(self, *, closed=False):
            self._closed = closed

        def is_closed(self):
            return self._closed

    action_page = Page()
    child_page = Page()
    opener_page = Page()

    selected, scope = _state_capture_page_after_child_navigation(
        child_page,
        action_page,
        opener_page,
        {"capture_opener_after_child": True},
    )

    assert selected is action_page
    assert scope == "action_page_after_child_navigation"


def test_child_navigation_state_capture_falls_back_to_opener_when_action_page_closed():
    class Page:
        def __init__(self, *, closed=False):
            self._closed = closed

        def is_closed(self):
            return self._closed

    action_page = Page(closed=True)
    child_page = Page()
    opener_page = Page()

    selected, scope = _state_capture_page_after_child_navigation(
        child_page,
        action_page,
        opener_page,
        {"capture_opener_after_child": True},
    )

    assert selected is opener_page
    assert scope == "opener_after_child_navigation"


def test_pdf_child_navigation_uses_action_page_for_state_capture_automatically():
    class Page:
        def __init__(self, *, closed=False):
            self._closed = closed

        def is_closed(self):
            return self._closed

    action_page = Page()
    child_page = Page()

    selected, scope = _state_capture_page_after_child_navigation(
        child_page,
        action_page,
        None,
        {
            "case_type": "child_navigation",
            "expected_url": "GazetteContentFrame.do?method=viewPDF",
        },
    )

    assert selected is action_page
    assert scope == "action_page_after_pdf_child_navigation"


def test_pdf_child_navigation_detection_does_not_match_regular_child_page():
    assert _is_pdf_child_navigation_context(
        {
            "case_type": "child_navigation",
            "expected_url": "GazetteContentFrame.do?method=viewPDF",
        }
    )
    assert not _is_pdf_child_navigation_context(
        {
            "case_type": "child_navigation",
            "expected_url": "EvalListForJpGazetteHTML.do",
        }
    )


def test_negative_visual_evidence_payload_names_visible_error_state():
    payload = _negative_visual_evidence_payload(
        "negative_http_500",
        detail="UploadConf failed",
        url="**/UploadConf.do*",
        phase="after trigger",
    )

    assert payload["title"] == "Simulated HTTP 500"
    assert payload["phase"] == "after trigger"
    assert payload["detail"] == "UploadConf failed"
    assert payload["url"] == "**/UploadConf.do*"


def test_accept_dialog_safely_ignores_already_handled_dialog():
    class Dialog:
        def accept(self):
            raise RuntimeError("Dialog.accept: Cannot accept dialog which is already handled!")

    assert _accept_dialog_safely(Dialog()) == "already_handled"


def test_accept_dialog_safely_reports_accept_success():
    class Dialog:
        accepted = False

        def accept(self):
            self.accepted = True

    dialog = Dialog()

    assert _accept_dialog_safely(dialog) == "accepted"
    assert dialog.accepted is True


def test_dialog_action_from_context_supports_cancel_buttons():
    assert _dialog_action_from_context({"dialog_action": "accept"}) == "accept"
    assert _dialog_action_from_context({"dialog_button": "OK"}) == "accept"
    assert _dialog_action_from_context({"dialog_button": "キャンセル"}) == "dismiss"
    assert _dialog_action_from_context({"confirm_action": "cancel"}) == "dismiss"


def test_handle_dialog_safely_can_dismiss_confirm():
    class Dialog:
        accepted = False
        dismissed = False

        def accept(self):
            self.accepted = True

        def dismiss(self):
            self.dismissed = True

    dialog = Dialog()

    assert _handle_dialog_safely(dialog, "dismiss") == "dismissed"
    assert dialog.dismissed is True
    assert dialog.accepted is False


def test_manual_replay_context_always_captures_browser_dialogs():
    assert _should_capture_browser_dialogs({"manual_replay": True, "action_type": "click"}, "click") is True


def test_upload_file_resolver_handles_placeholders_and_multiple_files(tmp_path):
    explicit = tmp_path / "valid.tsv"
    explicit.write_text("id\tname\n1\tmoonlight\n", encoding="utf-8")
    other = tmp_path / "other.tsv"
    other.write_text("id\tname\n2\tluna\n", encoding="utf-8")

    assert _resolve_upload_file_value(str(explicit), tmp_path) == str(explicit)
    assert _resolve_upload_file_value([str(explicit), str(other)], tmp_path) == [str(explicit), str(other)]

    placeholder = _resolve_upload_file_value("${UPLOAD_FILE}", tmp_path)
    assert Path(placeholder).exists()

    fakepath = _resolve_upload_file_value(r"C:\fakepath\missing.tsv", tmp_path)
    assert Path(fakepath).exists()


def test_upload_profile_resolver_prefers_checklist_then_profile_then_global(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    checklist_file = tmp_path / "checklist.tsv"
    profile_file = tmp_path / "profile.tsv"
    case_profile_file = tmp_path / "case_profile.tsv"
    fallback_file = tmp_path / "fallback.tsv"
    for path in (checklist_file, profile_file, case_profile_file, fallback_file):
        path.write_text("id\tname\n1\tmoonlight\n", encoding="utf-8")
    profile_config = tmp_path / "upload_profiles.json"
    profile_config.write_text(
        json.dumps(
            {
                "upload_profiles": [
                    {
                        "name": "valid_project_list_tsv",
                        "file": str(profile_file),
                        "page_patterns": ["ProjectListUploadDisp.jsp"],
                        "case_types": ["upload_submit"],
                        "locator": "input[name='uploadFile']",
                        "negative": False,
                    },
                    {
                        "name": "case_specific_upload",
                        "case_id": "project-upload-valid",
                        "file": str(case_profile_file),
                        "page_patterns": ["ProjectListUploadDisp.jsp"],
                        "case_types": ["upload_submit"],
                        "locator": "input[name='uploadFile']",
                        "negative": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    engine = RegressionEngine(
        mapping_path=str(mapping_path),
        output_dir=str(tmp_path / "out"),
        upload_file=str(fallback_file),
        upload_profile_config=str(profile_config),
    )
    action_case = {"page_id": "ProjectListUploadDisp.jsp", "case_id": "project-upload-valid", "case_type": "upload_submit", "action_type": "upload_submit"}
    step = {"action_type": "upload", "locator": "input[name='uploadFile']"}

    assert engine._resolve_upload_value(str(checklist_file), action_case, step, "input[name='uploadFile']") == str(checklist_file)
    assert engine._resolve_upload_value(r"C:\fakepath\bad.tsv", action_case, step, "input[name='uploadFile']") == str(case_profile_file)
    assert engine._resolve_upload_value(r"C:\fakepath\bad.tsv", {**action_case, "case_id": "other-upload"}, step, "input[name='uploadFile']") == str(profile_file)
    assert engine._resolve_upload_value("${UPLOAD_FILE}", {**action_case, "page_id": "Other.jsp"}, step, "input[name='uploadFile']") == str(fallback_file)


def test_upload_submit_button_step_clicks_onclick_instead_of_request_submit(tmp_path, monkeypatch):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    upload_file = tmp_path / "upload.tsv"
    upload_file.write_text("id\tname\n1\tmoonlight\n", encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"), upload_file=str(upload_file))
    calls = []

    def fake_execute_action(page, action_type, locator, value=None, **kwargs):
        context = kwargs.get("action_context") or {}
        calls.append(
            {
                "action_type": action_type,
                "locator": locator,
                "semantic_action": infer_semantic_action(action_type, context),
            }
        )
        return {"status": "PASS", "state": {"url": "http://example.test/after", "screenshot": str(tmp_path / "after.png")}}

    monkeypatch.setattr(regression_engine_module, "execute_action", fake_execute_action)
    action_case = {
        "case_id": "upload-1",
        "case_type": "upload_submit",
        "action_type": "upload_submit",
        "page_id": "Upload.jsp",
        "pre_steps": [{"action_type": "upload", "locator": "input[name='uploadFile']", "value": "${UPLOAD_FILE}"}],
        "main_step": {
            "action_type": "submit",
            "locator": "input[onclick*=\"submitForm('UploadForm','./UploadConfirm.do','')\"]",
        },
    }

    result = engine._execute_action_case(
        object(),
        action_case,
        side="legacy",
        browser_name="chrome_port",
        capture_dir=tmp_path,
        test_id="upload_case",
    )

    assert result["status"] == "PASS"
    assert calls[0]["semantic_action"] == "upload"
    assert calls[1]["action_type"] == "click"
    assert calls[1]["semantic_action"] == "click"


def test_scenario_step_semantics_do_not_inherit_parent_kind(tmp_path, monkeypatch):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"))
    calls = []

    def fake_execute_action(page, action_type, locator, value=None, **kwargs):
        context = kwargs.get("action_context") or {}
        calls.append(
            {
                "action_type": action_type,
                "locator": locator,
                "value": value,
                "semantic_action": infer_semantic_action(action_type, context),
            }
        )
        return {"status": "PASS", "state": {"url": "http://example.test/after", "screenshot": str(tmp_path / "after.png")}}

    monkeypatch.setattr(regression_engine_module, "execute_action", fake_execute_action)
    action_case = {
        "case_id": "person-aid-middle",
        "case_type": "assert_text",
        "action_type": "assert_text",
        "kind": "assert_text",
        "label": "Middle-match keyword search refreshes the result frame",
        "expected_type": "result_list",
        "expected_value": "11 HEALTH",
        "pre_steps": [
            {"action_type": "fill", "locator": "input[name='keyword'][type='text']", "value": "11"},
            {"action_type": "check", "locator": "input[name='searchType'][value='3']"},
            {"action_type": "click", "locator": "form[name='WwPersonalNameDicSearchForm'] input[type='submit']"},
        ],
        "main_step": {"action_type": "assert_text", "locator": "#tableBody", "value": "11 HEALTH"},
    }

    result = engine._execute_action_case(
        object(),
        action_case,
        side="legacy",
        browser_name="edge",
        capture_dir=tmp_path,
        test_id="person_aid_middle",
    )

    assert result["status"] == "PASS"
    assert calls[0]["semantic_action"] == "fill"
    assert calls[0]["value"] == "11"
    assert calls[1]["semantic_action"] == "check"
    assert calls[-1]["semantic_action"] == "assert_text"


def test_scenario_pre_steps_do_not_inherit_parent_navigation_expectation(tmp_path, monkeypatch):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"))
    contexts = []

    def fake_execute_action(page, action_type, locator, value=None, **kwargs):
        contexts.append(kwargs.get("action_context") or {})
        return {"status": "PASS", "state": {"url": "http://example.test/after", "screenshot": str(tmp_path / "after.png")}}

    monkeypatch.setattr(regression_engine_module, "execute_action", fake_execute_action)
    action_case = {
        "case_id": "dedicated-download-popup",
        "case_type": "child_navigation",
        "action_type": "child_navigation",
        "expected_type": "download_popup",
        "expected_value": "EvalFileDownloadConfirm.do",
        "expected_url": "EvalFileDownloadConfirm.do",
        "pre_steps": [{"action_type": "check", "locator": "input[name='downloadScr']"}],
        "main_step": {"action_type": "child_navigation", "locator": "#btnOutput"},
    }

    result = engine._execute_action_case(
        object(),
        action_case,
        side="legacy",
        browser_name="edge",
        capture_dir=tmp_path,
        test_id="download_popup",
    )

    assert result["status"] == "PASS"
    assert contexts[0]["action_type"] == "check"
    assert contexts[0]["case_type"] == "check"
    assert "expected_type" not in contexts[0]
    assert "expected_value" not in contexts[0]
    assert contexts[1]["expected_type"] == "download_popup"
    assert contexts[1]["expected_value"] == "EvalFileDownloadConfirm.do"
    assert contexts[1]["expected_url"] == "EvalFileDownloadConfirm.do"


def test_scenario_stops_after_step_closes_page(tmp_path, monkeypatch):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"))
    calls = []

    def fake_execute_action(page, action_type, locator, value=None, **kwargs):
        calls.append((action_type, locator))
        return {
            "status": "PASS",
            "page_closed_after_action": True,
            "download_filename": "report.pdf",
            "state": {"url": "about:closed", "page_closed": True, "screenshot": str(tmp_path / "closed.png")},
        }

    monkeypatch.setattr(regression_engine_module, "execute_action", fake_execute_action)
    action_case = {
        "case_id": "download-misgenerated",
        "case_type": "upload_submit",
        "action_type": "upload_submit",
        "page_id": "Download.jsp",
        "pre_steps": [{"action_type": "download", "locator": "#download"}],
        "main_step": {"action_type": "submit", "locator": "form[name='DownloadForm']"},
    }

    result = engine._execute_action_case(
        object(),
        action_case,
        side="legacy",
        browser_name="edge",
        capture_dir=tmp_path,
        test_id="download_case",
    )

    assert result["status"] == "PASS"
    assert result["page_closed_after_action"] is True
    assert calls == [("download", "#download")]


def test_checklist_loader_filters_optional_modes(tmp_path):
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    checklist = tmp_path / "migration_checklist.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Checklist"
    sheet.append(
        [
            "case_id",
            "page_id",
            "automation_mode",
            "case_type",
            "action_type",
            "locator",
            "submit_locator",
            "test_data",
            "destructive",
            "enabled",
        ]
    )
    sheet.append(["auto-1", "Page.jsp", "auto", "snapshot", "snapshot", "", "", "", "false", "true"])
    sheet.append(["semi-1", "Page.jsp", "semi-auto", "click", "click", "#semi", "", "", "false", "true"])
    sheet.append(["destroy-1", "Page.jsp", "auto", "delete_action", "click", "#delete", "", "", "true", "true"])
    sheet.append(["neg-1", "Page.jsp", "auto", "negative_file_upload", "upload", "input[type='file']", "", "", "false", "true"])
    workbook.save(checklist)

    default_engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"), checklist_path=str(checklist))
    default_cases = default_engine._load_checklist_cases("Page.jsp")
    assert [case["label"] for case in default_cases] == ["auto-1"]
    assert default_cases[0]["case_id"] == "auto-1"
    coverage = {row["case_id"]: row for row in default_engine._checklist_case_rows["page.jsp"]}
    assert set(coverage) == {"auto-1", "semi-1", "destroy-1", "neg-1"}
    assert coverage["auto-1"]["excluded_reason"] == ""
    assert "--include-semi-auto" in coverage["semi-1"]["excluded_reason"]
    assert "--include-destructive" in coverage["destroy-1"]["excluded_reason"]
    assert "--include-negative" in coverage["neg-1"]["excluded_reason"]

    full_engine = RegressionEngine(
        mapping_path=str(mapping_path),
        output_dir=str(tmp_path / "out2"),
        checklist_path=str(checklist),
        include_semi_auto=True,
        include_destructive=True,
        include_negative=True,
        negative_profile="negative_file_upload",
    )
    full_cases = full_engine._load_checklist_cases("Page.jsp")
    assert {case["label"] for case in full_cases} == {"auto-1", "semi-1", "destroy-1", "neg-1"}


def test_guided_json_checklist_loader_builds_scenario(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    checklist = tmp_path / "guided_checklist.json"
    checklist.write_text(
        json.dumps(
            {
                "schema": "moonlight.guided_checklist.v1",
                "page_id": "JpBiblioList.jsp",
                "cases": [
                    {
                        "case_id": "biblio-initial",
                        "database_operation": "update",
                        "title": "書誌一覧初期表示確認",
                        "risk_level": "safe",
                        "automation_mode": "auto",
                        "steps": [
                            {"action_type": "assert_visible", "locator": "table"},
                            {"action_type": "assert_text", "locator": "__page__", "value": "検索結果一覧"},
                        ],
                        "expected": {"type": "text_visible", "value": "検索結果一覧"},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"), checklist_path=str(checklist))
    cases = engine._load_checklist_cases("JpBiblioList.jsp")

    assert engine._last_checklist_debug["format"] == "guided_json"
    assert engine._last_checklist_debug["status"] == "loaded"
    assert cases[0]["case_id"] == "biblio-initial"
    assert cases[0]["database_operation"] == "update"
    assert cases[0]["pre_steps"] == [{"action_type": "assert_visible", "locator": "table"}]
    assert cases[0]["main_step"]["action_type"] == "assert_text"
    assert cases[0]["main_step"]["value"] == "検索結果一覧"


def test_guided_json_checklist_loader_accepts_utf8_bom(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    checklist = tmp_path / "guided_checklist.json"
    checklist.write_text(
        json.dumps(
            {
                "schema": "moonlight.guided_checklist.v1",
                "page_id": "WwBiblioFileDownloadDisp.jsp",
                "cases": [
                    {
                        "case_id": "download-output",
                        "title": "Download output file",
                        "automation_mode": "auto",
                        "steps": [{"action_type": "download", "locator": "#btnOutput"}],
                    }
                ],
            }
        ),
        encoding="utf-8-sig",
    )

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"), checklist_path=str(checklist))
    cases = engine._load_checklist_cases("WwBiblioFileDownloadDisp.jsp")

    assert engine._last_checklist_debug["status"] == "loaded"
    assert [case["case_id"] for case in cases] == ["download-output"]


def test_guided_json_checklist_loader_filters_modes_and_destructive(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    checklist = tmp_path / "guided_checklist.json"
    checklist.write_text(
        json.dumps(
            {
                "page_id": "Page.jsp",
                "cases": [
                    {"case_id": "safe", "automation_mode": "auto", "steps": [{"action_type": "snapshot"}]},
                    {"case_id": "semi", "automation_mode": "semi-auto", "steps": [{"action_type": "snapshot"}]},
                    {
                        "case_id": "destroy",
                        "automation_mode": "auto",
                        "risk_level": "destructive",
                        "steps": [{"action_type": "click", "locator": "#delete"}],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    default_engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"), checklist_path=str(checklist))
    default_cases = default_engine._load_checklist_cases("Page.jsp")
    assert [case["case_id"] for case in default_cases] == ["safe"]
    coverage = {row["case_id"]: row for row in default_engine._checklist_case_rows["page.jsp"]}
    assert "--include-semi-auto" in coverage["semi"]["excluded_reason"]
    assert "--include-destructive" in coverage["destroy"]["excluded_reason"]

    full_engine = RegressionEngine(
        mapping_path=str(mapping_path),
        output_dir=str(tmp_path / "out2"),
        checklist_path=str(checklist),
        include_semi_auto=True,
        include_destructive=True,
    )
    full_cases = full_engine._load_checklist_cases("Page.jsp")
    assert [case["case_id"] for case in full_cases] == ["safe", "semi", "destroy"]


def test_guided_json_checklist_loader_filters_negative_profiles(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    checklist = tmp_path / "guided_negative_checklist.json"
    checklist.write_text(
        json.dumps(
            {
                "page_id": "Page.jsp",
                "cases": [
                    {"case_id": "safe", "automation_mode": "auto", "steps": [{"action_type": "snapshot"}]},
                    {
                        "case_id": "neg-js",
                        "automation_mode": "auto-negative",
                        "case_type": "negative_js_error",
                        "action_type": "negative_js_error",
                        "steps": [{"action_type": "negative_js_error", "locator": "#highlight"}],
                    },
                    {
                        "case_id": "neg-http",
                        "automation_mode": "auto-negative",
                        "case_type": "negative_http_500",
                        "action_type": "negative_http_500",
                        "steps": [{"action_type": "negative_http_500", "locator": "#pdf", "value": "**/*PDF*"}],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    default_engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"), checklist_path=str(checklist))
    default_cases = default_engine._load_checklist_cases("Page.jsp")
    assert [case["case_id"] for case in default_cases] == ["safe"]
    coverage = {row["case_id"]: row for row in default_engine._checklist_case_rows["page.jsp"]}
    assert "--include-negative" in coverage["neg-js"]["excluded_reason"]

    filtered_engine = RegressionEngine(
        mapping_path=str(mapping_path),
        output_dir=str(tmp_path / "out2"),
        checklist_path=str(checklist),
        include_negative=True,
        negative_profile="negative_http_500",
    )
    filtered_cases = filtered_engine._load_checklist_cases("Page.jsp")
    assert [case["case_id"] for case in filtered_cases] == ["safe", "neg-http"]
    filtered_coverage = {row["case_id"]: row for row in filtered_engine._checklist_case_rows["page.jsp"]}
    assert "--negative-profile" in filtered_coverage["neg-js"]["excluded_reason"]


def test_checklist_loader_matches_page_stem_without_extension(tmp_path):
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    checklist = tmp_path / "migration_checklist.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Checklist"
    sheet.append(
        [
            "case_id",
            "page_id",
            "automation_mode",
            "case_type",
            "action_type",
            "locator",
            "destructive",
            "enabled",
        ]
    )
    sheet.append(["stem-1", "page", "auto", "initial_display", "snapshot", "", "false", "true"])
    workbook.save(checklist)

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"), checklist_path=str(checklist))
    cases = engine._load_checklist_cases("Page.jsp")

    assert [case["case_id"] for case in cases] == ["stem-1"]
    coverage = engine._checklist_case_rows["page.jsp"]
    assert coverage[0]["page_id"] == "page"
    assert engine._last_checklist_debug["status"] == "loaded"


def test_checklist_loader_preserves_negative_expected_value_and_steps(tmp_path):
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    checklist = tmp_path / "migration_checklist.xlsx"
    pre_steps = json.dumps([{"action_type": "upload", "locator": "input[type='file']", "value": "${UPLOAD_FILE}"}])
    main_step = json.dumps({"action_type": "negative_http_500", "locator": "#submit", "expected_value": "**/UploadConf.do*"})

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Checklist"
    sheet.append(
        [
            "case_id",
            "page_id",
            "automation_mode",
            "case_type",
            "action_type",
            "locator",
            "expected_type",
            "expected_value",
            "pre_steps",
            "main_step",
            "destructive",
            "enabled",
        ]
    )
    sheet.append(["neg-http", "Page.jsp", "auto-negative", "negative_http_500", "negative_http_500", "#submit", "http_error", "**/UploadConf.do*", pre_steps, main_step, "false", "true"])
    workbook.save(checklist)

    engine = RegressionEngine(
        mapping_path=str(mapping_path),
        output_dir=str(tmp_path / "out"),
        checklist_path=str(checklist),
        include_negative=True,
        negative_profile="negative_http_500",
    )
    cases = engine._load_checklist_cases("Page.jsp")

    assert len(cases) == 1
    assert cases[0]["expected_value"] == "**/UploadConf.do*"
    assert cases[0]["pre_steps"][0]["action_type"] == "upload"
    assert cases[0]["main_step"]["action_type"] == "negative_http_500"


def test_checklist_loader_keeps_download_case_out_of_upload_submit(tmp_path):
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    checklist = tmp_path / "migration_checklist.xlsx"
    pre_steps = json.dumps([{"action_type": "upload", "locator": 'input[name="btSave"][type="button"]', "value": ""}])
    main_step = json.dumps({"action_type": "submit", "locator": "form[name='fmPDFDownload']"})

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Checklist"
    sheet.append(
        [
            "case_id",
            "page_id",
            "automation_mode",
            "case_type",
            "action_type",
            "locator",
            "expected_type",
            "pre_steps",
            "main_step",
            "destructive",
            "enabled",
            "test_title",
        ]
    )
    sheet.append(
        [
            "jpgazettepdfdownloaddisp-file_download-001",
            "JpGazettePDFDownloadDisp.jsp",
            "auto",
            "upload_submit",
            "upload_submit",
            'input[name="btSave"][type="button"]',
            "download",
            pre_steps,
            main_step,
            "false",
            "true",
            "ファイル出力ダウンロード確認",
        ]
    )
    workbook.save(checklist)

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"), checklist_path=str(checklist))
    cases = engine._load_checklist_cases("JpGazettePDFDownloadDisp.jsp")

    assert len(cases) == 1
    assert cases[0]["case_type"] == "file_download"
    assert cases[0]["action_type"] == "file_download"
    assert cases[0]["legacy_locator"] == 'input[name="btSave"][type="button"]'
    assert "pre_steps" not in cases[0]
    assert "main_step" not in cases[0]


def test_checklist_loader_preserves_download_option_steps(tmp_path):
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    checklist = tmp_path / "migration_checklist.xlsx"
    pre_steps = json.dumps(
        [
            {"action_type": "check", "locator": 'input[name="cbKind"][type="checkbox"][value="11"]'},
            {"action_type": "uncheck", "locator": 'input[name="cbKind"][type="checkbox"][value="10"]'},
        ]
    )
    main_step = json.dumps({"action_type": "download", "locator": 'input[name="btSave"][type="button"]'})

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Checklist"
    sheet.append(
        [
            "case_id",
            "page_id",
            "automation_mode",
            "case_type",
            "action_type",
            "locator",
            "expected_type",
            "pre_steps",
            "main_step",
            "destructive",
            "enabled",
            "test_title",
        ]
    )
    sheet.append(
        [
            "jpgazettepdfdownloaddisp-file_download-001-option",
            "JpGazettePDFDownloadDisp.jsp",
            "auto",
            "file_download",
            "download",
            'input[name="btSave"][type="button"]',
            "download",
            pre_steps,
            main_step,
            "false",
            "true",
            "出力条件代表パターン確認",
        ]
    )
    workbook.save(checklist)

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path / "out"), checklist_path=str(checklist))
    cases = engine._load_checklist_cases("JpGazettePDFDownloadDisp.jsp")

    assert len(cases) == 1
    assert cases[0]["pre_steps"][0]["action_type"] == "check"
    assert cases[0]["pre_steps"][1]["action_type"] == "uncheck"
    assert cases[0]["main_step"]["action_type"] == "download"
    assert cases[0]["main_step"]["locator"] == 'input[name="btSave"][type="button"]'


def test_report_coverage_matrix_renders_checklist_case_rows(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    screenshot = output_dir / "shot.png"
    Image.new("RGB", (1, 1), "white").save(screenshot)

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(output_dir))
    engine._checklist_case_rows = {
        "upload.jsp": [
            {
                "case_id": "upload-001",
                "test_title": "Upload main path",
                "automation_mode": "auto",
                "destructive": "false",
                "excluded_reason": "",
            },
            {
                "case_id": "upload-002",
                "test_title": "Delete uploaded row",
                "automation_mode": "auto-db",
                "destructive": "true",
                "excluded_reason": "destructive=true; requires --include-destructive",
            },
        ],
        "download.jsp": [
            {
                "case_id": "download-001",
                "test_title": "Download template",
                "automation_mode": "auto",
                "destructive": "false",
                "excluded_reason": "",
            }
        ],
    }
    report = engine.render_report(
        [
            {
                "page_id": "Upload.jsp",
                "case_id": "upload-001",
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
                "case_id": "download-001",
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
                "download_filename_match": True,
                "legacy_download_filename": "template.tsv",
                "new_download_filename": "template.tsv",
                "legacy_download_path": str(tmp_path / "template.tsv"),
                "new_download_path": str(tmp_path / "template.tsv"),
                "legacy_console_screenshot": str(screenshot),
                "new_console_screenshot": str(screenshot),
                "legacy_console_error_count": 1,
                "new_console_error_count": 0,
                "legacy_http_error_count": 0,
                "new_http_error_count": 1,
                "legacy_request_failed_count": 0,
                "new_request_failed_count": 0,
            },
        ]
    )

    html = Path(report).read_text(encoding="utf-8")
    assert "Checklist Cases" in html
    assert "<td><b>status</b></td>" in html
    assert 'href="#case-0001-upload-001"' in html
    assert 'id="case-0001-upload-001"' in html
    assert 'href="#case-0002-download-001"' in html
    assert '<span class="status PASS">PASS</span>' in html
    assert '<span class="status EXCLUDED">EXCLUDED</span>' in html
    assert 'class="back-to-top" href="#top"' in html
    assert "upload-001" in html
    assert "Upload main path" in html
    assert "auto-db" in html
    assert "destructive=true; requires --include-destructive" in html
    assert "download-001" in html
    assert "Download filename match" in html
    assert "template.tsv" in html
    assert "Legacy Console" in html
    assert "Console errors" in html
    assert "HTTP errors" in html


def test_download_compare_tolerates_filename_mismatch_when_content_matches(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    screenshot = tmp_path / "shot.png"
    Image.new("RGB", (1, 1), "white").save(screenshot)
    legacy_download = tmp_path / "legacy.tsv"
    new_download = tmp_path / "new.tsv"
    legacy_download.write_bytes(b"same")
    new_download.write_bytes(b"same")

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path))
    compared = engine._compare_state(
        "Download.jsp",
        "High",
        "download",
        {"url": "https://legacy/app", "dom": "", "screenshot": str(screenshot)},
        {"url": "https://new/app", "dom": "", "screenshot": str(screenshot)},
        tmp_path / "diff.png",
        action_type="download",
        legacy_action={"status": "PASS", "saved_filename": "legacy.tsv", "download_path": str(legacy_download)},
        new_action={"status": "PASS", "saved_filename": "new.tsv", "download_path": str(new_download)},
    )

    assert compared["status"] == "PASS"
    assert compared["download_success_match"] is True
    assert compared["download_filename_match"] is False
    assert compared["download_size_match"] is True
    assert compared["download_hash_match"] is True
    assert compared["legacy_download_filename"] == "legacy.tsv"
    assert compared["new_download_filename"] == "new.tsv"


def test_download_compare_marks_content_mismatch_as_diff(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    screenshot = tmp_path / "shot.png"
    Image.new("RGB", (1, 1), "white").save(screenshot)
    legacy_download = tmp_path / "legacy.tsv"
    new_download = tmp_path / "new.tsv"
    legacy_download.write_bytes(b"legacy")
    new_download.write_bytes(b"new")

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path))
    compared = engine._compare_state(
        "Download.jsp",
        "High",
        "download",
        {"url": "https://legacy/app", "dom": "", "screenshot": str(screenshot)},
        {"url": "https://new/app", "dom": "", "screenshot": str(screenshot)},
        tmp_path / "diff.png",
        action_type="download",
        legacy_action={"status": "PASS", "saved_filename": "legacy.tsv", "download_path": str(legacy_download)},
        new_action={"status": "PASS", "saved_filename": "new.tsv", "download_path": str(new_download)},
    )

    assert compared["status"] == "DIFF"
    assert compared["download_success_match"] is True
    assert compared["download_hash_match"] is False


def test_download_compare_uses_suggested_filename_before_unique_saved_name(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path))

    compared = engine._compare_state(
        "Download.jsp",
        "Low",
        "file_download",
        {"url": "about:closed", "dom": "", "screenshot": ""},
        {"url": "about:closed", "dom": "", "screenshot": ""},
        tmp_path / "diff.png",
        action_type="file_download",
        legacy_action={"status": "PASS", "download_filename": "report.pdf", "saved_filename": "report.pdf"},
        new_action={"status": "PASS", "download_filename": "report.pdf", "saved_filename": "report (1).pdf"},
    )

    assert compared["status"] == "PASS"
    assert compared["download_filename_match"] is True
    assert compared["legacy_download_filename"] == "report.pdf"
    assert compared["new_download_filename"] == "report.pdf"


def test_download_compare_ignores_browser_temp_uuid_filename(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    screenshot = tmp_path / "shot.png"
    Image.new("RGB", (1, 1), "white").save(screenshot)
    legacy_download = tmp_path / "17e060f2-fec6-425c-9f98-3a66b2493823"
    new_download = tmp_path / "9cff8e81-6213-4082-ad3a-3a72e2ca331d"
    legacy_download.write_bytes(b"same")
    new_download.write_bytes(b"same")

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path))
    compared = engine._compare_state(
        "Download.jsp",
        "High",
        "download",
        {"url": "https://legacy/app", "dom": "", "screenshot": str(screenshot)},
        {"url": "https://new/app", "dom": "", "screenshot": str(screenshot)},
        tmp_path / "diff.png",
        action_type="download",
        legacy_action={
            "status": "PASS",
            "download_suggested_filename": legacy_download.name,
            "download_filename": legacy_download.name,
            "download_path": str(legacy_download),
        },
        new_action={
            "status": "PASS",
            "download_suggested_filename": new_download.name,
            "download_filename": new_download.name,
            "download_path": str(new_download),
        },
    )

    assert compared["status"] == "PASS"
    assert compared["download_success_match"] is True
    assert compared["download_filename_match"] is None
    assert compared["legacy_download_filename"] == ""
    assert compared["new_download_filename"] == ""
    assert compared["download_hash_match"] is True


def test_file_download_compare_uses_download_filename_fields(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path))

    compared = engine._compare_state(
        "Download.jsp",
        "High",
        "file output",
        {"url": "about:closed", "dom": "", "screenshot": ""},
        {"url": "about:closed", "dom": "", "screenshot": ""},
        tmp_path / "diff.png",
        action_type="file_download",
        legacy_action={"status": "PASS", "saved_filename": "output.pdf", "download_path": str(tmp_path / "output.pdf")},
        new_action={"status": "PASS", "saved_filename": "output.pdf", "download_path": str(tmp_path / "output.pdf")},
    )

    assert compared["status"] == "PASS"
    assert compared["visual"]["status"] == "SKIPPED"
    assert compared["download_filename_match"] is True
    assert compared["legacy_download_filename"] == "output.pdf"


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
    assert "<b>Action type</b><span>submit</span>" in html
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


def test_missing_dynamic_jsp_row_control_is_skipped_by_static_mapping():
    assert RegressionEngine._is_dynamic_jsp_row_control(
        {
            "kind": "button",
            "key": "field:deleteFileName",
            "label": "deleteFileName",
            "locator": "input[type=\"button\"][onclick=\"deleteFile('<bean:write name=\"]",
        }
    )


def test_compare_state_treats_table_data_variance_as_warn(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    legacy_image = output_dir / "legacy.png"
    new_image = output_dir / "new.png"
    Image.new("RGB", (10, 10), "white").save(legacy_image)
    image = Image.new("RGB", (10, 10), "white")
    image.putpixel((0, 0), (0, 0, 0))
    image.save(new_image)

    shared_text = "\n".join(
        [
            "社内分類更新処理待ちファイル一覧(国内)",
            "アップロード日時",
            "更新処理待ちファイル",
            "削除",
            "2026年04月24日 UopcSampleDataJp.csv",
        ]
    )
    legacy_state = {
        "screenshot": str(legacy_image),
        "url": "http://legacy.example/patlics/UopcUploadListDispJP.do",
        "dom": "<table><tr><td>legacy rows</td></tr></table>",
        "text": shared_text + "\nlegacy-only-row",
    }
    new_state = {
        "screenshot": str(new_image),
        "url": "http://new.example/patlics/UopcUploadListDispJP.do",
        "dom": "<table><tr><td>new rows</td></tr></table>",
        "text": shared_text + "\nnew-only-row",
    }
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(output_dir), visual_threshold_percent=0.1)

    result = engine._compare_state(
        "UopcUploadListDispJP.jsp",
        "High",
        "initial display",
        legacy_state,
        new_state,
        output_dir / "diff.png",
        action_type="initial_display",
    )

    assert result["status"] == "WARN"
    assert result["visual"]["data_variance_tolerated"] is True


def test_database_operation_compares_each_side_before_after(tmp_path):
    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    legacy_before = output_dir / "legacy_before.png"
    legacy_after = output_dir / "legacy_after.png"
    new_before = output_dir / "new_before.png"
    new_after = output_dir / "new_after.png"
    Image.new("RGB", (10, 10), "white").save(legacy_before)
    Image.new("RGB", (10, 10), "white").save(new_before)
    legacy_changed = Image.new("RGB", (10, 10), "white")
    legacy_changed.putpixel((0, 0), (0, 0, 0))
    legacy_changed.save(legacy_after)
    new_changed = Image.new("RGB", (10, 10), "white")
    new_changed.putpixel((0, 0), (0, 0, 0))
    new_changed.save(new_after)

    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(output_dir), visual_threshold_percent=0.1)
    result = engine._compare_database_operation(
        "UopcUploadListDispJP.jsp",
        "High",
        "削除ボタン動作確認",
        "delete_action",
        "delete",
        {"screenshot": str(legacy_before), "url": "http://legacy/app/List.do"},
        {"screenshot": str(new_before), "url": "http://new/app/List.do"},
        {"status": "PASS", "state": {"screenshot": str(legacy_after), "url": "http://legacy/app/List.do"}},
        {"status": "PASS", "state": {"screenshot": str(new_after), "url": "http://new/app/List.do"}},
        output_dir,
        "delete_case",
    )

    assert result["status"] == "PASS"
    assert result["comparison_mode"] == "database_operation_before_after"
    assert result["database_operation"] == "delete"
    assert result["legacy_delta"]["status"] == "DIFF"
    assert result["new_delta"]["status"] == "DIFF"


def test_database_operation_kind_covers_crud_actions():
    assert RegressionEngine._database_operation_kind({"case_type": "create_action", "label": "登録"}, "click", "click") == "create"
    assert RegressionEngine._database_operation_kind({"case_type": "update_action", "label": "更新"}, "click", "click") == "update"
    assert RegressionEngine._database_operation_kind({"case_type": "delete_action", "label": "削除"}, "click", "click") == "delete"
    assert RegressionEngine._database_operation_kind({"case_type": "search_normal", "label": "検索"}, "search", "click") is None
    assert RegressionEngine._database_operation_kind({"case_type": "upload_submit", "label": "アップロード確認"}, "upload_submit", "upload") is None


def test_database_operation_kind_ignores_child_navigation_entry_word():
    assert RegressionEngine._database_operation_kind(
        {"case_type": "child_navigation", "label": "Evaluation-information entry opens child"},
        "child_navigation",
        "click",
    ) is None


def test_database_operation_kind_requires_explicit_guided_json_intent():
    guided_case = {
        "source": "guided_json_checklist",
        "case_type": "browser_dialog",
        "label": "Registration entry rejects an empty selection",
        "main_step": {"cleanup_script": "() => { delete window.testState; }"},
    }

    assert RegressionEngine._database_operation_kind(guided_case, "browser_dialog", "browser_dialog") is None
    assert RegressionEngine._database_operation_kind(
        {**guided_case, "database_operation": "create"},
        "click",
        "click",
    ) == "create"


@pytest.mark.parametrize(
    "action_type",
    ["assert_attached", "assert_checked", "assert_text", "assert_url", "assert_value", "assert_visible"],
)
def test_database_operation_kind_ignores_passive_assertions(action_type):
    assert RegressionEngine._database_operation_kind(
        {"case_type": "initial_state", "label": "registration entry controls are visible"},
        action_type,
        action_type,
    ) is None


def test_leaving_actions_require_target_reopen():
    assert RegressionEngine._requires_target_reopen_after_action(
        {"case_type": "back_action", "label": "戻る"},
        "back_action",
        "click",
        {"status": "PASS"},
        {"status": "PASS"},
    )
    assert RegressionEngine._requires_target_reopen_after_action(
        {"case_type": "close_window", "locator": "input[onclick*='window.close']"},
        "close_window",
        "click",
        {"status": "PASS"},
        {"status": "PASS"},
    )
    assert RegressionEngine._requires_target_reopen_after_action(
        {"case_type": "click", "label": "キャンセル"},
        "click",
        "click",
        {"status": "PASS"},
        {"status": "PASS"},
    )
    assert RegressionEngine._requires_target_reopen_after_action(
        {"case_type": "click", "label": "next"},
        "click",
        "click",
        {"status": "PASS", "page_closed_after_action": True},
        {"status": "PASS"},
    )
    assert not RegressionEngine._requires_target_reopen_after_action(
        {"case_type": "negative_file_upload", "label": "invalid upload"},
        "negative_file_upload",
        "upload",
        {"status": "PASS"},
        {"status": "PASS"},
    )
    assert RegressionEngine._requires_target_reopen_after_action(
        {"case_type": "negative_http_500", "label": "simulated server error"},
        "negative_http_500",
        "negative_http_500",
        {"status": "PASS", "navigation_detected": True},
        {"status": "PASS", "navigation_detected": True},
    )
    assert not RegressionEngine._requires_target_reopen_after_action(
        {"case_type": "delete_action", "label": "削除"},
        "delete_action",
        "click",
        {"status": "PASS"},
        {"status": "PASS"},
    )


def test_target_reopen_hint_is_suppressed_when_page_still_matches_target():
    assert not RegressionEngine._should_reopen_target_for_following_action(
        True,
        {
            "requires_reopen": False,
            "reason": "action did not navigate away from the current page",
        },
    )
    assert RegressionEngine._should_reopen_target_for_following_action(
        False,
        {
            "requires_reopen": True,
            "reason": "current page no longer matches target page",
        },
    )


def test_reopen_prefers_open_route_step_page_before_login_recovery(tmp_path):
    class Context:
        def __init__(self):
            self.pages = []

    class Page:
        def __init__(self, url, *, closed=False):
            self.url = url
            self._closed = closed
            self.frames = []
            self.context = None
            self.front = 0

        def is_closed(self):
            return self._closed

        def bring_to_front(self):
            self.front += 1

        def wait_for_load_state(self, state, timeout=None):
            return None

    context = Context()
    closed_popup = Page("about:closed", closed=True)
    parent = Page("http://example.test/patlics/JpBiblioListForEasySearch.do")
    login = Page("http://example.test/patlics/login.jsp")
    for page in (closed_popup, parent, login):
        page.context = context
    context.pages = [closed_popup, login, parent]

    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path))

    selected, nav = engine._takeover_recovery_page(
        closed_popup,
        {"steps": [{"active_page_url": "http://example.test/patlics/JpBiblioListForEasySearch.do"}]},
    )

    assert selected is parent
    assert nav["status"] == "PASS"
    assert nav["strategy"] == "context_sibling_takeover"
    assert nav["route_step_detected"] is True
    assert parent.front == 1


def test_reopen_ignores_negative_evidence_page_when_parent_route_step_is_open(tmp_path):
    class Locator:
        def __init__(self, count):
            self._count = count

        def count(self):
            return self._count

    class Frame:
        def __init__(self, url, *, negative=False):
            self.url = url
            self.negative = negative

        def locator(self, selector):
            return Locator(1 if self.negative else 0)

    class Context:
        def __init__(self):
            self.pages = []

    class Page:
        def __init__(self, url, *, negative=False):
            self.url = url
            self._closed = False
            self.frames = [Frame(url, negative=negative)]
            self.context = None
            self.front = 0

        def is_closed(self):
            return self._closed

        def bring_to_front(self):
            self.front += 1

        def wait_for_load_state(self, state, timeout=None):
            return None

    context = Context()
    negative_page = Page("http://example.test/patlics/WwSearchAidSearch.do?method=forEasySearch", negative=True)
    parent = Page("http://example.test/patlics/PatlicsTopMain.do")
    for page in (negative_page, parent):
        page.context = context
    context.pages = [negative_page, parent]

    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path))
    route = {
        "target_page": "WwSearchAid.jsp",
        "steps": [
            {"active_page_url": "http://example.test/patlics/PatlicsTopMain.do"},
            {"active_page_url": "http://example.test/patlics/WwSearchAidSearch.do?method=forEasySearch"},
        ],
    }

    selected, nav = engine._takeover_recovery_page(negative_page, route)

    assert selected is parent
    assert nav["status"] == "PASS"
    assert nav["negative_evidence"] is False
    assert parent.front == 1
    assert engine._page_is_route_map_step(negative_page, route) is False


def test_closing_action_report_preserves_parent_state_and_keeps_reopened_state_for_recovery(tmp_path, monkeypatch):
    class Page:
        def __init__(self, url):
            self.url = url

    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path))

    def fake_capture_state(page, output_dir, name):
        return {
            "screenshot": str(Path(output_dir) / f"{name}.png"),
            "url": getattr(page, "url", ""),
            "dom": name,
            "text": name,
        }

    def fake_compare_state(page_id, risk, action, legacy_state, new_state, diff_path, **extra):
        return {
            "page_id": page_id,
            "risk": risk,
            "action": action,
            "status": "PASS",
            "legacy_screenshot": legacy_state.get("screenshot"),
            "new_screenshot": new_state.get("screenshot"),
            **extra,
        }

    monkeypatch.setattr(regression_engine_module, "_capture_state", fake_capture_state)
    engine._build_action_plan = lambda page_id, mapping: (
        [
            {
                "case_type": "file_download",
                "label": "download",
                "legacy_locator": "#download",
                "new_locator": "#download",
            },
            {
                "case_type": "close_window",
                "label": "close",
                "legacy_locator": "#close",
                "new_locator": "#close",
            },
        ],
        "test",
    )
    engine._execute_action_case = lambda page, action_case, **kwargs: {
        "status": "PASS",
        "page_closed_after_action": True,
        "state": {
            "screenshot": str(tmp_path / f"{kwargs['side']}_parent_after_close.png"),
            "url": f"http://{kwargs['side']}/parent.jsp",
            "dom": "<parent-page />",
            "capture_scope": "opener_after_popup_close",
        },
    }
    engine._reopen_target_pair = lambda legacy_page, new_page, mapping, page_dir, browser_name, *, reason: (
        Page("http://legacy/target.jsp"),
        Page("http://new/target.jsp"),
        {"status": "PASS", "reason": reason},
    )
    engine._compare_state = fake_compare_state

    results = engine._run_captured_page_pair(
        Page("http://legacy/start.jsp"),
        Page("http://new/start.jsp"),
        {"page_id": "Download.jsp", "risk": "Low"},
        tmp_path,
        "chrome",
        {"status": "PASS"},
        {"status": "PASS"},
        manual=False,
    )

    action_result = next(item for item in results if item["action"] == "download")
    assert action_result["legacy_screenshot"].endswith("legacy_parent_after_close.png")
    assert action_result["new_screenshot"].endswith("new_parent_after_close.png")
    assert action_result["legacy_action"]["state_before_reopen"]["capture_scope"] == "opener_after_popup_close"
    assert action_result["legacy_action"]["state_after_reopen"]["screenshot"].endswith("01_download_legacy_after_reopen.png")
    assert action_result["new_action"]["state_after_reopen"]["screenshot"].endswith("01_download_new_after_reopen.png")
    assert action_result["post_action_reopen"]["status"] == "PASS"
    log_text = (tmp_path / "full_test_log.jsonl").read_text(encoding="utf-8")
    assert '"event": "action_executed"' in log_text
    assert '"event": "target_reopen_finished"' in log_text
    assert '"event": "compare_result"' in log_text


def test_terminal_closing_action_does_not_require_target_reopen(tmp_path, monkeypatch):
    class Page:
        def __init__(self, url):
            self.url = url

    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path))

    def fake_capture_state(page, output_dir, name):
        return {
            "screenshot": str(Path(output_dir) / f"{name}.png"),
            "url": getattr(page, "url", ""),
            "dom": name,
            "text": name,
        }

    def fake_compare_state(page_id, risk, action, legacy_state, new_state, diff_path, **extra):
        return {
            "page_id": page_id,
            "risk": risk,
            "action": action,
            "status": "PASS",
            "legacy_screenshot": legacy_state.get("screenshot"),
            "new_screenshot": new_state.get("screenshot"),
            **extra,
        }

    monkeypatch.setattr(regression_engine_module, "_capture_state", fake_capture_state)
    engine._build_action_plan = lambda page_id, mapping: (
        [
            {
                "case_type": "close_window",
                "label": "confirm and reflect",
                "legacy_locator": "#confirm",
                "new_locator": "#confirm",
            }
        ],
        "checklist",
    )
    engine._execute_action_case = lambda page, action_case, **kwargs: {
        "status": "PASS",
        "page_closed_after_action": True,
        "state": {
            "screenshot": str(tmp_path / f"{kwargs['side']}_parent_after_close.png"),
            "url": f"http://{kwargs['side']}/parent.jsp",
            "dom": "<parent-page />",
            "capture_scope": "opener_after_popup_close",
        },
    }
    engine._reopen_target_pair = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("terminal action must not reopen the target page")
    )
    engine._compare_state = fake_compare_state

    results = engine._run_captured_page_pair(
        Page("http://legacy/start.jsp"),
        Page("http://new/start.jsp"),
        {"page_id": "Popup.jsp", "risk": "Low"},
        tmp_path,
        "chrome",
        {"status": "PASS"},
        {"status": "PASS"},
        manual=False,
    )

    action_result = next(item for item in results if item["action"] == "confirm and reflect")
    assert action_result["status"] == "PASS"
    assert "post_action_reopen" not in action_result
    log_text = (tmp_path / "full_test_log.jsonl").read_text(encoding="utf-8")
    assert '"event": "target_reopen_skipped_terminal_action"' in log_text


def test_failed_reopen_does_not_relabel_successful_popup_reflection_as_blocked(tmp_path, monkeypatch):
    class Page:
        def __init__(self, url):
            self.url = url

    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path))

    def fake_capture_state(page, output_dir, name):
        return {
            "screenshot": str(Path(output_dir) / f"{name}.png"),
            "url": getattr(page, "url", ""),
            "dom": name,
            "text": name,
        }

    def fake_compare_state(page_id, risk, action, legacy_state, new_state, diff_path, **extra):
        return {
            "page_id": page_id,
            "risk": risk,
            "action": action,
            "status": "PASS",
            "legacy_screenshot": legacy_state.get("screenshot"),
            "new_screenshot": new_state.get("screenshot"),
            **extra,
        }

    monkeypatch.setattr(regression_engine_module, "_capture_state", fake_capture_state)
    engine._compare_state = fake_compare_state
    engine._build_action_plan = lambda page_id, mapping: (
        [
            {
                "case_type": "close_window",
                "label": "confirm and reflect",
                "legacy_locator": "#confirm",
                "new_locator": "#confirm",
            },
            {
                "case_type": "snapshot",
                "label": "remaining popup assertion",
                "legacy_locator": "__page__",
                "new_locator": "__page__",
            },
        ],
        "checklist",
    )
    engine._execute_action_case = lambda page, action_case, **kwargs: {
        "status": "PASS",
        "page_closed_after_action": True,
        "state": {
            "screenshot": str(tmp_path / f"{kwargs['side']}_parent_after_close.png"),
            "url": f"http://{kwargs['side']}/parent.jsp",
            "dom": "<parent-page />",
            "capture_scope": "opener_after_popup_close",
        },
    }
    engine._reopen_target_pair = lambda legacy_page, new_page, mapping, page_dir, browser_name, *, reason: (
        legacy_page,
        new_page,
        {"status": "BLOCKED", "reason": "route replay failed"},
    )

    results = engine._run_captured_page_pair(
        Page("http://legacy/start.jsp"),
        Page("http://new/start.jsp"),
        {"page_id": "Popup.jsp", "risk": "Low"},
        tmp_path,
        "chrome",
        {"status": "PASS"},
        {"status": "PASS"},
        manual=False,
    )

    reflection = next(item for item in results if item["action"] == "confirm and reflect")
    recovery = next(item for item in results if item["action"] == "Recover target page for remaining checklist actions")
    assert reflection["status"] == "PASS"
    assert reflection["recovery_failed_for_following_actions"] is True
    assert recovery["status"] == "BLOCKED"
    assert not any(item["action"] == "remaining popup assertion" for item in results)


def test_guided_checklist_plan_skips_static_missing_element_noise(tmp_path, monkeypatch):
    class Page:
        def __init__(self, url):
            self.url = url

    mapping_path = tmp_path / "page_mapping.json"
    mapping_path.write_text(json.dumps({"page_mappings": []}), encoding="utf-8")
    engine = RegressionEngine(mapping_path=str(mapping_path), output_dir=str(tmp_path))

    def fake_capture_state(page, output_dir, name):
        return {
            "screenshot": str(Path(output_dir) / f"{name}.png"),
            "url": page.url,
            "dom": name,
            "text": name,
        }

    def fake_compare_state(page_id, risk, action, legacy_state, new_state, diff_path, **extra):
        return {
            "page_id": page_id,
            "risk": risk,
            "action": action,
            "status": "PASS",
            "legacy_screenshot": legacy_state.get("screenshot"),
            "new_screenshot": new_state.get("screenshot"),
            **extra,
        }

    monkeypatch.setattr(regression_engine_module, "_capture_state", fake_capture_state)
    engine._compare_state = fake_compare_state
    engine._build_action_plan = lambda page_id, mapping: ([], "checklist")

    results = engine._run_captured_page_pair(
        Page("http://legacy/WwSearchAid.do"),
        Page("http://new/WwSearchAid.do"),
        {
            "page_id": "WwSearchAid.jsp",
            "risk": "High",
            "missing_legacy_elements": [
                {
                    "key": "KEY",
                    "kind": "input",
                    "locator": "[name='KEY']",
                    "action_hint": "fill",
                }
            ],
        },
        tmp_path,
        "chrome",
        {"status": "PASS"},
        {"status": "PASS"},
        manual=False,
    )

    assert [item["action"] for item in results] == ["page_snapshot"]
