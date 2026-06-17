---
name: patlics-edge-migration-test
description: Compare PATLICS pages between the legacy .47 environment and migrated .192 environment in Microsoft Edge. Use when testing popup pages or related workflows for strict migration parity, including screenshots, DOM and controls, static resources, print/PDF output, file downloads, condition matrices, accepted server/session differences, and reusable HTML/JSON evidence reports.
---

# PATLICS Edge Migration Test

Use the repository venv and the user's existing Edge debug session to compare `.47` and `.192`.
Treat `.47` as the baseline and require `.192` to match unless the user explicitly accepts a migration difference.

## Prepare

1. Work from the `moonlight-auto-test` repository root.
2. Use the repository venv only after activating it in the same shell command that runs `python`.
   Do not call `.\venv\Scripts\python.exe` directly.
   Do not run `.\venv\Scripts\activate` and `python ...` as separate Codex commands, because activation state does not carry across independent shells.
3. Connect to Microsoft Edge at `http://127.0.0.1:9222`.
4. Preserve the user's login state and open pages. Do not reload or close business pages unless required.
5. Ask the user to open both target pages only when they cannot be reached from the current parent pages.
6. Write evidence under `output/edge_compare/<PageName>_edge/`.

Read [references/workflow.md](references/workflow.md) before executing a new page test.
Read [references/accepted-differences.md](references/accepted-differences.md) before changing a result from `DIFF` to `PASS`.
Read [references/interaction-behavior.md](references/interaction-behavior.md) before running or judging interaction tests.
Use [references/checklist.md](references/checklist.md) when reporting completion.

## Choose The Test

- Run `page` for screenshot, DOM, controls, tables, metrics, and static resource comparison.
- Run `pdf` when buttons open print or print-preview behavior and a saved PDF must be compared.
- Run `download` for one default `GazetteChooseSaveDoc.do` download.
- Run `download-matrix` for the six verified `CURRENT/ALL` and kind `10/20` combinations.
- Run `interaction` to enumerate visible actionable elements, execute safe page interactions on both environments, and compare before/after behavior evidence.
- Extend the bundled script only when a new page has different selectors, popup opening logic, or condition semantics.

## Run

Resolve this Skill directory and invoke `scripts/run.py` through an activated venv in the same PowerShell command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command ". .\venv\Scripts\Activate.ps1; python .codex\skills\patlics-edge-migration-test\scripts\run.py page `
  --path GazetteChooseSaveDoc.do `
  --output-dir output/edge_compare/GazetteChooseSaveDoc_edge `
  --template-dir C:\work\note\PageTest\6_12"
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command ". .\venv\Scripts\Activate.ps1; python .codex\skills\patlics-edge-migration-test\scripts\run.py pdf --path JpGazetteTextprint.do --output-dir output/edge_compare/JpGazetteTextprint_edge"
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command ". .\venv\Scripts\Activate.ps1; python .codex\skills\patlics-edge-migration-test\scripts\run.py download-matrix --output-dir output/edge_compare/GazetteChooseSaveDoc_edge --download-timeout-ms 120000"
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command ". .\venv\Scripts\Activate.ps1; python .codex\skills\patlics-edge-migration-test\scripts\run.py interaction --path WwAbstList --output-dir output/edge_compare/WwAbstList_edge --max-cases 80"
```

If `Activate.ps1` is blocked by PowerShell policy, retry with `cmd` and `activate.bat` in the same command:

```cmd
cmd /c "call .\venv\Scripts\activate.bat && python .codex\skills\patlics-edge-migration-test\scripts\run.py page --path GazetteChooseSaveDoc.do --output-dir output/edge_compare/GazetteChooseSaveDoc_edge"
```

Do not rewrite the workflow in Node/CDP or use a Node fallback unless both `Activate.ps1` and `activate.bat` fail and the user explicitly permits a fallback.
When venv activation or Python execution fails, stop and report:

1. current working directory;
2. the venv activation command used;
3. `where python`;
4. `python --version`;
5. the exact error output.

Override `--legacy-host`, `--new-host`, or `--cdp` only when the user supplies different environments.

## Judge Results

1. Keep both raw and normalized results.
2. Require screenshot equality, normalized DOM/control/table equality, and matching static resource content for a page PASS.
3. Compare PDF filename, size, header, and normalized PDF SHA-256. The accepted biz-Stream upgrade difference may normalize the Producer/version, creation/modification timestamps, generated PDF ID, and a minor byte-size delta up to 4 bytes when the filename, header, print-preview behavior, and workflow match. Keep the raw size/hash difference visible as evidence.
4. Normalize the 14-digit timestamp in generated ZIP names, then compare size and every ZIP entry by filename, uncompressed size, CRC, and SHA-256.
5. Treat an expected no-download case as PASS only when both environments return the same normalized response and expected message.
6. Never whitelist a new difference solely because it appears environment-dependent. Record evidence and obtain user acceptance first.
7. Preserve `raw_status=DIFF` when normalization or an accepted difference is required for effective PASS.
8. For interaction tests, classify the control behavior before judging the result. Compare the correct post-action target: original page, new tab/window, parent list page, download artifact, PDF artifact, or closure state.
9. Do not skip download, print, close, help, filter/list-focus, check-all, select, checkbox, or row-link interactions merely because they navigate, open a window, or close the target page. Test them with the behavior-specific oracle in `interaction-behavior.md`.
10. Skip or dry-run only server-side data-mutating operations by default, including delete, register, update, submit/send, confirm, execute, and save actions. Report skipped cases explicitly.

## Finish

Open or link the generated HTML report, summarize every tested condition, and state:

- effective status;
- raw differences;
- accepted differences;
- filenames and sizes;
- content/hash comparison;
- blocked or untested conditions.

Do not report full completion when only the default condition was tested.
