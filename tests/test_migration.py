from src.regression_engine import RegressionEngine


def test_migration_regression(
    target_page,
    manual,
    legacy_page,
    new_page,
    browser_name,
    mapping_path,
    risk_only,
    regression_limit,
    struts_config,
):
    """
    Legacy/New 全量或风险优先回归入口。

    默认执行全量页面；传入 --risk-only 时只执行 High/Medium 风险页面。
    传入 --target-page 时只执行指定 JSP，且无视风险等级。
    """
    engine = RegressionEngine(mapping_path=mapping_path)
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
