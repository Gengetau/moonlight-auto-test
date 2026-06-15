# Execution Workflow

## 1. Confirm Preconditions

- Confirm `.\venv\Scripts\python.exe --version`.
- Confirm `http://127.0.0.1:9222/json/version` reports Microsoft Edge.
- Enumerate `/json/list` and verify both hosts have the expected parent or popup page.
- Use exact URL path fragments such as `JpGazetteTextprint.do` or `GazetteChooseSaveDoc.do`.

## 2. Establish Equivalent State

- Use the same account role, source document, selected record, scroll position, viewport, and popup size.
- Capture the applied state for selects, checkboxes, radio buttons, disabled controls, and hidden values.
- Reopen popups from their parent page after actions that close them.
- Prefer stable function or `onclick` selectors such as `fnSaveDocs` over localized visible text.

## 3. Compare The Page

Run `page` and retain:

- `.47` and `.192` screenshots;
- visual diff image and percentage;
- DOM and normalized DOM;
- controls, forms, tables, text, document metrics, and element rectangles;
- static resource URLs, lengths, and hashes;
- accepted-difference records;
- HTML and JSON reports.

Investigate any visual difference before normalizing DOM differences.

## 4. Compare Print/PDF

Run `pdf` for pages with `#print` and `#preview`.

- Exercise both buttons in both environments.
- Record whether Edge print preview targets opened.
- Save PDFs with CDP `Page.printToPDF`.
- Compare filename and byte size.
- Compare raw SHA-256 for evidence.
- Compare normalized SHA-256 after removing `/CreationDate` and `/ModDate`.
- Use normalized SHA-256 for effective content parity.

Do not claim that the native Save dialog itself was automated when CDP generated the PDF.

## 5. Compare Downloads

For `GazetteChooseSaveDoc.do`, run the matrix:

| Case | Target | Kinds | Expected |
|---|---|---|---|
| `current_default_10_20` | CURRENT | 10,20 | ZIP |
| `current_kind_10_only` | CURRENT | 10 | no file response |
| `current_kind_20_only` | CURRENT | 20 | ZIP |
| `all_default_10_20` | ALL | 10,20 | ZIP |
| `all_kind_10_only` | ALL | 10 | no file response |
| `all_kind_20_only` | ALL | 20 | ZIP |

The popup submits to `GazetteSaveDocs.do` through the parent page and then closes. Match download events by host plus endpoint, not by popup page identity.

For ZIP results, compare:

- timestamp-normalized filename;
- archive size;
- entry count and order;
- each entry filename as decoded by Python;
- uncompressed size;
- CRC;
- content SHA-256.

Raw ZIP hashes can differ because ZIP metadata contains timestamps.

For no-file results, require both sides to return HTTP 200, `text/html;charset=UTF-8`, the normalized same body, and `ファイルが存在しません。`.

## 6. Recover From Automation Problems

- If CDP connection hangs, close only stale `edge://downloads-hub/` or `edge://print` targets; preserve PATLICS pages.
- If the popup cannot be reopened, inspect the parent frame and locate the underlying JavaScript action.
- If a command times out, inspect partial downloads and JSON before rerunning.
- Run matrix cases individually with `--case <id> --append-existing` to retain completed results.
- Keep `.47` and `.192` runs sequential when a shared browser context could mix events.

## 7. Produce Evidence

Keep HTML as the human report and JSON as machine-readable evidence. Include absolute links to the report in the final response.
