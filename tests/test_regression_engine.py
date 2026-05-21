import json
from pathlib import Path

from PIL import Image

from src.action_executor import build_steps_from_page_mapping
from src.assert_engine import compare_visual_screenshot
from src.regression_engine import RegressionEngine


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
