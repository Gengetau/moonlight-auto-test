# Accepted Differences

Apply only differences explicitly accepted by the user or listed here from prior acceptance.
Keep the raw difference visible in JSON and the HTML accepted-difference section.

## Already Accepted

1. A form or named form control on `.192` may add `id` equal to its existing `name`, while `.47` has only `name`.
   Examples: `fmMain`, `fmChooseDoc`, `actionMode`, `dispCnt`.
   Selector/path differences caused only by these added IDs are also accepted.
2. Hidden `userId` values may differ because sessions and servers differ.
3. Static resource paths may contain different server-generated `pattest/<number>/` directories when fetched bytes and hashes are equal.
4. Generated ZIP filenames may differ only by the 14-digit timestamp.
5. Raw ZIP SHA-256 may differ when every normalized ZIP entry matches.
6. Raw PDF SHA-256 may differ only because `/CreationDate` or `/ModDate` differs.
7. HTTP response headers such as `Date`, Apache version, and `.192`-only `Content-Language` may differ when status, content type, and normalized body behavior match.
8. PDF generator metadata may differ because the migrated server upgraded BrainSellers biz-Stream.
   Accepted values include `.47` version `5.0.0` and `.192` version `5.2.0`, plus generated `/CreationDate`, `/ModDate`, and `/ID` values.
   When the PDF filename, header, print-preview behavior, and generated-page workflow match, a minor byte-size delta of up to 4 bytes is accepted as a biz-Stream generation difference even when the raw and normalized PDF SHA-256 values differ.
   Keep the raw size/hash difference visible in evidence and report the effective status as PASS only through this accepted difference.
9. Interaction evidence may normalize session, `userId`, token, nonce, CSRF, and 14-digit timestamp values when comparing URLs, form values, dialogs, network records, and snapshots.
   The surrounding behavior, request method/status, visible text, control state, and non-dynamic field values must still match.

## Not Automatically Accepted

- Missing or extra visible controls, text, rows, columns, buttons, or links.
- Different checked, selected, disabled, or default states.
- Layout, clipping, font, spacing, color, size, or screenshot differences.
- Different static resource bytes.
- Different PDF or ZIP payload content.
- One environment downloads while the other reports no file.
- Different business messages or HTTP statuses.
- New IDs, hidden values, paths, or headers not covered above.
- Different interaction outcomes after normalization, including mismatched alert messages, request destinations, methods/statuses, visible control states, or opened windows.

For a new difference, present both values, explain the suspected cause, and wait for explicit user acceptance before changing effective status to PASS.
