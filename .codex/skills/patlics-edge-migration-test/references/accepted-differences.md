# Accepted Differences

Apply only differences explicitly accepted by the user or listed here from prior acceptance.
Keep the raw difference visible in JSON and the HTML accepted-difference section.

## Already Accepted

1. A form on `.192` may add `id` equal to its existing `name`, while `.47` has only `name`.
   Examples: `fmMain`, `fmChooseDoc`.
2. Hidden `userId` values may differ because sessions and servers differ.
3. Static resource paths may contain different server-generated `pattest/<number>/` directories when fetched bytes and hashes are equal.
4. Generated ZIP filenames may differ only by the 14-digit timestamp.
5. Raw ZIP SHA-256 may differ when every normalized ZIP entry matches.
6. Raw PDF SHA-256 may differ only because `/CreationDate` or `/ModDate` differs.
7. HTTP response headers such as `Date`, Apache version, and `.192`-only `Content-Language` may differ when status, content type, and normalized body behavior match.

## Not Automatically Accepted

- Missing or extra visible controls, text, rows, columns, buttons, or links.
- Different checked, selected, disabled, or default states.
- Layout, clipping, font, spacing, color, size, or screenshot differences.
- Different static resource bytes.
- Different PDF or ZIP payload content.
- One environment downloads while the other reports no file.
- Different business messages or HTTP statuses.
- New IDs, hidden values, paths, or headers not covered above.

For a new difference, present both values, explain the suspected cause, and wait for explicit user acceptance before changing effective status to PASS.
