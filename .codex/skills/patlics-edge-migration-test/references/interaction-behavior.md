# Interaction Behavior Rules

Use these rules when testing page interactions. Do not judge every click by comparing only the original target page after the click.

## Core Principle

First understand the intended behavior of the control, then compare the resulting state on `.47` and `.192`.
Screenshots of the original page can look identical while the interaction result differs, and conversely the original page can change or close because that is the expected behavior.

## Must-Test Interactions

Do not skip these as dangerous by default:

- Download/output buttons such as `ダウンロード` or `出力`.
- Print buttons such as `印刷`.
- Close buttons such as `閉じる`.
- Help links such as `ヘルプ`.
- Filter/list-focus buttons such as `絞込`.
- Check all / clear all links such as `全チェック` and `全チェック解除`.
- Select boxes and checkbox/radio state changes.

Only skip or dry-run operations that can mutate server-side business data, such as delete, register, update, submit/send, confirm, execute, or save.

## Behavior-Specific Oracles

### Help / Navigation Links

If a link opens a new tab/window or navigates away, compare the resulting destination instead of the original page:

- opened/new page count;
- destination URL after accepted host/session normalization;
- title;
- visible text;
- DOM/control snapshot;
- screenshot;
- network status and console errors.

For `ヘルプ` in simple analysis pages, the expected behavior is opening a help destination from the parent/entry context. Treat it as navigation/new-window behavior, not as a failure because the original analysis page remains unchanged.

### Filter / List-Focus Actions

If an action such as `絞込` closes the analysis popup/page and returns results to the entry list page, compare the entry/list page after the action:

- parent `frMain` URL;
- visible list text/table rows;
- selected/filter state;
- form values and visible controls;
- screenshot of the list page;
- network requests produced by the action.

Do not mark this DIFF merely because the target analysis page closed.

### Download / Print

For download or print buttons, route to the existing download/PDF comparison logic or capture equivalent artifacts:

- download filename, size, and normalized content;
- print-preview/PDF open result;
- PDF filename, size, header, raw hash, and normalized hash.

Do not skip these controls in interaction coverage. If the generic interaction runner cannot safely capture the artifact, mark the case `BLOCKED` with the missing artifact reason rather than `SKIPPED`.

### Close

For close buttons, compare closure behavior:

- both environments close the same target page/popup, or both remain open;
- focus returns to the same parent/entry page;
- parent page URL/title/visible state remains equivalent.

Do not treat the target page being closed as an automation failure when the control is `閉じる`.

### State-Only Controls

For checkboxes, radio buttons, select boxes, and text input:

- compare the relevant changed control value/state;
- compare dependent visible text/table/control state;
- ignore unrelated hidden/session/token values using accepted differences.

Avoid declaring DIFF solely because full-form hidden values changed if the visible behavior and relevant control state match.

## Reporting

For each case, record:

- detected behavior type;
- element selector and text/value;
- action performed;
- comparison target used after the action: original page, new page, parent/list page, download artifact, PDF artifact, or closure state;
- raw differences;
- accepted differences;
- final status.
