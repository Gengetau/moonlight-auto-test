from collections import Counter

import pytest

from src.regression_engine import RegressionEngine


def test_migration_regression(
    request,
    target_pages,
    manual,
    page_pair_factory,
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
    upload_profile_config,
    regression_output_dir,
    include_semi_auto,
    include_destructive,
    include_negative,
    negative_profile,
):
    """
    Legacy/New 全量或风险优先回归入口。

    默认执行全量页面；传入 --risk-only 时只执行 High/Medium 风险页面。
    传入 --target-page 或 --target-pages 时按指定 JSP 队列逐页执行，且无视风险等级。
    """
    if not request.config.getoption("--run-migration"):
        pytest.skip("requires --run-migration with real browser/login environment")

    engine = RegressionEngine(
        mapping_path=mapping_path,
        checklist_path=checklist_path,
        route_map_path=route_map_path,
        force_route_map=force_route_map,
        upload_file=upload_file,
        upload_profile_config=upload_profile_config,
        output_dir=regression_output_dir or "./output/regression",
        include_semi_auto=include_semi_auto,
        include_destructive=include_destructive,
        include_negative=include_negative,
        negative_profile=negative_profile,
        legacy_base_url=login_entry["legacy_url"],
        new_base_url=login_entry["new_url"],
    )

    queue = target_pages or [None]
    combined_summary = Counter()
    report_paths = []
    final_status = "PASS"

    for index, target_page in enumerate(queue, start=1):
        label = target_page or "<selected pages>"
        print(f"[QUEUE] START {index}/{len(queue)}: {label}")
        legacy_page, new_page, close_pages = page_pair_factory()
        try:
            result = engine.run(
                legacy_page,
                new_page,
                browser_name=browser_name,
                risk_only=risk_only,
                limit=regression_limit,
                target_page=target_page,
                manual=manual,
            )
        finally:
            close_pages()

        combined_summary.update(result["summary"])
        report_paths.append(result["report_path"])
        if result["status"] == "BLOCKED":
            final_status = "BLOCKED"
        elif result["status"] == "DIFF" and final_status != "BLOCKED":
            final_status = "DIFF"
        print(
            f"[QUEUE] END {index}/{len(queue)}: "
            f"{label} status={result['status']} report={result['report_path']}"
        )

    report_text = ", ".join(report_paths)
    assert combined_summary.get("BLOCKED", 0) == 0, f"Blocked regression steps. Reports: {report_text}"
    assert combined_summary.get("DIFF", 0) == 0, f"Regression diffs found. Reports: {report_text}"
    assert final_status == "PASS"
