# JSP Scanner and Checklist Generator

`jsp_scanner.py` scans legacy JSP pages and exports UI elements as JSON mappings for Moonlight automation.
`checklist_generator.py` turns that JSON into a Markdown or Excel test recommendation report.

## Logic

The scanner combines two parsing strategies:

- `BeautifulSoup` parses tolerant HTML/JSP fragments and normalizes regular tags.
- Regular expressions preserve Struts tags such as `html:file` and `html:link`, capture raw source snippets, and calculate source line numbers.

It currently extracts:

- `form` and `html:form` as `kind: form`
- `html:file` as `kind: file`
- `input type="button"` as `kind: button`
- `html:link` as `kind: link`

Each record includes the tag name, source line, parsed attributes, a best-effort locator, and an action hint such as `submit`, `upload`, `click`, or `navigate`.
`action_executor.py` consumes those hints through the semantic execution layer, so migration tests can execute the intended behavior even when the raw locator action would otherwise be ambiguous.

## Contribution to Moonlight

Moonlight compares behavior between the legacy Struts application and the migrated Spring application. The scanner turns legacy JSP source into structured mapping data, which can be used to:

- seed UI locator mappings under `mappings/`
- generate migration checklist data under `generated/`
- identify form, upload, button, and link coverage gaps before Playwright tests are written

This reduces manual inspection of JSP pages and gives the test executor a stable JSON bridge from legacy UI source to automated regression scenarios.

The regression executor now captures evidence from the detected business frame rather than blindly using the top-level page. HTML reports include target frame metadata, wait diagnostics, action type, locator pairs, and blocked reasons for each case.

## Remote Collaboration Flow

When Mika cannot transfer JSP source code into this environment, run the scanner and generator inside the company environment. Only the generated report needs to be shared back.

### 1. Prepare dependencies

```bash
pip install -r requirements.txt
```

If company policy blocks package installation, install at least:

```bash
pip install beautifulsoup4 openpyxl
```

`beautifulsoup4` is needed by the scanner. `openpyxl` is only needed when generating `.xlsx`.

### 2. Scan JSP files

Scan one JSP file:

```bash
python src/jsp_scanner.py path/to/page.jsp -o generated/elements.json
```

Scan a JSP directory:

```bash
python src/jsp_scanner.py path/to/jsp/root -o generated/elements.json
```

The JSON contains only structural UI mapping data: source path, line number, tag type, attributes, best-effort locator, and action hint. Review it before sharing if source paths or attributes are sensitive.

### 3. Generate the test recommendation report

Generate Markdown:

```bash
python src/checklist_generator.py generated/elements.json -o generated/test_recommendations.md
```

Generate Excel:

```bash
python src/checklist_generator.py generated/elements.json -o generated/test_recommendations.xlsx
```

Markdown is the safest format for quick review in chat or Git. Excel is better when Mika wants to filter by page, severity, element type, or locator.

### 4. Execute tests manually or convert to automation

Use the report in this order:

1. Start with `主祭优先执行清单`; it lists the highest-risk checks first.
2. For every `file` element, verify allowed file type, disguised file type, oversized upload, and dangerous file names.
3. For every `form` element, verify normal submit, required fields, boundary text, and XSS probes.
4. For every `button` element, verify main click behavior, duplicate-click protection, and JavaScript console errors.
5. For every `link` element, verify navigation, parameter tampering, permission checks, refresh, and browser back behavior.

The generated report is a testing guide, not a proof of coverage. Mika should mark each row with result, evidence screenshot, ticket number, or automation status inside the company environment.

## Usage

Install dependencies:

```bash
pip install -r requirements.txt
```

Scan one JSP file and print JSON:

```bash
python src/jsp_scanner.py path/to/page.jsp
```

Scan a directory and write JSON:

```bash
python src/jsp_scanner.py path/to/jsp/root -o generated/jsp_scan.json
```

Generate a Markdown checklist:

```bash
python src/checklist_generator.py generated/jsp_scan.json -o generated/test_recommendations.md
```

Generate an Excel checklist:

```bash
python src/checklist_generator.py generated/jsp_scan.json -o generated/test_recommendations.xlsx
```

The output shape is:

```json
{
  "root": "path/to/jsp/root",
  "totals": {
    "form": 1,
    "file": 1,
    "button": 1,
    "link": 1
  },
  "pages": [
    {
      "source": "path/to/page.jsp",
      "counts": {},
      "elements": []
    }
  ]
}
```
