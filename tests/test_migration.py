import pytest

from src.regression_engine import RegressionEngine


def test_migration_regression(
    request,
    target_page,
    manual,
    legacy_page,
    new_page,
    browser_name,
    mapping_path,
    risk_only,
    regression_limit,
    struts_config,
    login_entry,
    checklist_path,
    route_map_path,
    force_route_map,
    upload_file,
):
    """
    Legacy/New 全量或风险优先回归入口。

    默认执行全量页面；传入 --risk-only 时只执行 High/Medium 风险页面。
    传入 --target-page 时只执行指定 JSP，且无视风险等级。
    """
    if not request.config.getoption("--run-migration"):
        pytest.skip("requires --run-migration with real browser/login environment")

    engine = RegressionEngine(
        mapping_path=mapping_path,
        checklist_path=checklist_path,
        route_map_path=route_map_path,
        force_route_map=force_route_map,
        upload_file=upload_file,
        legacy_base_url=login_entry["legacy_url"],
        new_base_url=login_entry["new_url"],
    )
    result = engine.run(
        legacy_page,
        new_page,
        browser_name=browser_name,
        risk_only=risk_only,
        limit=regression_limit,
        target_page=target_page,
        manual=manual,
        )

    assert result["summary"].get("BLOCKED", 0) == 0, f"Blocked regression steps. Report: {result['report_path']}"
    assert result["summary"].get("DIFF", 0) == 0, f"Regression diffs found. Report: {result['report_path']}"
    assert result["status"] == "PASS"
