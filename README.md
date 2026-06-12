# Moonlight UI Regression Toolkit

Moonlight is a Python-based regression testing toolkit for legacy JSP applications that are being migrated from Struts-style UI flows to newer implementations. It scans JSP source, maps legacy and migrated pages, generates executable test checklists, and drives Playwright-based comparisons across both environments.

The project is designed for enterprise migration work where direct URL access is unreliable, pages are nested in frames, and test cases need to be derived from real page structure instead of hand-written click scripts.

## What It Does

- Scans JSP source and extracts forms, fields, links, buttons, file inputs, Struts tags, locators, and action hints.
- Compares legacy and migrated page structures to identify locator drift, missing fields, and high-risk pages.
- Generates Markdown or Excel regression checklists with page profiles, executable cases, skipped-template diagnostics, and coverage notes.
- Executes semantic actions with Playwright, including submit, upload, click, navigation, select, fill, download, popup, and close-window flows.
- Supports route-map based navigation for systems where target pages cannot be reached through stable direct URLs.
- Captures browser runtime profiles after navigation so generated cases can use real DOM evidence instead of static JSP guesses.
- Provides a manual takeover mode for legacy systems that require human login, menu expansion, session preparation, or business data setup.

## Architecture

The workflow has four main stages:

1. `src/jsp_scanner.py` scans JSP files and exports structural UI mappings.
2. `src/page_mapping.py` compares legacy and migrated mappings and produces page-level risk data.
3. `src/checklist_generator.py` and `src/page_case_planner.py` convert mappings and runtime profiles into executable checklists.
4. `src/regression_engine.py`, `src/action_executor.py`, and the route tools run Playwright-based regression checks and produce evidence reports.

The toolkit intentionally keeps Japanese UI labels and validation text in parser rules and test fixtures because many target systems are Japanese enterprise applications. English is used for documentation, comments, CLI help, diagnostics, and generated report labels.

## Repository Layout

```text
src/
  action_executor.py              Semantic Playwright action layer
  checklist_generator.py          Markdown and Excel checklist generation
  jsp_scanner.py                  JSP and Struts tag scanner
  page_case_planner.py            Page-profile based case planning
  page_mapping.py                 Legacy/new page comparison
  regression_engine.py            End-to-end regression runner
  route_catalog.py                Static candidate route generation
  route_map_runner.py             Browser route validation and recording
  route_runtime_verifier.py       Runtime route verification utilities
  runtime_page_profile.py         Live DOM profiling from Playwright pages
tests/                            Unit and integration tests
test_data/                        Local test fixtures
guided_checklist_standard_example.json
```

Generated artifacts are intentionally ignored by Git:

- `generated/`
- `mappings/`
- `output/`
- `test_data/upload/`
- `.env`

## Setup

Create a virtual environment and install the dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install
```

Create a local `.env` file when running against real systems:

```ini
LEGACY_URL=https://legacy.example.com
NEW_URL=https://new.example.com

TEST_USERNAME=your_username
TEST_PASSWORD=your_password

# Optional: multiple login entries can be selected at runtime.
LOGIN_ENTRY_NAMES=dev-a,dev-b
LEGACY_URLS=https://legacy-a.example.com,https://legacy-b.example.com
NEW_URLS=https://new-a.example.com,https://new-b.example.com
LOGIN_ENTRY=dev-a

# Optional: custom browser and download location.
CHROME_PORTABLE_PATH=/path/to/chrome.exe
DOWNLOAD_DIR=~/Downloads
```

## Basic Workflow

Scan legacy and migrated JSP trees:

```bash
python src/jsp_scanner.py /path/to/legacy/jsp -o mappings/legacy_elements.json
python src/jsp_scanner.py /path/to/new/jsp -o mappings/new_elements.json
```

Build a page mapping:

```bash
python src/page_mapping.py mappings/legacy_elements.json mappings/new_elements.json \
  -o mappings/page_diff.json \
  --md generated/comparison_summary.md
```

Generate an Excel checklist:

```bash
python src/checklist_generator.py mappings/page_diff.json -o generated/migration_checklist.xlsx
```

Run regression tests:

```bash
pytest tests/test_migration.py \
  --run-migration \
  --test-browser=chrome_port \
  --checklist-path=generated/migration_checklist.xlsx \
  --html=output/regression_report.html
```

Run a focused page regression:

```bash
pytest tests/test_migration.py \
  --run-migration \
  --test-browser=chrome_port \
  --target-page=ExamplePage.jsp \
  --checklist-path=generated/migration_checklist.xlsx \
  --force-route-map \
  --route-map-path=generated/valid/route \
  --html=output/ExamplePage_report.html
```

## Route Mapping

Some legacy applications cannot open target JSP pages directly because they depend on frames, menu state, session state, or server-side transition actions. In that case, generate and validate route maps.

Generate candidate routes:

```bash
python -m src.route_catalog \
  --target ExamplePage.jsp \
  --output generated/valid/route/route_candidates_ExamplePage.json \
  --limit-per-target 5
```

Validate a route with browser evidence:

```bash
python -m src.route_map_runner \
  --candidates generated/valid/route/route_candidates_ExamplePage.json \
  --target ExamplePage.jsp \
  --output generated/valid/route/usable_route_map_legacy_ExamplePage.json \
  --capture-dir output/route_map/ExamplePage_legacy \
  --side legacy \
  --manual-data
```

When automatic navigation is blocked, `--manual-data` lets an operator complete the required business steps in the browser. The tool records replayable manual actions and stores them in the route map.

## Safety Defaults

Moonlight is conservative by default:

- Semi-automated cases are skipped unless `--include-semi-auto` is provided.
- Destructive create/update/delete cases are skipped unless `--include-destructive` is provided.
- Negative or error-injection cases are skipped unless `--include-negative` is provided.
- Browser evidence, downloaded files, generated reports, and environment-specific mappings are ignored by Git.

## Testing

Run the unit test suite:

```bash
pytest
```

Compile-check the source files:

```bash
python -m py_compile src/*.py src/utils/*.py
```

## Portfolio Notes

This project demonstrates migration-focused automation rather than a toy Selenium script. The interesting parts are the page-structure scanner, capability-based case planner, semantic action executor, route-map navigation, runtime DOM profiling, and report diagnostics around blocked legacy flows.
