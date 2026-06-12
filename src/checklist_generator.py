import argparse
import html
import json
import sys
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Allows both `python -m src.checklist_generator` and direct `python src/checklist_generator.py`.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.coverage_policy import CHECKLIST_SECTIONS, CASE_DEPTH  # noqa: E402
from src.page_aliases import page_aliases  # noqa: E402
try:  # noqa: E402
    from src.page_case_planner import PageCasePlanner
except ImportError:  # pragma: no cover - direct script execution fallback
    from page_case_planner import PageCasePlanner  # type: ignore
try:  # noqa: E402
    from src.page_evidence_builder import PageEvidenceBuilder
    from src.page_spec_checklist_generator import page_spec_to_cases, page_spec_to_profile
    from src.page_spec_generator import (
        PageSpecError,
        page_evidence_cache_path,
        load_cached_page_spec,
        page_spec_prompt_cache_path,
        page_spec_cache_path,
        write_page_evidence,
        write_page_spec_prompt,
    )
except ImportError:  # pragma: no cover - direct script execution fallback
    from page_evidence_builder import PageEvidenceBuilder  # type: ignore
    from page_spec_checklist_generator import page_spec_to_cases, page_spec_to_profile  # type: ignore
    from page_spec_generator import (  # type: ignore
        PageSpecError,
        page_evidence_cache_path,
        load_cached_page_spec,
        page_spec_prompt_cache_path,
        page_spec_cache_path,
        write_page_evidence,
        write_page_spec_prompt,
    )


SEVERITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}
CASE_GENERATING_KINDS = {"page", "form", "field", "select", "textarea", "hidden", "file", "button", "link", "scenario"}
FIELD_LIKE_KINDS = {"field", "select", "textarea"}
AUTO_ACTION_TYPES = {
    "snapshot",
    "wait",
    "click",
    "submit",
    "navigate",
    "download",
    "upload",
    "upload_submit",
    "fill",
    "select",
    "clear",
    "set_value",
    "press",
    "close_window",
}
RUNTIME_PROFILE_SCHEMA = "moonlight.runtime_page_profile.v1"
DEFAULT_RUNTIME_PROFILE_DIR = Path("generated/valid/runtime_profile")
DEFAULT_PAGE_SPEC_DIR = Path("generated/valid/page_specs")


@dataclass(frozen=True)
class TestCase:
    # Positional arguments are intentionally ordered as:
    # title, objective, steps, expected, severity.
    # Page/element metadata is supplied by **fields in the case builders.
    title: str
    objective: str
    steps: str
    expected: str
    severity: str
    page: str
    kind: str
    locator: str
    line: str
    evidence: str
    automation_mode: str = "manual/assist"
    case_type: str = ""
    action_type: str = ""
    test_data: str = ""
    submit_locator: str = ""
    expected_type: str = ""
    expected_value: str = ""
    pre_steps: str = ""
    main_step: str = ""
    case_id: str = ""
    parent_case_id: str = ""
    viewpoint_id: str = ""
    enabled: str = "true"
    generated_by: str = ""
    matched_capabilities: str = ""
    destructive: str = "false"
    priority: str = ""


