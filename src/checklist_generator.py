import argparse
import html
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


SEVERITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}
CASE_GENERATING_KINDS = {"form", "file", "button", "link"}


@dataclass(frozen=True)
class TestCase:
    page: str
    kind: str
    locator: str
    line: str
    title: str
    objective: str
    steps: str
    expected: str
    severity: str
    evidence: str


def as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def first_attr(attributes: Dict[str, Any], *names: str) -> str:
    lowered = {str(key).lower(): value for key, value in attributes.items()}
    for name in names:
        if name in attributes:
            return as_text(attributes[name])
        value = lowered.get(name.lower())
        if value is not None:
            return as_text(value)
    return ""


def element_label(element: Dict[str, Any]) -> str:
    attributes = element.get("attributes", {})
    label = first_attr(
        attributes,
        "styleId",
        "id",
        "name",
        "property",
        "path",
        "value",
        "title",
        "href",
        "action",
    )
    if label:
        return label
    return as_text(element.get("tag"), "unknown")


def element_evidence(element: Dict[str, Any]) -> str:
    raw = as_text(element.get("raw"))
    if raw:
        return raw
    attributes = element.get("attributes", {})
    if not attributes:
        return as_text(element.get("tag"), "")
    pairs = [f"{key}={as_text(value)}" for key, value in sorted(attributes.items())]
    return ", ".join(pairs)


