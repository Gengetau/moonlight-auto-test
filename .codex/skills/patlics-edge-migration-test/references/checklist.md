# Completion Checklist

Use this checklist in the report or final summary.

| Category | Required evidence |
|---|---|
| Environment | Edge version, CDP endpoint, `.47` URL, `.192` URL |
| Preconditions | Same user role, source record, viewport, popup state |
| Visual | Both screenshots, diff image, diff percentage |
| Structure | DOM, controls, forms, tables, metrics |
| Resources | Resource count, URL mapping, size, SHA-256 |
| Allowed differences | Raw values, reason, explicit acceptance |
| PDF | Button, filename, size, raw hash, normalized hash |
| Download | Condition, result type, normalized filename, size |
| ZIP | Entry count, entry filenames, sizes, CRC, SHA-256 |
| No download | HTTP status, content type, normalized body, message |
| Output | HTML report, JSON result, downloaded artifacts |

Use these result labels:

- `PASS`: effective behavior and content match.
- `DIFF`: an unaccepted or material difference remains.
- `BLOCKED`: required evidence could not be collected.
- `NO_DOWNLOAD`: expected no-file response collected; compare both sides before effective PASS.

Always state coverage explicitly, for example: `6/6 conditions tested`.