def as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def as_json(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return as_text(value)


def first_attr(attributes: Dict[str, Any], *names: str) -> str:
    lowered = {str(key).lower(): value for key, value in attributes.items()}
    for name in names:
        if name in attributes:
            return as_text(attributes[name])
        value = lowered.get(name.lower())
        if value is not None:
            return as_text(value)
    return ""


def page_entries(scan_data: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    if scan_data.get("schema") == RUNTIME_PROFILE_SCHEMA or isinstance(scan_data.get("controls"), list):
        yield scan_data
        return
    if isinstance(scan_data.get("runtime_profiles"), list):
        yield from scan_data["runtime_profiles"]
        return
    if "pages" in scan_data:
        yield from scan_data.get("pages", [])
        return
    if isinstance(scan_data.get("page_mappings"), list):
        yield from scan_data["page_mappings"]
        return
    yielded_mapping_subset = False
    for key in ["high_risk_pages", "medium_risk_pages"]:
        if isinstance(scan_data.get(key), list):
            yield from scan_data[key]
            yielded_mapping_subset = True
    if yielded_mapping_subset:
        return
    yield {
        "source": scan_data.get("source", scan_data.get("root", "<unknown>")),
        "counts": scan_data.get("counts", {}),
        "elements": scan_data.get("elements", []),
    }


def page_name_of(page: Dict[str, Any]) -> str:
    return as_text(
        page.get("page_id")
        or page.get("target_page_name")
        or page.get("target_page")
        or page.get("source")
        or page.get("legacy_sources", [None])[0],
        "<unknown>",
    )


def page_match_keys(value: Any) -> set:
    text = as_text(value).replace("\\", "/").strip().strip("'\"").lower()
    if not text:
        return set()
    leaf = text.rsplit("/", 1)[-1]
    stem = leaf.rsplit(".", 1)[0] if "." in leaf else leaf
    return {text, leaf, stem} | page_aliases(value)


def parse_target_pages(values: Optional[Sequence[str]]) -> List[str]:
    targets: List[str] = []
    for value in values or []:
        for part in as_text(value).replace("\n", ",").split(","):
            cleaned = part.strip()
            if cleaned:
                targets.append(cleaned)
    return targets


def _page_values_for_match(page: Dict[str, Any]) -> List[Any]:
    values: List[Any] = [
        page.get("page_id"),
        page.get("target_page_name"),
        page.get("target_page"),
        page.get("source"),
        page.get("legacy_page"),
        page.get("new_page"),
        page.get("view_page"),
    ]
    for key in ("legacy_sources", "new_sources"):
        source = page.get(key)
        if isinstance(source, list):
            values.extend(source)
    return values


def page_matches_targets(page: Dict[str, Any], target_pages: Optional[Sequence[str]]) -> bool:
    targets = parse_target_pages(target_pages)
    if not targets:
        return True
    page_keys = set()
    for value in _page_values_for_match(page):
        page_keys.update(page_match_keys(value))
    page_keys.update(page_match_keys(page_name_of(page)))
    target_keys = set()
    for target in targets:
        target_keys.update(page_match_keys(target))
    return bool(page_keys.intersection(target_keys))


def runtime_profile_paths(search_dir: Path = DEFAULT_RUNTIME_PROFILE_DIR) -> List[Path]:
    if not search_dir.exists():
        return []
    return sorted(
        (path for path in search_dir.rglob("*.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def load_runtime_profiles(search_dir: Path = DEFAULT_RUNTIME_PROFILE_DIR) -> List[Dict[str, Any]]:
    profiles: List[Dict[str, Any]] = []
    for path in runtime_profile_paths(search_dir):
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(profile, dict):
            continue
        if profile.get("schema") != RUNTIME_PROFILE_SCHEMA and not isinstance(profile.get("controls"), list):
            continue
        profile.setdefault("runtime_profile_path", str(path))
        profile["_runtime_profile_mtime"] = path.stat().st_mtime
        profiles.append(profile)
    return profiles


def select_runtime_profile_for_page(page: Dict[str, Any], profiles: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    target_keys = set()
    for value in _page_values_for_match(page):
        target_keys.update(page_match_keys(value))
    if not target_keys:
        return None

    best: Optional[Dict[str, Any]] = None
    best_score = -1
    for profile in profiles:
        profile_keys = set()
        for value in _page_values_for_match(profile):
            profile_keys.update(page_match_keys(value))
        if not target_keys.intersection(profile_keys):
            continue
        controls = profile.get("controls") if isinstance(profile.get("controls"), list) else []
        score = len(controls) + int(float(profile.get("_runtime_profile_mtime") or 0) % 100000)
        if page_match_keys(page_name_of(page)).intersection(profile_keys):
            score += 1000000
        if score > best_score:
            best = profile
            best_score = score
    if best is None:
        return None
    selected = dict(best)
    selected.setdefault("static_page_id", page_name_of(page))
    selected["profile_source"] = "runtime_profile"
    return selected


def runtime_profile_identity(profile: Dict[str, Any]) -> str:
    path = as_text(profile.get("runtime_profile_path"))
    if path:
        try:
            return str(Path(path).resolve()).lower()
        except OSError:
            return path.replace("\\", "/").lower()
    return "|".join(
        as_text(profile.get(key)).lower()
        for key in ("side", "login_entry", "route_id", "target_page", "target_page_name", "page_id")
    )


def runtime_profile_page_input(profile: Dict[str, Any]) -> Dict[str, Any]:
    page = dict(profile)
    page.setdefault("profile_source", "runtime_profile")
    page.setdefault("page_id", page.get("target_page") or page.get("target_page_name") or page.get("source"))
    return page


def page_risk(page: Dict[str, Any]) -> str:
    risk = as_text(page.get("risk"), "Medium")
    return risk if risk in CASE_DEPTH else "Medium"


def case_depth(page: Dict[str, Any]) -> str:
    return CASE_DEPTH.get(page_risk(page), "standard")


def take_by_depth(cases: List[TestCase], depth: str) -> List[TestCase]:
    if depth == "full":
        return cases
    if depth == "standard":
        return [case for case in cases if case.severity in {"High", "Medium"}]
    # smoke
    high = [case for case in cases if case.severity == "High"]
    return high[: max(1, min(3, len(high)))]


def element_attributes(element: Dict[str, Any]) -> Dict[str, Any]:
    attrs = element.get("attributes")
    if isinstance(attrs, dict):
        return attrs
    return {}


def element_field_name(element: Dict[str, Any]) -> str:
    if element.get("field_name"):
        return as_text(element.get("field_name"))
    attrs = element_attributes(element)
    kind = as_text(element.get("kind")).lower()
    tag = as_text(element.get("tag")).lower()

    if kind == "file" or tag == "html:file":
        return first_attr(attrs, "property", "path", "name", "id", "styleId")
    if tag.startswith("html:"):
        return first_attr(attrs, "property", "name", "id", "styleId")
    if tag.startswith("form:"):
        return first_attr(attrs, "path", "name", "id", "modelAttribute", "commandName")
    return first_attr(attrs, "id", "styleId", "name", "property", "path", "value", "title", "href", "action", "modelAttribute", "commandName")


def element_label(element: Dict[str, Any]) -> str:
    return (
        as_text(element.get("label"))
        or element_field_name(element)
        or as_text(element.get("semantic_key"))
        or as_text(element.get("key"))
        or as_text(element.get("legacy_locator") or element.get("new_locator") or element.get("locator"))
        or as_text(element.get("tag"), "unknown")
    )


def normalized_locator(element: Dict[str, Any]) -> str:
    kind = as_text(element.get("kind")).lower()
    tag = as_text(element.get("tag")).lower()
    attrs = element_attributes(element)
    if kind == "file" or tag == "html:file":
        field = first_attr(attrs, "property", "path", "name", "id") or element_field_name(element)
        if field:
            return f"input[name='{field}']"
    main_step = element.get("main_step") or {}
    return as_text(
        element.get("locator")
        or element.get("legacy_locator")
        or element.get("new_locator")
        or main_step.get("legacy_locator")
        or main_step.get("locator"),
        "(locator missing)",
    )


def element_evidence(element: Dict[str, Any]) -> str:
    raw = as_text(element.get("raw"))
    attrs = element_attributes(element)
    parts = []
    if raw:
        parts.append(raw)
    elif attrs:
        parts.append(", ".join(f"{key}={as_text(value)}" for key, value in sorted(attrs.items())))
    for key in ("key", "semantic_key", "legacy_locator", "new_locator", "action", "action_type"):
        if element.get(key):
            parts.append(f"{key}={as_text(element.get(key))}")
    return "\n".join(parts) if parts else as_text(element.get("tag"), "")


def field_summary(fields: Sequence[Dict[str, Any]]) -> str:
    summary = []
    for field in fields[:20]:
        label = element_label(field)
        line = as_text(field.get("line"), "-")
        locator = normalized_locator(field)
        summary.append(f"{as_text(field.get('tag'), 'field')} `{label}` line={line} locator={locator}")
    if len(fields) > 20:
        summary.append(f"... and {len(fields) - 20} more field(s)")
    return "; ".join(summary)


def common_element_fields(page: str, element: Dict[str, Any]) -> Dict[str, str]:
    evidence = element_evidence(element)
    related_fields = element.get("related_fields", [])
    if related_fields:
        evidence = f"{evidence}\nField completeness check: {field_summary(related_fields)}"
    return {
        "page": page,
        "kind": as_text(element.get("kind"), "unknown"),
        "locator": normalized_locator(element),
        "line": as_text(element.get("line"), "-"),
        "evidence": evidence,
    }


def attach_related_fields(elements: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach fields to nearest preceding form, but keep standalone field cases as well."""
    output: List[Dict[str, Any]] = []
    current_form: Optional[Dict[str, Any]] = None
    unassigned_fields: List[Dict[str, Any]] = []

    for element in elements:
        kind = as_text(element.get("kind")).lower()
        copied = dict(element)
        if kind in FIELD_LIKE_KINDS or kind == "hidden":
            field = dict(element)
            if current_form is None:
                unassigned_fields.append(field)
            else:
                current_form.setdefault("related_fields", []).append(field)
            output.append(copied)
            continue

        if kind == "form":
            copied["related_fields"] = []
            if unassigned_fields:
                copied["related_fields"].extend(unassigned_fields)
                unassigned_fields = []
            current_form = copied
        output.append(copied)

    return output


def button_semantic(element: Dict[str, Any]) -> str:
    attrs = element_attributes(element)
    onclick = first_attr(attrs, "onclick", "onClick").lower()
    action_type = as_text(element.get("action_type") or element.get("action_hint")).lower()
    if "window.close" in onclick or action_type == "close_window":
        return "close_window"
    if "download" in onclick or action_type == "download":
        return "download"
    if "fnsubmit" in onclick or ".submit" in onclick or action_type == "submit":
        return "submit"
    return action_type or "click"


def page_cases(page: Dict[str, Any]) -> List[TestCase]:
    page_name = page_name_of(page)
    counts = page.get("counts") or page.get("legacy_counts") or {}
    risk = page_risk(page)
    fields = {
        "page": page_name,
        "kind": "page",
        "locator": "-",
        "line": "-",
        "evidence": f"risk={risk}, counts={counts}, legacy_sources={page.get('legacy_sources')}, new_sources={page.get('new_sources')}",
    }
    cases = [
        TestCase("Initial page display", "Verify that the page opens successfully from the same business entry in Legacy and New.", "Open the target .do entry and check for blank pages, HTTP errors, permission errors, titles, headings, and primary text.", "Both environments display the target page without major missing content.", "High", **fields),
        TestCase("Layout difference check", "Compare page layout before and after migration.", "Capture screenshots under the same conditions and review the visual diff rate.", "No business-impacting layout differences are present, or acceptable differences are recorded.", "High", **fields),
        TestCase("DOM and copy difference check", "Verify that primary labels, button names, and explanatory text match after migration.", "Capture DOM text and compare Legacy/New differences.", "No unintended differences exist in business copy or messages.", "Medium", **fields),
        TestCase("Character encoding check", "Verify that Japanese text and symbols survive Windows-31J/UTF-8 conversion.", "Review Japanese, English, full-width, half-width, symbol, and newline text.", "No replacement glyphs, missing symbols, line-break regressions, or encoding corruption appear.", "High", **fields),
        TestCase("Back and reload behavior", "Verify that back/reload does not cause unsafe resubmission or error pages.", "Use back, forward, and reload after page display and inspect state and messages.", "State is restored safely without duplicate registration, duplicate submit, or session corruption.", "Medium", **fields),
        TestCase("Role-based initial display", "Verify permission-dependent visible and hidden controls after migration.", "Open the same page with general, admin, and read-only roles where available.", "Out-of-scope buttons and links are hidden, and visible scope matches Legacy.", "High", **fields),
        TestCase("Multi-browser display", "Verify equivalent behavior in the target browsers.", "Check initial display, primary buttons, and screenshots in Chrome, Edge, and Firefox where required.", "No browser-specific layout breakage or JavaScript errors occur.", "Low", **fields),
    ]
    return take_by_depth(cases, case_depth(page))


def form_cases(page: str, element: Dict[str, Any]) -> List[TestCase]:
    fields = common_element_fields(page, element)
    attrs = element_attributes(element)
    action = first_attr(attrs, "action") or "(current page/default action)"
    method = first_attr(attrs, "method") or "unspecified"
    enctype = first_attr(attrs, "enctype") or "unspecified"
    target = first_attr(attrs, "target") or "unspecified"
    label = element_label(element)
    return [
        TestCase(f"Form action and method: {label}", f"Verify action={action}, method={method}, enctype={enctype}, target={target} after migration.", "Compare form attributes on initial display in Legacy and New.", "Submit target, method, multipart settings, and target window/frame behavior are equivalent.", "High", **fields),
        TestCase(f"Primary form submit path: {label}", "Verify that normal data submit completes the intended business process.", "Enter valid business data available in the client environment and submit the form.", "Navigation, messages, key fields, and backend state match between Legacy and New.", "High", **fields),
        TestCase(f"Required and empty-value validation: {label}", "Verify that required and empty-value validation remains intact.", "Clear visible inputs and tamper-capable hidden values before submit.", "Submit is blocked or a clear business error is shown; no 500, null-pointer, or silent success occurs.", "High", **fields),
        TestCase(f"Field completeness: {label}", "Verify field count, names, initial values, readonly state, and disabled state.", "Compare input, select, textarea, and hidden fields from the initial DOM.", "Legacy/New field sets match, and every difference has a migration reason.", "Medium", **fields),
        TestCase(f"Duplicate submit prevention: {label}", "Verify that repeat clicks and network delay do not duplicate updates.", "Double-click submit under slow-network conditions and retry/replay where appropriate.", "Only one effective business operation is produced, or duplicate requests are rejected/idempotent.", "High", **fields),
        TestCase(f"Boundary length and special characters: {label}", "Cover field length, encoding, and escaping differences.", "Submit max length, overlong, multibyte, newline, quote, and backslash values.", "Length limits remain stable, multibyte text is not corrupted, and special characters do not break the page or SQL/API layers.", "Medium", **fields),
        TestCase(f"XSS probe injection: {label}", "Verify submitted values are escaped on current, confirmation, and error pages.", "Submit probes such as <script>alert(1)</script>.", "The browser does not execute script, and the response has no unescaped user input.", "High", **fields),
    ]


def field_cases(page: str, element: Dict[str, Any]) -> List[TestCase]:
    fields = common_element_fields(page, element)
    attrs = element_attributes(element)
    label = element_label(element)
    maxlength = first_attr(attrs, "maxlength", "maxLength") or "(not specified)"
    readonly = first_attr(attrs, "readonly", "readOnly") or "false"
    disabled = first_attr(attrs, "disabled") or "false"
    return [
        TestCase(f"Input display: {label}", "Verify that the input appears with the same label, position, and initial value after migration.", "Check visibility, enabled/disabled state, initial value, readonly, and disabled attributes on initial display.", f"Legacy/New display state, initial value, readonly={readonly}, and disabled={disabled} match.", "High", **fields),
        TestCase(f"Input editing: {label}", "Verify that normal text can be entered into the field.", "Enter alphanumeric, Japanese, numeric, and symbol values, then blur or submit.", "The value is retained without encoding corruption or JavaScript errors.", "Medium", **fields),
        TestCase(f"Maximum length: {label}", f"Verify that maxlength={maxlength} behavior is preserved after migration.", "Enter exact-limit, over-limit, and mixed full-width/half-width values.", "Within-limit values pass, and over-limit values are blocked or rejected with a business error.", "Medium", **fields),
        TestCase(f"Empty and required checks: {label}", "Verify empty-value handling for required or business-required fields.", "Clear the target field and run register, search, or update.", "An appropriate error message is shown and invalid data is not saved.", "High", **fields),
        TestCase(f"Whitespace and newline handling: {label}", "Verify consistent trimming, newline, and tab handling.", "Submit values with leading/trailing spaces, tabs, and newlines.", "Whitespace handling during save, search, and display matches Legacy.", "Medium", **fields),
        TestCase(f"Special character input: {label}", "Verify controls for symbols and SQL/XSS probes.", "Enter single quotes, double quotes, HTML tags, yen/backslash characters, and full-width symbols.", "No page breakage, SQL error, script execution, or encoding corruption occurs.", "High", **fields),
        TestCase(f"IME and multibyte input: {label}", "Verify Japanese IME input and surrogate-character handling.", "Enter hiragana, katakana, kanji, legacy glyphs, and platform-dependent characters.", "No encoding corruption, missing characters, or length miscalculation occurs.", "Low", **fields),
    ]


def hidden_cases(page: str, element: Dict[str, Any]) -> List[TestCase]:
    fields = common_element_fields(page, element)
    label = element_label(element)
    return [
        TestCase(f"Hidden value carry-over: {label}", "Verify hidden values are carried through screen transitions and submits.", "Compare hidden values on initial display and immediately before submit in Legacy and New.", "Hidden values such as userId, projectId, permission, and mode are preserved as intended.", "High", **fields),
        TestCase(f"Hidden tamper resistance: {label}", "Verify that hidden-value tampering cannot bypass permissions or operate on other data.", "Change hidden values to empty, another user, invalid, or overlong values before submit.", "Invalid values are rejected and out-of-scope data is not viewed or updated.", "High", **fields),
        TestCase(f"Hidden missing-value handling: {label}", "Verify safe behavior when a hidden value is missing.", "Remove the target hidden field from the DOM and submit.", "A business error or safe redisplay occurs instead of NullPointer or HTTP 500.", "Medium", **fields),
    ]


def file_cases(page: str, element: Dict[str, Any]) -> List[TestCase]:
    fields = common_element_fields(page, element)
    attrs = element_attributes(element)
    accept = first_attr(attrs, "accept") or "(not specified)"
    label = element_label(element)
    return [
        TestCase(f"File input display: {label}", "Verify that the file input appears after migration and is operable as input[type=file].", "Check file input visibility, name, and enabled state on initial display.", "Legacy/New contain an operable file input with the same name.", "High", **fields),
        TestCase(f"Upload with no file selected: {label}", "Verify error handling when upload is submitted without a file.", "Press the upload button without selecting a file.", "A business error is shown without HTTP 500 or empty registration.", "High", **fields),
        TestCase(f"Valid file upload: {label}", f"Verify that an allowed business file can be uploaded. accept={accept}.", "Select a valid template or sample file and submit.", "Upload succeeds, and messages, navigation, and registration results match between Legacy and New.", "High", **fields),
        TestCase(f"Empty file upload: {label}", "Verify handling for 0-byte or empty-content files.", "Upload a 0-byte file or a file containing only blank lines.", "The file is rejected as invalid or processed exactly as specified.", "High", **fields),
        TestCase(f"Invalid file extension: {label}", "Verify that disallowed extensions are rejected.", "Upload files with extensions such as txt, exe, and zip when not allowed.", "Invalid types are rejected with a clear error, and dangerous files are not saved server-side.", "High", **fields),
        TestCase(f"File type spoofing: {label}", "Verify rejection when extension and MIME/content do not match.", "Rename text content to .xls/.xlsx or upload a file with mismatched MIME.", "Content validation or business validation is effective without abnormal processing.", "High", **fields),
        TestCase(f"File size boundary: {label}", "Verify multipart, business, and reverse-proxy size limits.", "Upload files just below, exactly at, and above the limit.", "Within-boundary files pass, over-limit files are rejected reliably, and no 500/504 or temporary-file leak occurs.", "High", **fields),
        TestCase(f"Japanese filename: {label}", "Verify that Japanese filenames are processed without encoding corruption.", "Upload filenames containing kanji, kana, full-width spaces, long vowels, and brackets.", "Messages, logs, registration results, and download filenames are not corrupted.", "Medium", **fields),
        TestCase(f"Symbol-heavy filename: {label}", "Verify filename safety for symbols and spaces.", "Upload filenames containing ../, ..\\, quotes, spaces, brackets, newlines, and very long names.", "Path traversal, log pollution, and page breakage do not occur; unsafe names are rejected or normalized.", "Medium", **fields),
        TestCase(f"Invalid template format: {label}", "Verify detection of missing columns, invalid types, and missing required fields in Excel/CSV files.", "Upload files with missing required columns, incorrect column names, duplicate rows, invalid types, or excessive row counts.", "Business errors identify the relevant row or column, and invalid data is not registered.", "High", **fields),
        TestCase(f"Duplicate upload: {label}", "Verify duplicate handling when the same file is uploaded repeatedly.", "Upload the same file repeatedly and include a double-click attempt.", "Duplicate registration is prevented, or overwrite/error behavior follows the specification.", "Medium", **fields),
        TestCase(f"Post-upload message and page state: {label}", "Verify messages, navigation, and input state after upload.", "Check page state, messages, back behavior, and re-upload availability after valid and invalid uploads.", "Legacy/New messages, navigation, and re-operation state match.", "High", **fields),
    ]


def button_cases(page: str, element: Dict[str, Any]) -> List[TestCase]:
    fields = common_element_fields(page, element)
    attrs = element_attributes(element)
    onclick = first_attr(attrs, "onclick", "onClick") or "(not specified)"
    label = element_label(element)
    semantic = button_semantic(element)
    cases = [
        TestCase(f"Button display and enabled state: {label}", "Verify that the button keeps the same label, position, and enabled state after migration.", "Check button text, visibility, disabled state, and role-dependent display on initial display.", "Visible/hidden state, enabled/disabled state, and label match between Legacy and New.", "High", **fields),
        TestCase(f"Primary button click path: {label}", "Verify that clicking the button performs the same business action after migration.", "Click from a normal page state and record requests, navigation, popups, refreshes, and backend state changes.", "Legacy/New behavior matches; the button does not become unresponsive, duplicate-triggered, or routed to the wrong action.", "High", **fields),
        TestCase(f"Frontend script dependency: {label}", f"Verify that onclick and related scripts survived migration. onclick={onclick}.", "Click the button with the browser console open and observe JavaScript errors, missing functions, undefined variables, and blocked requests.", "No console script errors occur; dynamic validation, confirms, parameter composition, and page-state updates work normally.", "Medium", **fields),
        TestCase(f"Repeat click and duplicate-submit guard: {label}", "Verify that repeated clicks do not duplicate registration, update, or send operations.", "Double-click or multi-click quickly, including under slow-network conditions.", "Only one effective business operation is produced, or duplicate requests are blocked/idempotent.", "High", **fields),
    ]
    if semantic == "submit":
        cases.extend([
            TestCase(f"Submit target check: {label}", "Verify the button submits to the expected action and preserves target/frame/window behavior.", "Click the button and record request URL, method, target, and destination.", "Legacy/New submit target, transition, and message behavior match.", "High", **fields),
            TestCase(f"Redisplay after submit error: {label}", "Verify safe redisplay after a business error on submit.", "Click the button with invalid data and check error messages and retained input.", "No HTTP 500 or blank page occurs; error message, input retention, and focus position match Legacy.", "High", **fields),
        ])
    elif semantic == "close_window":
        cases.extend([
            TestCase(f"Close/cancel behavior: {label}", "Verify that the target window closes or returns according to specification.", "Click the control and inspect window close behavior, parent page state, and session state.", "The target window closes or returns to the previous page as specified, with no parent-page error.", "High", **fields),
            TestCase(f"Redisplay after close: {label}", "Verify that the same page can be opened again after close/cancel.", "After cancel, reopen the same page through the menu or business entry.", "The page can be redisplayed and session/context remains valid.", "Medium", **fields),
        ])
    elif semantic == "download":
        cases.extend([
            TestCase(f"Download start: {label}", "Verify that clicking the button starts the expected file output.", "Inspect the download event, filename, extension, and size after click.", "Legacy/New filename, format, and content summary match.", "High", **fields),
            TestCase(f"Download permission: {label}", "Verify that unauthorized users cannot export the file.", "Display or click the button with users that have different permissions.", "Unauthorized users see a hidden control or error, and no file is exported.", "High", **fields),
        ])
    return cases


def link_cases(page: str, element: Dict[str, Any]) -> List[TestCase]:
    fields = common_element_fields(page, element)
    attrs = element_attributes(element)
    href = first_attr(attrs, "href", "action", "page") or first_attr(attrs, "onclick", "onClick") or "(not specified)"
    target = first_attr(attrs, "target") or "(not specified)"
    label = element_label(element)
    return [
        TestCase(f"Link display: {label}", "Verify that the link keeps the same text, position, and display conditions after migration.", "Check link text, href/onclick, target, and role-dependent display on initial display.", "Legacy/New visibility, text, href, and target match.", "High", **fields),
        TestCase(f"Primary link navigation path: {label}", f"Verify that the migrated link still reaches the correct page or business action, target={href}.", "Click the link and record target URL, request parameters, page title, and key content.", "Legacy/New targets match; parameters are preserved; no 404/500 occurs; login and permission state remain valid.", "High", **fields),
        TestCase(f"Link target and popup behavior: {label}", f"Verify target={target} behavior after migration.", "Check normal click, new tab/window, and popup behavior.", "Popup/window/frame opening behavior matches between Legacy and New.", "Medium", **fields),
        TestCase(f"Parameter carry-over: {label}", "Verify id, mode, returnUrl, and similar parameters on link click.", "Inspect URL, hidden values, and request parameters before and after click.", "Required parameters are not lost, and unnecessary sensitive data is not exposed.", "High", **fields),
        TestCase(f"Parameter tamper and permission check: {label}", "Verify that link parameter tampering cannot access unauthorized data.", "Change URL parameters to nonexistent, unauthorized, empty, special-character, and overlong values.", "Invalid parameters are rejected or redirected safely without leaking data, stack traces, or internal paths.", "High", **fields),
        TestCase(f"Back and open-mode behavior: {label}", "Verify state consistency after browser back, refresh, and new-tab open.", "Click, then run back, refresh, and new-tab open; check popups/downloads separately.", "Page state recovers safely without repeating dangerous operations, and download/popup behavior matches Legacy.", "Medium", **fields),
        TestCase(f"Broken link check: {label}", "Verify that help, external, and static file links are not broken.", "Open the link target and check HTTP status, encoding, and 404/500 responses.", "The link target displays normally, or environment differences have an accepted reason.", "Medium", **fields),
        TestCase(f"Localized link check: {label}", "Verify that help and copy links switch correctly by language.", "Check link target and displayed text under Japanese and English language settings.", "The correct link target and text appear for each language.", "Low", **fields),
    ]




def scenario_cases(page: str, element: Dict[str, Any]) -> List[TestCase]:
    fields = common_element_fields(page, element)
    label = element_label(element)
    case_type = as_text(element.get("case_type") or element.get("action_hint"), "scenario")
    pre_steps = element.get("pre_steps") or []
    main_step = element.get("main_step") or {}
    if case_type == "upload_submit":
        meta = executable_metadata(element)
        return [
            TestCase(
                title=f"Automated scenario: file select to submit: {label}",
                objective="Verify the primary automated path for selecting a file and submitting the form.",
                steps="Set the test file through pre_steps and submit the form or button through main_step.",
                expected="Legacy/New both complete submit without major differences in destination, messages, or page state.",
                severity="High",
                **fields,
                automation_mode="auto",
                case_type="upload_submit",
                action_type="upload_submit",
                test_data=meta.get("test_data", ""),
                submit_locator=meta.get("submit_locator", ""),
                expected_type=meta.get("expected_type", "visual_or_message"),
                expected_value=meta.get("expected_value", ""),
                pre_steps=meta.get("pre_steps", ""),
                main_step=meta.get("main_step", ""),
            )
        ]
    meta = executable_metadata(element)
    return [
        TestCase(
            title=f"Automated scenario: {case_type}: {label}",
            objective="Verify an executable scenario generated from page_mapping executable_cases.",
            steps=f"Run case_type={case_type}, pre_steps={len(pre_steps)}, main_step={as_text(main_step.get('action_type'), '-')}.",
            expected="Legacy/New produce equivalent results without BLOCKED or major DIFF outcomes.",
            severity="Medium",
            **fields,
            automation_mode="auto",
            case_type=meta.get("case_type", case_type),
            action_type=meta.get("action_type", as_text(main_step.get("action_type"), "")),
            test_data=meta.get("test_data", ""),
            submit_locator=meta.get("submit_locator", ""),
            expected_type=meta.get("expected_type", "visual_or_message"),
            expected_value=meta.get("expected_value", ""),
            pre_steps=meta.get("pre_steps", ""),
            main_step=meta.get("main_step", ""),
        )
    ]

CASE_BUILDERS = {
    "form": form_cases,
    "field": field_cases,
    "select": field_cases,
    "textarea": field_cases,
    "hidden": hidden_cases,
    "file": file_cases,
    "button": button_cases,
    "link": link_cases,
    "scenario": scenario_cases,
}


def normalize_mapping_element(element: Dict[str, Any]) -> Dict[str, Any]:
    copied = dict(element)
    if not copied.get("locator"):
        copied["locator"] = copied.get("legacy_locator") or copied.get("new_locator")
    if not copied.get("action_hint") and copied.get("action_type"):
        copied["action_hint"] = copied.get("action_type")
    if not copied.get("label"):
        copied["label"] = element_label(copied)
    return copied


def collect_elements(page: Dict[str, Any]) -> List[Dict[str, Any]]:
    elements: List[Dict[str, Any]] = []

    def add_items(items: Any, source: str) -> None:
        for item in items or []:
            if not isinstance(item, dict):
                continue
            copied = dict(item)
            copied.setdefault("_source", source)
            elements.append(copied)

    # Scanner output.
    add_items(page.get("elements", []), "elements")

    # Mapping output. Keep matched elements so successful pairs also generate checklist rows.
    for key in ("matched_elements", "locator_changes", "missing_legacy_elements", "missing_new_elements", "full_action_steps"):
        add_items(page.get(key, []), key)

    # Executable scenarios are the bridge between the human checklist and the
    # automated regression runner. Include them so the Excel clearly shows
    # which generated items are actually executable.
    for case in page.get("executable_cases", []) or []:
        if isinstance(case, dict):
            item = dict(case)
            item.setdefault("kind", "scenario")
            item.setdefault("locator", item.get("legacy_locator") or (item.get("main_step") or {}).get("legacy_locator"))
            item.setdefault("action_hint", item.get("case_type"))
            item.setdefault("_source", "executable_cases")
            elements.append(item)

    # Some mapping formats only have legacy/new side elements embedded under comparable pairs.
    for key in ("actions", "action_items", "test_actions"):
        add_items(page.get(key, []), key)

    normalized: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str, str, str, str]] = set()
    for element in elements:
        if not isinstance(element, dict):
            continue
        normalized_element = normalize_mapping_element(element)
        kind = as_text(normalized_element.get("kind")).lower()
        locator = normalized_locator(normalized_element)
        label = element_label(normalized_element).strip()
        
        # Skip low-value elements to keep the checklist focused.
        if kind in {"link", "button"} and not label and locator == "(locator missing)":
            continue
        if kind == "hidden":
            continue  # Hidden fields are handled through form-level evidence.
            
        line = as_text(normalized_element.get("line"), "-")
        source = as_text(normalized_element.get("_source"), "")
        key = (kind, locator, label, line, source)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(normalized_element)
    return normalized


def infer_action_type(element: Dict[str, Any]) -> str:
    kind = as_text(element.get("kind")).lower()
    if kind == "button":
        return button_semantic(element)
    action = as_text(element.get("action_type") or element.get("action_hint") or element.get("case_type")).lower()
    if action:
        return action
    if kind == "file":
        return "upload"
    if kind == "link":
        attrs = element_attributes(element)
        onclick = first_attr(attrs, "onclick", "onClick").lower()
        href = first_attr(attrs, "href").lower()
        if "download" in onclick:
            return "download"
        if "fnsubmit" in onclick:
            return "submit"
        if href:
            return "navigate"
    if kind == "form":
        return "submit"
    return ""


def executable_metadata(element: Dict[str, Any]) -> Dict[str, str]:
    kind = as_text(element.get("kind")).lower()
    case_type = as_text(element.get("case_type") or element.get("action_hint") or element.get("action_type"))
    action_type = infer_action_type(element)

    pre_steps = element.get("pre_steps") or []
    main_step = element.get("main_step") or {}

    locator = normalized_locator(element)
    submit_locator = as_text(
        element.get("submit_locator")
        or element.get("submit_legacy_locator")
        or main_step.get("legacy_locator")
        or main_step.get("locator")
    )

    test_data = as_text(element.get("test_data") or element.get("value"))
    if not test_data and isinstance(pre_steps, list):
        for step in pre_steps:
            if isinstance(step, dict) and as_text(step.get("action_type")).lower() in {"upload", "set_input_files"}:
                test_data = as_text(step.get("value") or step.get("test_data"))
                locator = as_text(step.get("legacy_locator") or step.get("locator") or locator)
                break

    if not test_data and action_type in {"upload", "upload_submit"}:
        test_data = "test_data/upload/default_valid.xlsx"

    if not case_type:
        case_type = action_type or kind

    if case_type == "upload_submit":
        action_type = "upload_submit"
        if not submit_locator:
            submit_locator = as_text(main_step.get("legacy_locator") or main_step.get("locator"))

    expected_type = as_text(element.get("expected_type"))
    if not expected_type:
        if action_type in {"download"}:
            expected_type = "download"
        elif action_type in {"close_window"}:
            expected_type = "window_closed"
        elif action_type in {"upload", "upload_submit", "submit", "click", "navigate"}:
            expected_type = "visual_or_message"
        else:
            expected_type = "manual_review"

    return {
        "automation_mode": "auto",
        "case_type": case_type,
        "action_type": action_type,
        "test_data": test_data,
        "submit_locator": submit_locator,
        "expected_type": expected_type,
        "expected_value": as_text(element.get("expected_value")),
        "pre_steps": as_json(pre_steps),
        "main_step": as_json(main_step),
        "locator": locator,
    }


def infer_case_action_type(case: TestCase, element: Dict[str, Any]) -> str:
    kind = as_text(element.get("kind") or case.kind).lower()
    title_blob = " ".join([case.title, case.objective, case.steps]).lower()

    if kind == "page":
        return "snapshot"
    if kind == "form":
        return "submit"
    if kind == "file":
        return "upload"
    if kind == "button":
        return button_semantic(element)
    if kind == "link":
        return infer_action_type(element) or "navigate"
    if kind == "hidden":
        return "set_value"
    if kind in {"select"} or "select" in as_text(element.get("tag")).lower():
        return "select"
    if kind in FIELD_LIKE_KINDS:
        if any(token in title_blob for token in ("空", "empty", "required", "必須")):
            return "clear"
        return "fill"
    return infer_action_type(element)


def default_test_data_for_case(action_type: str, case: TestCase, element: Dict[str, Any]) -> str:
    existing = as_text(case.test_data or element.get("test_data") or element.get("value"))
    if existing:
        return existing

    title_blob = " ".join([case.title, case.objective, case.steps]).lower()
    attrs = element_attributes(element)
    maxlength = first_attr(attrs, "maxlength", "maxLength")

    if action_type in {"clear"}:
        return ""
    if action_type == "set_value":
        return "moonlight-hidden-auto"
    if action_type == "press":
        return "Enter"
    if action_type == "upload":
        if any(token in title_blob for token in ("0 byte", "空ファイル", "empty")):
            return "test_data/upload/empty.txt"
        if any(token in title_blob for token in ("拡張子", "invalid", "不正", ".exe")):
            return "test_data/upload/invalid_type.exe"
        if any(token in title_blob for token in ("サイズ", "large", "上限")):
            return "test_data/upload/large_sample.tsv"
        if any(token in title_blob for token in ("日本語", "文字化け")):
            return "test_data/upload/日本語ファイル名.tsv"
        return "test_data/upload/プロジェクトリストアップロード.tsv"
    if action_type == "fill":
        if "xss" in title_blob or "script" in title_blob:
            return "<script>alert(1)</script>"
        if "sql" in title_blob:
            return "' OR '1'='1"
        if any(token in title_blob for token in ("数字", "number", "件数")):
            return "1234567890"
        if maxlength and maxlength.isdigit():
            size = max(1, min(int(maxlength), 128))
            return "M" * size
        if any(token in title_blob for token in ("最大", "maxlength", "桁")):
            return "M" * 64
        if any(token in title_blob for token in ("日本語", "ime")):
            return "自動化テスト"
        return "moonlight-auto"
    return ""


def expected_type_for_action(action_type: str) -> str:
    if action_type == "download":
        return "download"
    if action_type == "close_window":
        return "window_closed"
    if action_type in {"snapshot", "wait"}:
        return "visual"
    if action_type in {"fill", "clear", "select", "set_value", "press"}:
        return "control_state"
    if action_type in {"upload", "upload_submit", "submit", "click", "navigate"}:
        return "visual_or_message"
    return "manual_review"


def auto_enrich_case(case: TestCase, element: Dict[str, Any]) -> TestCase:
    if case.automation_mode == "auto" and case.action_type:
        return case

    action_type = infer_case_action_type(case, element)
    locator = normalized_locator(element)
    can_auto = action_type in AUTO_ACTION_TYPES and (
        locator not in {"", "(locator missing)"} or action_type == "snapshot"
    )
    if action_type == "snapshot":
        locator = "__page__"

    return replace(
        case,
        automation_mode="auto" if can_auto else "manual/assist",
        case_type=case.case_type or action_type,
        action_type=case.action_type or action_type,
        test_data=case.test_data or default_test_data_for_case(action_type, case, element),
        submit_locator=case.submit_locator or as_text(element.get("submit_locator")),
        expected_type=case.expected_type or expected_type_for_action(action_type),
        expected_value=case.expected_value,
        locator=locator if can_auto else case.locator,
    )


def auto_enrich_cases(cases: List[TestCase], element: Dict[str, Any]) -> List[TestCase]:
    return [auto_enrich_case(case, element) for case in cases]


def is_executable_element(element: Dict[str, Any]) -> bool:
    source = as_text(element.get("_source"))
    kind = as_text(element.get("kind")).lower()
    if source == "executable_cases":
        return True
    if source == "full_action_steps" and kind in {"link", "button", "file"}:
        return bool(infer_action_type(element) and normalized_locator(element))
    return False


def automation_case(page: str, element: Dict[str, Any]) -> Optional[TestCase]:
    if not is_executable_element(element):
        return None

    meta = executable_metadata(element)
    label = element_label(element)
    action_type = meta["action_type"] or "-"
    case_type = meta["case_type"] or action_type
    fields = common_element_fields(page, element)
    fields["locator"] = meta["locator"] or fields["locator"]

    if case_type == "upload_submit":
        title = f"AUTO execution: file select to submit: {label}"
        objective = "Define the primary upload path that can run from the Excel checklist."
        steps = "Set the test file on locator, then submit through submit_locator on a form, button, or link."
        expected = "Legacy/New both complete execution without BLOCKED or major DIFF outcomes."
    else:
        title = f"AUTO execution: {action_type}: {label}"
        objective = "Define a page operation that can run from the Excel checklist."
        steps = "Run action_type against the target locator and compare Legacy/New results."
        expected = "Legacy/New produce equivalent results without BLOCKED or major DIFF outcomes."

    return TestCase(
        title,
        objective,
        steps,
        expected,
        "High",
        **fields,
        automation_mode=meta["automation_mode"],
        case_type=case_type,
        action_type=action_type,
        test_data=meta["test_data"],
        submit_locator=meta["submit_locator"],
        expected_type=meta["expected_type"],
        expected_value=meta["expected_value"],
        pre_steps=meta["pre_steps"],
        main_step=meta["main_step"],
    )


def uses_page_specific_planner(scan_data: Dict[str, Any]) -> bool:
    return (
        isinstance(scan_data.get("page_mappings"), list)
        or isinstance(scan_data.get("runtime_profiles"), list)
        or scan_data.get("schema") == RUNTIME_PROFILE_SCHEMA
        or isinstance(scan_data.get("controls"), list)
    )


def planned_case_to_test_case(case: Dict[str, Any]) -> TestCase:
    capabilities = as_text(case.get("matched_capabilities"))
    evidence_parts = [
        f"template_id={as_text(case.get('template_id'))}",
        f"generated_by={as_text(case.get('generated_by'), 'PageCasePlanner')}",
    ]
    if case.get("parent_case_id"):
        evidence_parts.append(f"parent_case_id={as_text(case.get('parent_case_id'))}")
    if case.get("viewpoint_id"):
        evidence_parts.append(f"viewpoint_id={as_text(case.get('viewpoint_id'))}")
    if capabilities:
        evidence_parts.append(f"matched_capabilities={capabilities}")
    if case.get("profile_source"):
        evidence_parts.append(f"profile_source={as_text(case.get('profile_source'))}")
    if case.get("runtime_profile_path"):
        evidence_parts.append(f"runtime_profile_path={as_text(case.get('runtime_profile_path'))}")
    if case.get("page_spec_path"):
        evidence_parts.append(f"page_spec_path={as_text(case.get('page_spec_path'))}")
    if case.get("evidence"):
        evidence_parts.append(as_text(case.get("evidence")))
    return TestCase(
        title=as_text(case.get("title"), as_text(case.get("case_type"), "planned case")),
        objective=as_text(case.get("objective")),
        steps=as_text(case.get("steps")),
        expected=as_text(case.get("expected")),
        severity=as_text(case.get("severity") or case.get("risk"), "High"),
        page=as_text(case.get("page_id"), "<unknown>"),
        kind=as_text(case.get("case_type"), "page"),
        locator=as_text(case.get("locator"), "__page__"),
        line="-",
        evidence="\n".join(part for part in evidence_parts if part),
        automation_mode=as_text(case.get("automation_mode"), "auto"),
        case_type=as_text(case.get("case_type")),
        action_type=as_text(case.get("action_type")),
        test_data=as_text(case.get("test_data")),
        submit_locator=as_text(case.get("submit_locator")),
        expected_type=as_text(case.get("expected_type")),
        expected_value=as_text(case.get("expected_value")),
        pre_steps=as_text(case.get("pre_steps")),
        main_step=as_text(case.get("main_step")),
        case_id=as_text(case.get("case_id")),
        parent_case_id=as_text(case.get("parent_case_id")),
        viewpoint_id=as_text(case.get("viewpoint_id")),
        enabled=as_text(case.get("enabled"), "true"),
        generated_by=as_text(case.get("generated_by"), "PageCasePlanner"),
        matched_capabilities=capabilities,
        destructive=as_text(case.get("destructive"), "false"),
        priority=as_text(case.get("priority")),
    )


def case_priority_order(case: TestCase) -> Tuple[int, str]:
    try:
        return int(case.priority), case.priority
    except (TypeError, ValueError):
        return 1000 + SEVERITY_ORDER.get(case.severity, 99), case.severity


def _page_spec_skipped(page_id: str, reason: str, capabilities: Dict[str, Any]) -> Dict[str, Any]:
    matched = ",".join(name for name, enabled in capabilities.items() if enabled)
    return {
        "page_id": page_id,
        "template_id": "manual_page_spec",
        "case_type": "page_spec",
        "status": "fallback",
        "reason": reason[:500],
        "missing_capabilities": "",
        "matched_capabilities": matched,
    }


def _write_page_spec_inputs(
    page_input: Dict[str, Any],
    *,
    page_spec_dir: Path,
) -> Tuple[Dict[str, Any], Path, Path, Path]:
    evidence = PageEvidenceBuilder().build(page_input)
    evidence_path = page_evidence_cache_path(evidence.get("page_id"), page_spec_dir)
    prompt_path = page_spec_prompt_cache_path(evidence.get("page_id"), page_spec_dir)
    spec_path = page_spec_cache_path(evidence.get("page_id"), page_spec_dir)
    write_page_evidence(evidence_path, evidence)
    write_page_spec_prompt(prompt_path, evidence)
    return evidence, evidence_path, prompt_path, spec_path


def _plan_with_manual_page_spec(
    page_input: Dict[str, Any],
    *,
    page_spec_dir: Path,
    export_page_spec_inputs: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    evidence, evidence_path, prompt_path, spec_path = _write_page_spec_inputs(page_input, page_spec_dir=page_spec_dir)
    spec = load_cached_page_spec(spec_path)
    if spec is None:
        raise PageSpecError(
            "Manual PageSpec JSON not found. "
            f"Use the prompt file at {prompt_path} in the web model, then save the returned JSON to {spec_path}."
        )

    planned_cases = page_spec_to_cases(spec, evidence)
    profile = page_spec_to_profile(spec, evidence, page_spec_path=str(spec_path))
    profile["page_evidence_path"] = str(evidence_path)
    profile["page_spec_prompt_path"] = str(prompt_path)
    for case in planned_cases:
        case["page_spec_path"] = str(spec_path)
        case["page_evidence_path"] = str(evidence_path)
        case["page_spec_prompt_path"] = str(prompt_path)
    return planned_cases, [], profile


def plan_page_specific_cases(
    scan_data: Dict[str, Any],
    *,
    runtime_profile_dir: Path = DEFAULT_RUNTIME_PROFILE_DIR,
    include_runtime_profile_dir: bool = False,
    target_pages: Optional[Sequence[str]] = None,
    page_spec_dir: Path = DEFAULT_PAGE_SPEC_DIR,
    use_page_spec: bool = False,
    export_page_spec_inputs: bool = False,
) -> Tuple[List[TestCase], List[Dict[str, Any]], List[Dict[str, Any]]]:
    planner = PageCasePlanner()
    cases: List[TestCase] = []
    skipped_templates: List[Dict[str, Any]] = []
    profiles: List[Dict[str, Any]] = []
    runtime_profiles = [] if scan_data.get("schema") == RUNTIME_PROFILE_SCHEMA else load_runtime_profiles(runtime_profile_dir)
    consumed_runtime_profiles = set()

    for page in page_entries(scan_data):
        if not page_matches_targets(page, target_pages):
            continue
        selected_profile = select_runtime_profile_for_page(page, runtime_profiles)
        profile_input = selected_profile or page
        if selected_profile:
            consumed_runtime_profiles.add(runtime_profile_identity(selected_profile))
        if export_page_spec_inputs or use_page_spec:
            try:
                if use_page_spec:
                    planned_cases, skipped, profile = _plan_with_manual_page_spec(
                        profile_input,
                        page_spec_dir=page_spec_dir,
                        export_page_spec_inputs=export_page_spec_inputs,
                    )
                else:
                    evidence, evidence_path, prompt_path, spec_path = _write_page_spec_inputs(profile_input, page_spec_dir=page_spec_dir)
                    planned_cases, skipped, profile = planner.plan(profile_input)
                    profile["page_evidence_path"] = str(evidence_path)
                    profile["page_spec_prompt_path"] = str(prompt_path)
                    profile["page_spec_path"] = str(spec_path)
                    skipped.append(
                        _page_spec_skipped(
                            page_name_of(profile_input),
                            f"PageSpec inputs exported. Save web-model JSON to {spec_path}, then regenerate with --use-page-spec.",
                            profile.get("capabilities") or {},
                        )
                    )
            except PageSpecError as exc:
                if use_page_spec:
                    raise
                planned_cases, skipped, profile = planner.plan(profile_input)
                skipped.append(_page_spec_skipped(page_name_of(profile_input), str(exc), profile.get("capabilities") or {}))
        else:
            planned_cases, skipped, profile = planner.plan(profile_input)
        cases.extend(planned_case_to_test_case(case) for case in planned_cases)
        skipped_templates.extend(skipped)
        profiles.append(profile)

    if include_runtime_profile_dir:
        for runtime_profile in runtime_profiles:
            if not page_matches_targets(runtime_profile, target_pages):
                continue
            identity = runtime_profile_identity(runtime_profile)
            if identity in consumed_runtime_profiles:
                continue
            profile_input = runtime_profile_page_input(runtime_profile)
            if export_page_spec_inputs or use_page_spec:
                try:
                    if use_page_spec:
                        planned_cases, skipped, profile = _plan_with_manual_page_spec(
                            profile_input,
                            page_spec_dir=page_spec_dir,
                            export_page_spec_inputs=export_page_spec_inputs,
                        )
                    else:
                        evidence, evidence_path, prompt_path, spec_path = _write_page_spec_inputs(profile_input, page_spec_dir=page_spec_dir)
                        planned_cases, skipped, profile = planner.plan(profile_input)
                        profile["page_evidence_path"] = str(evidence_path)
                        profile["page_spec_prompt_path"] = str(prompt_path)
                        profile["page_spec_path"] = str(spec_path)
                        skipped.append(
                            _page_spec_skipped(
                                page_name_of(profile_input),
                                f"PageSpec inputs exported. Save web-model JSON to {spec_path}, then regenerate with --use-page-spec.",
                                profile.get("capabilities") or {},
                            )
                        )
                except PageSpecError as exc:
                    if use_page_spec:
                        raise
                    planned_cases, skipped, profile = planner.plan(profile_input)
                    skipped.append(_page_spec_skipped(page_name_of(profile_input), str(exc), profile.get("capabilities") or {}))
            else:
                planned_cases, skipped, profile = planner.plan(profile_input)
            cases.extend(planned_case_to_test_case(case) for case in planned_cases)
            skipped_templates.extend(skipped)
            profiles.append(profile)
            consumed_runtime_profiles.add(identity)

    cases.sort(
        key=lambda item: (
            item.page,
            item.destructive == "true",
            case_priority_order(item),
            SEVERITY_ORDER.get(item.severity, 99),
            item.case_type,
            item.case_id,
            item.viewpoint_id,
        )
    )
    return cases, skipped_templates, profiles


def generate_cases(
    scan_data: Dict[str, Any],
    *,
    runtime_profile_dir: Path = DEFAULT_RUNTIME_PROFILE_DIR,
    include_runtime_profile_dir: bool = False,
    target_pages: Optional[Sequence[str]] = None,
    page_spec_dir: Path = DEFAULT_PAGE_SPEC_DIR,
    use_page_spec: bool = False,
    export_page_spec_inputs: bool = False,
) -> List[TestCase]:
    if uses_page_specific_planner(scan_data):
        cases, _, _ = plan_page_specific_cases(
            scan_data,
            runtime_profile_dir=runtime_profile_dir,
            include_runtime_profile_dir=include_runtime_profile_dir,
            target_pages=target_pages,
            page_spec_dir=page_spec_dir,
            use_page_spec=use_page_spec,
            export_page_spec_inputs=export_page_spec_inputs,
        )
        return cases

    cases: List[TestCase] = []
    for page in page_entries(scan_data):
        if not page_matches_targets(page, target_pages):
            continue
        page_name = page_name_of(page)
        depth = case_depth(page)
        page_element = {
            "kind": "page",
            "label": page_name,
            "locator": "__page__",
            "line": "-",
        }
        cases.extend(auto_enrich_cases(page_cases(page), page_element))

        for element in attach_related_fields(collect_elements(page)):
            auto_case = automation_case(page_name, element)
            if auto_case is not None:
                cases.append(auto_case)

            kind = as_text(element.get("kind")).lower()
            if kind not in CASE_GENERATING_KINDS:
                continue
            builder = CASE_BUILDERS.get(kind)
            if builder is None:
                continue
            built = auto_enrich_cases(builder(page_name, element), element)
            cases.extend(take_by_depth(built, depth))

    return sorted(cases, key=lambda item: (item.page, SEVERITY_ORDER.get(item.severity, 99), item.kind, item.title, item.locator))


def summarize_counts(scan_data: Dict[str, Any], cases: Sequence[TestCase]) -> Dict[str, int]:
    totals = {str(key): int(value) for key, value in scan_data.get("totals", {}).items()}
    if not totals:
        for page in page_entries(scan_data):
            for kind, count in (page.get("counts") or page.get("legacy_counts") or {}).items():
                try:
                    totals[str(kind)] = totals.get(str(kind), 0) + int(count)
                except Exception:
                    pass
    if not totals:
        for case in cases:
            totals[case.kind] = totals.get(case.kind, 0) + 1
    totals["test_cases"] = len(cases)
    return totals


def markdown_escape_cell(value: str) -> str:
    return html.escape(value).replace("\n", "<br>").replace("|", "\\|")


def unique_evidence_cases(cases: Sequence[TestCase]) -> List[TestCase]:
    unique: List[TestCase] = []
    seen = set()
    for case in cases:
        key = (case.page, case.line, case.kind, case.locator, case.evidence)
        if key in seen:
            continue
        seen.add(key)
        unique.append(case)
    return unique


def universal_checklist_markdown_lines() -> List[str]:
    lines = ["", "## Universal Migration Checklist", "", "| # | Checklist item | Automation mode | Evidence policy |", "|---|---|---|---|"]
    for index, section in enumerate(CHECKLIST_SECTIONS, start=1):
        lines.append("| " + " | ".join(markdown_escape_cell(value) for value in [str(index), section["title"], section["mode"], section["expected"]]) + " |")
    return lines


def render_markdown(scan_data: Dict[str, Any], cases: Sequence[TestCase]) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pages = list(page_entries(scan_data))
    totals = summarize_counts(scan_data, cases)
    lines = [
        "# Automated Test Recommendation Report",
        "",
        f"- Generated at: {generated_at}",
        f"- Scan root: {as_text(scan_data.get('root'), as_text(scan_data.get('source'), '<unknown>'))}",
        f"- Page count: {len(pages)}",
        f"- Element totals: form={totals.get('form', 0)}, file={totals.get('file', 0)}, button={totals.get('button', 0)}, link={totals.get('link', 0)}",
        f"- Recommended case count: {totals.get('test_cases', 0)}",
        "",
        "## Priority Execution List",
        "",
    ]
    high_cases = [case for case in cases if case.severity == "High"]
    for index, case in enumerate(high_cases[:20], start=1):
        lines.append(f"{index}. [{case.page}:{case.line}] {case.title} - {case.objective}")
    if not high_cases:
        lines.append("No high-priority cases were generated. Confirm that the input JSON contains pages, elements, or page_mapping data.")
    lines.extend(universal_checklist_markdown_lines())
    lines.extend(["", "## Case Details", "", "| # | Severity | Page | Line | Type | Locator | Case | Suggested Action | Expected Result | Automation Mode | action_type |", "|---|---|---|---|---|---|---|---|---|---|---|"])
    for index, case in enumerate(cases, start=1):
        lines.append("| " + " | ".join(markdown_escape_cell(value) for value in [str(index), case.severity, case.page, case.line, case.kind, case.locator, f"{case.title}\n{case.objective}", case.steps, case.expected, case.automation_mode, case.action_type]) + " |")
    lines.extend(["", "## Element Evidence", "", "| Page | Line | Type | Locator | JSP Evidence |", "|---|---|---|---|---|"])
    for case in unique_evidence_cases(cases):
        lines.append("| " + " | ".join(markdown_escape_cell(value) for value in [case.page, case.line, case.kind, case.locator, case.evidence]) + " |")
    return "\n".join(lines) + "\n"


def write_excel(
    path: Path,
    scan_data: Dict[str, Any],
    cases: Sequence[TestCase],
    *,
    runtime_profile_dir: Path = DEFAULT_RUNTIME_PROFILE_DIR,
    include_runtime_profile_dir: bool = False,
    target_pages: Optional[Sequence[str]] = None,
    page_spec_dir: Path = DEFAULT_PAGE_SPEC_DIR,
    use_page_spec: bool = False,
    export_page_spec_inputs: bool = False,
    planned_skipped_templates: Optional[List[Dict[str, Any]]] = None,
    planned_page_profiles: Optional[List[Dict[str, Any]]] = None,
) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise SystemExit("Excel output requires openpyxl. Install dependencies with: pip install -r requirements.txt") from exc

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"
    pages = list(page_entries(scan_data))
    totals = summarize_counts(scan_data, cases)
    skipped_templates: List[Dict[str, Any]] = []
    page_profiles: List[Dict[str, Any]] = []
    if planned_skipped_templates is not None or planned_page_profiles is not None:
        skipped_templates = planned_skipped_templates or []
        page_profiles = planned_page_profiles or []
    elif uses_page_specific_planner(scan_data):
        _, skipped_templates, page_profiles = plan_page_specific_cases(
            scan_data,
            runtime_profile_dir=runtime_profile_dir,
            include_runtime_profile_dir=include_runtime_profile_dir,
            target_pages=target_pages,
            page_spec_dir=page_spec_dir,
            use_page_spec=use_page_spec,
            export_page_spec_inputs=export_page_spec_inputs,
        )
    summary_rows = [
        ("Generated at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("Scan root", as_text(scan_data.get("root"), as_text(scan_data.get("source"), "<unknown>"))),
        ("Page count", len(page_profiles) if page_profiles else len(pages)),
        ("form", totals.get("form", 0)),
        ("file", totals.get("file", 0)),
        ("button", totals.get("button", 0)),
        ("link", totals.get("link", 0)),
        ("Recommended case count", totals.get("test_cases", 0)),
    ]
    for row in summary_rows:
        summary.append(row)
    summary.column_dimensions["A"].width = 18
    summary.column_dimensions["B"].width = 90

    universal = workbook.create_sheet("UniversalChecklist")
    universal.append(["#", "Checklist item", "Automation mode", "Evidence policy"])
    for index, section in enumerate(CHECKLIST_SECTIONS, start=1):
        universal.append([index, section["title"], section["mode"], section["expected"]])
    for cell in universal[1]:
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for row in universal.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for index, width in enumerate([6, 36, 18, 72], start=1):
        universal.column_dimensions[get_column_letter(index)].width = width
    universal.freeze_panes = "A2"
    universal.auto_filter.ref = universal.dimensions

    if page_profiles:
        profile_sheet = workbook.create_sheet("PageProfile")
        profile_headers = [
            "page_id",
            "profile_source",
            "runtime_profile_path",
            "entry_url",
            "view_page",
            "ready_selector",
            "page_spec_path",
            "page_evidence_path",
            "page_spec_prompt_path",
            "page_spec_summary",
            "page_type",
            "capabilities",
            "state_count",
            "operation_count",
            "form_count",
            "file_count",
            "button_count",
            "link_count",
            "input_count",
            "select_count",
            "textarea_count",
            "table_count",
            "submit_action_count",
            "download_action_count",
            "close_action_count",
        ]
        profile_sheet.append(profile_headers)
        for profile in page_profiles:
            counts = profile.get("counts") or {}
            capabilities = profile.get("capabilities") or {}
            enabled_caps = ",".join(name for name, enabled in capabilities.items() if enabled)
            profile_sheet.append(
                [
                    profile.get("page_id"),
                    profile.get("profile_source"),
                    profile.get("runtime_profile_path"),
                    profile.get("entry_url"),
                    profile.get("view_page"),
                    profile.get("ready_selector"),
                    profile.get("page_spec_path"),
                    profile.get("page_evidence_path"),
                    profile.get("page_spec_prompt_path"),
                    profile.get("page_spec_summary"),
                    profile.get("page_type"),
                    enabled_caps,
                    profile.get("state_count"),
                    profile.get("operation_count"),
                    counts.get("form", 0),
                    counts.get("file", 0),
                    counts.get("button", 0),
                    counts.get("link", 0),
                    counts.get("input", 0),
                    counts.get("select", 0),
                    counts.get("textarea", 0),
                    counts.get("table", 0),
                    len(profile.get("submit_actions") or []),
                    len(profile.get("download_actions") or []),
                    len(profile.get("close_actions") or []),
                ]
            )
        for cell in profile_sheet[1]:
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for row in profile_sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for index, width in enumerate([34, 22, 54, 34, 34, 34, 54, 54, 54, 72, 20, 72, 12, 16, 12, 12, 12, 12, 12, 12, 12, 12, 18, 20, 18], start=1):
            profile_sheet.column_dimensions[get_column_letter(index)].width = width
        profile_sheet.freeze_panes = "A2"
        profile_sheet.auto_filter.ref = profile_sheet.dimensions

    if skipped_templates:
        skipped_sheet = workbook.create_sheet("SkippedTemplates")
        skipped_headers = [
            "page_id",
            "template_id",
            "case_type",
            "status",
            "reason",
            "missing_capabilities",
            "matched_capabilities",
        ]
        skipped_sheet.append(skipped_headers)
        for skipped in skipped_templates:
            skipped_sheet.append(
                [
                    skipped.get("page_id"),
                    skipped.get("template_id"),
                    skipped.get("case_type"),
                    skipped.get("status"),
                    skipped.get("reason"),
                    skipped.get("missing_capabilities"),
                    skipped.get("matched_capabilities"),
                ]
            )
        for cell in skipped_sheet[1]:
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for row in skipped_sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for index, width in enumerate([34, 24, 24, 16, 36, 34, 72], start=1):
            skipped_sheet.column_dimensions[get_column_letter(index)].width = width
        skipped_sheet.freeze_panes = "A2"
        skipped_sheet.auto_filter.ref = skipped_sheet.dimensions

    detail = workbook.create_sheet("Checklist")
    headers = [
        "case_id",
        "parent_case_id",
        "viewpoint_id",
        "priority",
        "page_id",
        "line",
        "section",
        "element_kind",
        "locator",
        "test_title",
        "objective",
        "operation",
        "expected_result",
        "automation_mode",
        "case_type",
        "action_type",
        "test_data",
        "submit_locator",
        "expected_type",
        "expected_value",
        "pre_steps",
        "main_step",
        "evidence",
        "enabled",
        "generated_by",
        "matched_capabilities",
        "destructive",
    ]
    detail.append(headers)
    for index, case in enumerate(cases, start=1):
        case_id = case.case_id or f"{Path(case.page).stem or 'PAGE'}-{index:05d}"
        detail.append([
            case_id,
            case.parent_case_id,
            case.viewpoint_id,
            case.priority or case.severity,
            case.page,
            case.line,
            case.kind,
            case.kind,
            case.locator,
            case.title,
            case.objective,
            case.steps,
            case.expected,
            case.automation_mode,
            case.case_type,
            case.action_type,
            case.test_data,
            case.submit_locator,
            case.expected_type,
            case.expected_value,
            case.pre_steps,
            case.main_step,
            case.evidence,
            case.enabled,
            case.generated_by,
            case.matched_capabilities,
            case.destructive,
        ])

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in detail[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for row in detail.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    widths = [46, 46, 24, 10, 44, 8, 18, 18, 34, 42, 56, 64, 64, 18, 18, 18, 34, 34, 18, 28, 42, 42, 58, 10, 20, 72, 12]
    for index, width in enumerate(widths, start=1):
        detail.column_dimensions[get_column_letter(index)].width = width
    detail.freeze_panes = "A2"
    detail.auto_filter.ref = detail.dimensions

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def load_scan(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON input: {path} ({exc})") from exc


def export_page_spec_inputs_report(
    scan_data: Dict[str, Any],
    *,
    runtime_profile_dir: Path = DEFAULT_RUNTIME_PROFILE_DIR,
    include_runtime_profile_dir: bool = True,
    target_pages: Optional[Sequence[str]] = None,
    page_spec_dir: Path = DEFAULT_PAGE_SPEC_DIR,
) -> List[Dict[str, Any]]:
    _, skipped, profiles = plan_page_specific_cases(
        scan_data,
        runtime_profile_dir=runtime_profile_dir,
        include_runtime_profile_dir=include_runtime_profile_dir,
        target_pages=target_pages,
        page_spec_dir=page_spec_dir,
        export_page_spec_inputs=True,
    )
    rows: List[Dict[str, Any]] = []
    for profile in profiles:
        rows.append(
            {
                "page_id": profile.get("page_id"),
                "page_evidence_path": profile.get("page_evidence_path"),
                "page_spec_prompt_path": profile.get("page_spec_prompt_path"),
                "page_spec_path": profile.get("page_spec_path"),
            }
        )
    if skipped:
        rows.append({"notes": skipped})
    return rows


def write_report(
    scan_data: Dict[str, Any],
    output: Optional[Path],
    *,
    runtime_profile_dir: Path = DEFAULT_RUNTIME_PROFILE_DIR,
    include_runtime_profile_dir: bool = True,
    target_pages: Optional[Sequence[str]] = None,
    page_spec_dir: Path = DEFAULT_PAGE_SPEC_DIR,
    use_page_spec: bool = False,
    export_page_spec_inputs: bool = False,
) -> None:
    planned_skipped_templates: Optional[List[Dict[str, Any]]] = None
    planned_page_profiles: Optional[List[Dict[str, Any]]] = None
    if uses_page_specific_planner(scan_data):
        cases, planned_skipped_templates, planned_page_profiles = plan_page_specific_cases(
            scan_data,
            runtime_profile_dir=runtime_profile_dir,
            include_runtime_profile_dir=include_runtime_profile_dir,
            target_pages=target_pages,
            page_spec_dir=page_spec_dir,
            use_page_spec=use_page_spec,
            export_page_spec_inputs=export_page_spec_inputs,
        )
    else:
        cases = generate_cases(
            scan_data,
            runtime_profile_dir=runtime_profile_dir,
            include_runtime_profile_dir=include_runtime_profile_dir,
            target_pages=target_pages,
            page_spec_dir=page_spec_dir,
            use_page_spec=use_page_spec,
            export_page_spec_inputs=export_page_spec_inputs,
        )
    if output and output.suffix.lower() == ".xlsx":
        write_excel(
            output,
            scan_data,
            cases,
            runtime_profile_dir=runtime_profile_dir,
            include_runtime_profile_dir=include_runtime_profile_dir,
            target_pages=target_pages,
            page_spec_dir=page_spec_dir,
            use_page_spec=use_page_spec,
            export_page_spec_inputs=export_page_spec_inputs,
            planned_skipped_templates=planned_skipped_templates,
            planned_page_profiles=planned_page_profiles,
        )
        return
    report = render_markdown(scan_data, cases)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        return
    print(report)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a JSP migration test checklist from jsp_scanner/page_mapping JSON.")
    parser.add_argument("input", type=Path, help="elements.json or page_mapping.json generated by moonlight tools")
    parser.add_argument("-o", "--output", type=Path, help="Report output path. Use .md or .xlsx. Defaults to stdout Markdown.")
    parser.add_argument(
        "--runtime-profile-dir",
        type=Path,
        default=DEFAULT_RUNTIME_PROFILE_DIR,
        help="Runtime page profile JSON directory. Defaults to generated/valid/runtime_profile.",
    )
    parser.add_argument(
        "--include-runtime-profiles",
        action="store_true",
        help="Append all existing runtime_profile/*.json pages to the checklist.",
    )
    parser.add_argument(
        "--target-page",
        action="append",
        default=[],
        help="Limit generated checklist/PageSpec to one JSP/page. Can be passed multiple times.",
    )
    parser.add_argument(
        "--target-pages",
        default="",
        help="Comma-separated JSP/page list to generate.",
    )
    parser.add_argument(
        "--page-spec-dir",
        type=Path,
        default=DEFAULT_PAGE_SPEC_DIR,
        help="Directory for PageSpec evidence, prompt, and manually generated PageSpec JSON files.",
    )
    parser.add_argument(
        "--export-page-spec-inputs",
        action="store_true",
        help="Write *.page_evidence.json and *.page_spec_prompt.md files for manual web-model PageSpec generation.",
    )
    parser.add_argument(
        "--page-spec-inputs-only",
        action="store_true",
        help="Only export PageSpec evidence/prompt files and print their paths as JSON. Does not write checklist output.",
    )
    parser.add_argument(
        "--use-page-spec",
        action="store_true",
        help="Read manually generated *.page_spec.json files from --page-spec-dir and generate checklist cases from them.",
    )
    args = parser.parse_args()
    if not args.input.exists():
        raise SystemExit(f"Input JSON does not exist: {args.input}")
    target_pages = parse_target_pages([*args.target_page, args.target_pages])
    scan_data = load_scan(args.input)
    if args.page_spec_inputs_only:
        rows = export_page_spec_inputs_report(
            scan_data,
            runtime_profile_dir=args.runtime_profile_dir,
            include_runtime_profile_dir=args.include_runtime_profiles,
            target_pages=target_pages,
            page_spec_dir=args.page_spec_dir,
        )
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    try:
        write_report(
            scan_data,
            args.output,
            runtime_profile_dir=args.runtime_profile_dir,
            include_runtime_profile_dir=args.include_runtime_profiles,
            target_pages=target_pages,
            page_spec_dir=args.page_spec_dir,
            use_page_spec=args.use_page_spec,
            export_page_spec_inputs=args.export_page_spec_inputs,
        )
    except PageSpecError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