def page_entries(scan_data: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    if "pages" in scan_data:
        yield from scan_data.get("pages", [])
        return
    # Support data from page_mapping.py (high_risk_pages, medium_risk_pages, etc.)
    for key in ["high_risk_pages", "medium_risk_pages", "page_mappings"]:
        if key in scan_data and isinstance(scan_data[key], list):
            yield from scan_data[key]
            return
    yield {
        "source": scan_data.get("source", scan_data.get("root", "<unknown>")),
        "counts": scan_data.get("counts", {}),
        "elements": scan_data.get("elements", []),
    }


def common_element_fields(page: str, element: Dict[str, Any]) -> Dict[str, str]:
    evidence = element_evidence(element)
    related_fields = element.get("related_fields", [])
    if related_fields:
        evidence = f"{evidence}\n字段完整性校验：{field_summary(related_fields)}"
    return {
        "page": page,
        "kind": as_text(element.get("kind"), "unknown"),
        "locator": as_text(element.get("locator"), "(locator missing)"),
        "line": as_text(element.get("line"), "-"),
        "evidence": evidence,
    }


def field_summary(fields: Sequence[Dict[str, Any]]) -> str:
    summary = []
    for field in fields[:20]:
        label = element_label(field)
        line = as_text(field.get("line"), "-")
        locator = as_text(field.get("locator"), "(locator missing)")
        summary.append(f"{as_text(field.get('tag'), 'field')} `{label}` line={line} locator={locator}")
    if len(fields) > 20:
        summary.append(f"... and {len(fields) - 20} more field(s)")
    return "; ".join(summary)


def attach_related_fields(elements: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach field records to the nearest preceding form while suppressing standalone field cases."""
    output: List[Dict[str, Any]] = []
    current_form: Optional[Dict[str, Any]] = None
    unassigned_fields: List[Dict[str, Any]] = []

    for element in elements:
        kind = as_text(element.get("kind")).lower()
        if kind == "field":
            field = dict(element)
            if current_form is None:
                unassigned_fields.append(field)
            else:
                current_form.setdefault("related_fields", []).append(field)
            continue

        copied = dict(element)
        if kind == "form":
            copied["related_fields"] = []
            if unassigned_fields:
                copied["related_fields"].extend(unassigned_fields)
                unassigned_fields = []
            current_form = copied
        output.append(copied)

    return output


def form_cases(page: str, element: Dict[str, Any]) -> List[TestCase]:
    fields = common_element_fields(page, element)
    attributes = element.get("attributes", {})
    action = first_attr(attributes, "action") or "(current page/default action)"
    method = first_attr(attributes, "method") or "unspecified"
    label = element_label(element)
    return [
        TestCase(
            title=f"表单提交主路径：{label}",
            objective=f"确认 Struts 表单迁移后仍提交到预期业务入口，action={action}，method={method}。",
            steps="填写一组公司环境认可的正常数据，提交表单，观察页面跳转、提示消息和后端状态。",
            expected="提交成功；无 4xx/5xx；新旧系统的跳转、提示文案、关键字段落库结果一致。",
            severity="High",
            **fields,
        ),
        TestCase(
            title=f"必填与空值校验：{label}",
            objective="确认服务端校验未因迁移丢失，尤其是仅靠前端控制的字段。",
            steps="将可见输入项逐个置空；对隐藏但可篡改字段使用浏览器开发者工具移除或改空后提交。",
            expected="阻止提交或返回明确业务错误；不得出现空指针、SQL 异常、默认越权值或静默成功。",
            severity="High",
            **fields,
        ),
        TestCase(
            title=f"边界长度与特殊字符：{label}",
            objective="覆盖字段长度、编码和转义差异，提前发现 Struts 到 Spring 的绑定变化。",
            steps="输入最大长度、超长文本、日文/中文、半角假名、换行、单引号、双引号、反斜杠后提交。",
            expected="长度限制和错误提示稳定；多字节字符不乱码；特殊字符不破坏页面或 SQL/API 调用。",
            severity="Medium",
            **fields,
        ),
        TestCase(
            title=f"XSS 探针注入：{label}",
            objective="确认提交值在当前页、确认页、列表页和错误页均被正确转义。",
            steps="在文本字段输入 <script>alert(1)</script>、\"><img src=x onerror=alert(1)> 等探针并提交，再回看展示位置。",
            expected="探针作为普通文本显示或被拒绝；浏览器不执行脚本；响应中无未转义用户输入。",
            severity="High",
            **fields,
        ),
    ]


def file_cases(page: str, element: Dict[str, Any]) -> List[TestCase]:
    fields = common_element_fields(page, element)
    attributes = element.get("attributes", {})
    accept = first_attr(attributes, "accept") or "(not specified)"
    label = element_label(element)
    return [
        TestCase(
            title=f"允许类型上传：{label}",
            objective=f"确认文件上传控件接受业务允许的文件类型，accept={accept}。",
            steps="选择一份公司环境允许的最小有效文件并随表单提交。",
            expected="上传成功；文件名、大小、内容摘要或预览结果与旧系统一致。",
            severity="High",
            **fields,
        ),
        TestCase(
            title=f"文件类型伪装校验：{label}",
            objective="确认后端根据真实内容和扩展名共同校验，不能只信任浏览器 accept。",
            steps="上传改名文件，例如 .exe 改 .jpg、文本文件改 .pdf、MIME 与扩展名不一致的文件。",
            expected="非法类型被拒绝；错误消息清晰；服务端不保存危险文件。",
            severity="High",
            **fields,
        ),
        TestCase(
            title=f"超大文件上传：{label}",
            objective="验证 Spring multipart 限制、反向代理限制和业务提示是否一致。",
            steps="上传超过业务上限的文件；再上传接近上限的边界文件。",
            expected="超限文件被稳定拒绝且无 500/504；边界内文件可上传；临时文件被清理。",
            severity="High",
            **fields,
        ),
        TestCase(
            title=f"危险文件名处理：{label}",
            objective="覆盖路径穿越、编码和日志污染风险。",
            steps="上传文件名包含 ../、..\\、空格、日文/中文、超长名称、换行符、单双引号的文件。",
            expected="文件名被规范化或拒绝；不能写入目标目录外；下载/预览/日志不乱码不注入。",
            severity="Medium",
            **fields,
        ),
    ]


def button_cases(page: str, element: Dict[str, Any]) -> List[TestCase]:
    fields = common_element_fields(page, element)
    attributes = element.get("attributes", {})
    onclick = first_attr(attributes, "onclick")
    label = element_label(element)
    return [
        TestCase(
            title=f"按钮点击主路径：{label}",
            objective="确认按钮迁移后仍触发相同业务动作。",
            steps="在正常页面状态下点击按钮；记录请求、跳转、弹窗、页面刷新和后端状态变化。",
            expected="新旧系统行为一致；按钮不会无响应、重复触发或触发错误 action。",
            severity="High",
            **fields,
        ),
        TestCase(
            title=f"重复点击与防重提交：{label}",
            objective="确认点击节流、按钮禁用和后端幂等策略仍有效。",
            steps="快速双击/多击按钮；网络慢速条件下重复点击；刷新后重放提交请求。",
            expected="只产生一次有效业务处理；重复请求被拦截或安全幂等；页面提示不混乱。",
            severity="High",
            **fields,
        ),
        TestCase(
            title=f"前端脚本依赖检查：{label}",
            objective=f"确认 onclick 或关联脚本迁移后没有丢失。onclick={onclick or '(not specified)'}。",
            steps="打开浏览器控制台点击按钮；观察 JS 错误、缺失函数、未定义变量和被拦截请求。",
            expected="控制台无脚本错误；所有动态校验、确认框、参数拼接和页面状态更新正常。",
            severity="Medium",
            **fields,
        ),
    ]


def link_cases(page: str, element: Dict[str, Any]) -> List[TestCase]:
    fields = common_element_fields(page, element)
    attributes = element.get("attributes", {})
    href = first_attr(attributes, "href", "action", "page") or "(not specified)"
    label = element_label(element)
    return [
        TestCase(
            title=f"链接导航主路径：{label}",
            objective=f"确认迁移后的链接仍进入正确页面或业务动作，target={href}。",
            steps="点击链接，记录目标 URL、请求参数、页面标题和关键内容。",
            expected="新旧系统目标一致；参数未丢失；无 404/500；登录态和权限状态保持正确。",
            severity="High",
            **fields,
        ),
        TestCase(
            title=f"参数篡改与权限校验：{label}",
            objective="确认链接中的 id、mode、returnUrl 等参数不能绕过权限或访问他人数据。",
            steps="修改 URL 参数为不存在、越权、空值、特殊字符和超长值后访问。",
            expected="非法参数被拒绝或回到安全页面；不得泄漏数据、堆栈或内部路径。",
            severity="High",
            **fields,
        ),
        TestCase(
            title=f"返回与打开方式：{label}",
            objective="确认浏览器返回、刷新、新标签页打开时状态一致。",
            steps="点击链接后执行返回、刷新、新标签页打开；对带弹窗或下载行为的链接额外确认。",
            expected="页面状态可恢复；不会重复提交危险操作；下载/弹窗行为与旧系统一致。",
            severity="Medium",
            **fields,
        ),
    ]


CASE_BUILDERS = {
    "form": form_cases,
    "file": file_cases,
    "button": button_cases,
    "link": link_cases,
}


def generate_cases(scan_data: Dict[str, Any]) -> List[TestCase]:
    cases: List[TestCase] = []
    for page in page_entries(scan_data):
        page_name = as_text(page.get("source") or page.get("page_id"), "<unknown>")
        # elements may be inside 'missing_legacy_elements' or just 'elements'
        elements = list(page.get("elements", []))
        if not elements:
            # If coming from page_mapping, we might want to test the missing ones
            elements.extend(page.get("missing_legacy_elements", []))
            
        for element in attach_related_fields(elements):
            kind = as_text(element.get("kind")).lower()
            if kind not in CASE_GENERATING_KINDS:
                continue
            builder = CASE_BUILDERS.get(kind)
            if builder is None:
                continue
            cases.extend(builder(page_name, element))
    return sorted(cases, key=lambda item: (item.page, SEVERITY_ORDER.get(item.severity, 99), item.kind, item.title))


def summarize_counts(scan_data: Dict[str, Any], cases: Sequence[TestCase]) -> Dict[str, int]:
    totals = {str(key): int(value) for key, value in scan_data.get("totals", {}).items()}
    if not totals:
        for page in page_entries(scan_data):
            for kind, count in page.get("counts", {}).items():
                totals[str(kind)] = totals.get(str(kind), 0) + int(count)
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


def render_markdown(scan_data: Dict[str, Any], cases: Sequence[TestCase]) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    totals = summarize_counts(scan_data, cases)
    lines = [
        "# 自动化测试建议报告",
        "",
        f"- 生成时间：{generated_at}",
        f"- 扫描根路径：{as_text(scan_data.get('root'), as_text(scan_data.get('source'), '<unknown>'))}",
        f"- 页面数量：{len(list(page_entries(scan_data)))}",
        f"- 元素统计：form={totals.get('form', 0)}，file={totals.get('file', 0)}，button={totals.get('button', 0)}，link={totals.get('link', 0)}",
        f"- 建议用例数：{totals.get('test_cases', 0)}",
        "",
        "## 主祭优先执行清单",
        "",
    ]

    high_cases = [case for case in cases if case.severity == "High"]
    for index, case in enumerate(high_cases[:12], start=1):
        lines.append(f"{index}. [{case.page}:{case.line}] {case.title} - {case.objective}")
    if not high_cases:
        lines.append("未发现可生成的高优先级用例。请确认 elements.json 中包含 form/file/button/link 元素。")

    lines.extend(
        [
            "",
            "## 用例明细",
            "",
            "| # | 优先级 | 页面 | 行 | 类型 | 定位器 | 用例 | 操作建议 | 期望结果 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for index, case in enumerate(cases, start=1):
        lines.append(
            "| "
            + " | ".join(
                markdown_escape_cell(value)
                for value in [
                    str(index),
                    case.severity,
                    case.page,
                    case.line,
                    case.kind,
                    case.locator,
                    f"{case.title}\n{case.objective}",
                    case.steps,
                    case.expected,
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## 元素证据",
            "",
            "| 页面 | 行 | 类型 | 定位器 | JSP 线索 |",
            "|---|---|---|---|---|",
        ]
    )
    for case in unique_evidence_cases(cases):
        lines.append(
            "| "
            + " | ".join(
                markdown_escape_cell(value)
                for value in [case.page, case.line, case.kind, case.locator, case.evidence]
            )
            + " |"
        )

    return "\n".join(lines) + "\n"


def write_excel(path: Path, scan_data: Dict[str, Any], cases: Sequence[TestCase]) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:  # pragma: no cover - depends on optional runtime package
        raise SystemExit("Excel output requires openpyxl. Install dependencies with: pip install -r requirements.txt") from exc

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"
    totals = summarize_counts(scan_data, cases)
    summary_rows = [
        ("生成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("扫描根路径", as_text(scan_data.get("root"), as_text(scan_data.get("source"), "<unknown>"))),
        ("页面数量", len(list(page_entries(scan_data)))),
        ("form", totals.get("form", 0)),
        ("file", totals.get("file", 0)),
        ("button", totals.get("button", 0)),
        ("link", totals.get("link", 0)),
        ("建议用例数", totals.get("test_cases", 0)),
    ]
    for row in summary_rows:
        summary.append(row)
    summary.column_dimensions["A"].width = 18
    summary.column_dimensions["B"].width = 80

    detail = workbook.create_sheet("Checklist")
    headers = ["#", "优先级", "页面", "行", "类型", "定位器", "用例", "目标", "操作建议", "期望结果", "JSP 线索"]
    detail.append(headers)
    for index, case in enumerate(cases, start=1):
        detail.append(
            [
                index,
                case.severity,
                case.page,
                case.line,
                case.kind,
                case.locator,
                case.title,
                case.objective,
                case.steps,
                case.expected,
                case.evidence,
            ]
        )

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in detail[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for row in detail.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    widths = [6, 10, 44, 8, 10, 24, 32, 46, 56, 56, 52]
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


def write_report(scan_data: Dict[str, Any], output: Optional[Path]) -> None:
    if output and output.suffix.lower() == ".xlsx":
        write_excel(output, scan_data, generate_cases(scan_data))
        return

    report = render_markdown(scan_data, generate_cases(scan_data))
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        return
    print(report)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a JSP migration test checklist from jsp_scanner JSON.")
    parser.add_argument("input", type=Path, help="elements.json generated by src/jsp_scanner.py")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Report output path. Use .md for Markdown or .xlsx for Excel. Defaults to stdout Markdown.",
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"Input JSON does not exist: {args.input}")
    write_report(load_scan(args.input), args.output)


if __name__ == "__main__":
    main()
