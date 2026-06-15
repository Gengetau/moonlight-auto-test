---
name: patlics-edge-migration-test
description: Compare PATLICS pages between the legacy .47 environment and migrated .192 environment in Microsoft Edge. Use when testing popup pages or related workflows for strict migration parity, including screenshots, DOM and controls, static resources, print/PDF output, file downloads, condition matrices, accepted server/session differences, and reusable HTML/JSON evidence reports.
---

# PATLICS Edge Migration Test

Use the repository venv and the user's existing Edge debug session to compare `.47` and `.192`.
Treat `.47` as the baseline and require `.192` to match unless the user explicitly accepts a migration difference.

## Prepare

1. Work from the `moonlight-auto-test` repository root.
2. Use `.\venv\Scripts\python.exe`; do not assume activation in another PowerShell is inherited.
3. Connect to Microsoft Edge at `http://127.0.0.1:9222`.
4. Preserve the user's login state and open pages. Do not reload or close business pages unless required.
5. Ask the user to open both target pages only when they cannot be reached from the current parent pages.
6. Write evidence under `output/edge_compare/<PageName>_edge/`.

Read [references/workflow.md](references/workflow.md) before executing a new page test.
Read [references/accepted-differences.md](references/accepted-differences.md) before changing a result from `DIFF` to `PASS`.
Use [references/checklist.md](references/checklist.md) when reporting completion.

## Choose The Test

- Run `page` for screenshot, DOM, controls, tables, metrics, and static resource comparison.
- Run `pdf` when buttons open print or print-preview behavior and a saved PDF must be compared.
- Run `download` for one default `GazetteChooseSaveDoc.do` download.
- Run `download-matrix` for the six verified `CURRENT/ALL` and kind `10/20` combinations.
- Extend the bundled script only when a new page has different selectors, popup opening logic, or condition semantics.

## Run

Resolve this Skill directory and invoke `scripts/run.py`.

```powershell
.\venv\Scripts\python.exe .codex\skills\patlics-edge-migration-test\scripts\run.py page `
  --path GazetteChooseSaveDoc.do `
  --output-dir output/edge_compare/GazetteChooseSaveDoc_edge `
  --template-dir C:\work\note\PageTest\6_12
```

```powershell
.\venv\Scripts\python.exe .codex\skills\patlics-edge-migration-test\scripts\run.py pdf `
  --path JpGazetteTextprint.do `
  --output-dir output/edge_compare/JpGazetteTextprint_edge
```

```powershell
.\venv\Scripts\python.exe .codex\skills\patlics-edge-migration-test\scripts\run.py download-matrix `
  --output-dir output/edge_compare/GazetteChooseSaveDoc_edge `
  --download-timeout-ms 120000
```

Override `--legacy-host`, `--new-host`, or `--cdp` only when the user supplies different environments.

## Judge Results

1. Keep both raw and normalized results.
2. Require screenshot equality, normalized DOM/control/table equality, and matching static resource content for a page PASS.
3. Compare PDF filename, size, and normalized PDF SHA-256. Ignore only PDF creation/modification timestamps.
4. Normalize the 14-digit timestamp in generated ZIP names, then compare size and every ZIP entry by filename, uncompressed size, CRC, and SHA-256.
5. Treat an expected no-download case as PASS only when both environments return the same normalized response and expected message.
6. Never whitelist a new difference solely because it appears environment-dependent. Record evidence and obtain user acceptance first.
7. Preserve `raw_status=DIFF` when normalization or an accepted difference is required for effective PASS.

## Finish

Open or link the generated HTML report, summarize every tested condition, and state:

- effective status;
- raw differences;
- accepted differences;
- filenames and sizes;
- content/hash comparison;
- blocked or untested conditions.

Do not report full completion when only the default condition was tested.
