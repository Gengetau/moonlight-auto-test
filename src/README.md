# Source Modules

This directory contains the core implementation for Moonlight UI Regression Toolkit.

## Scanner and Mapping

- `jsp_scanner.py` extracts structured UI elements from JSP and Struts tag source.
- `page_mapping.py` compares legacy and migrated scan output.
- `page_evidence_builder.py` builds normalized evidence used by case planning and reports.
- `rendered_scanner.py` extracts runtime evidence from rendered browser pages.
- `runtime_page_profile.py` captures live DOM profiles after route validation.

## Checklist Planning

- `checklist_generator.py` converts scan and mapping data into Markdown or Excel checklists.
- `page_case_planner.py` builds capability-aware page profiles and page-specific automated cases.
- `page_spec_generator.py` and `page_spec_checklist_generator.py` support page specification workflows.
- `coverage_policy.py` keeps generated coverage expectations explicit and testable.

## Runtime Execution

- `action_executor.py` translates semantic actions into Playwright interactions.
- `regression_engine.py` coordinates legacy/new execution and evidence comparison.
- `assert_engine.py` contains shared text and URL assertions.
- `data_loader.py`, `config_parser.py`, and `error_handler.py` provide runtime support.

## Route Navigation

- `route_catalog.py` generates static candidate routes.
- `route_map_runner.py` validates candidate routes in a browser and records usable route maps.
- `route_runtime_verifier.py` handles runtime route verification and manual intervention.
- `route_navigator.py` replays route maps during regression execution.

## GUI and Command Building

- `gui.py` and `app_gui.py` provide local operator interfaces.
- `gui_command_builder.py` builds reproducible CLI commands from GUI state.
- `browser_window.py` and `browser_print.py` contain browser helper utilities.

## Notes

Some parser rules and fixtures intentionally include Japanese UI labels because the toolkit targets Japanese enterprise applications. Developer-facing documentation, CLI help, comments, and generated English report labels should remain in English.
