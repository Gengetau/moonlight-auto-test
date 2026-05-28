# Codex 实装任务文档：基于模糊模板生成页面专属 Case

## 背景

当前仓库：`Gengetau/moonlight-auto-test`

该项目是用于 Patlics / PatentSQUARE Struts → Spring 迁移场景的 UI 回归自动化工具。

当前工具链大致为：

```text
JSP / struts-config / mapping
  ↓
jsp_scanner.py
  ↓
page_mapping.py
  ↓
checklist_generator.py
  ↓
regression_engine.py + action_executor.py
  ↓
HTML report
```

目前 `checklist_generator.py` 已经可以生成大量 `automation_mode=auto` 的 checklist case。

但是当前问题是：

```text
通用模糊模板生成的 auto case 太泛。
很多页面虽然生成了大量 case，但页面本身并不具备执行这些 case 的条件。
```

例如：

- 没有搜索框的页面不应该生成 search case
- 没有 table 的页面不应该生成 table verify case
- 只有 file input、但没有 submit action 的页面不应该生成 upload_submit
- 只有 close button 的页面不应该生成复杂业务跳转 case
- download / popup / close_window 需要按页面能力生成，而不是全页面套模板

下一步目标是：

```text
从“统一模糊模板批量生成 checklist”
升级为
“根据每个页面的结构画像 Page Profile，生成页面专属 Case”
```

---

## 目标

请实现一个页面专属 case 生成机制。

核心设计：

```text
模糊模板 fuzzy templates
  ↓
页面结构画像 PageProfile
  ↓
能力匹配 Capability Matching
  ↓
页面专属 Case Planning
  ↓
Checklist sheet / RuntimeCase
```

最终效果：

1. 每个页面先生成一个 `PageProfile`
2. 根据 `PageProfile.capabilities` 从模糊模板中选择适合该页面的 case
3. 不满足条件的模板不生成到 Checklist
4. 被跳过的模板需要记录到 `SkippedTemplates` sheet，方便调试
5. Checklist 中只保留当前页面真正可执行或有意义的 case
6. 尽量保持现有执行器兼容，不要大改 `RegressionEngine`

---

## 建议新增模块

新增文件：

```text
src/page_case_planner.py
```

该文件负责：

- 从 page mapping 构建 PageProfile
- 定义 fuzzy template registry
- 根据 profile 生成页面专属 checklist cases
- 输出 skipped template diagnostics

建议包含以下类：

```python
class PageProfileBuilder:
    def build(self, page_mapping: dict) -> dict:
        ...


class CaseTemplateRegistry:
    def list_templates(self) -> list[dict]:
        ...


class PageCasePlanner:
    def plan(self, page_mapping: dict) -> tuple[list[dict], list[dict], dict]:
        ...
```

返回值建议：

```python
cases, skipped_templates, profile = planner.plan(page_mapping)
```

---

## PageProfile 设计

PageProfile 是每个页面的结构画像。

请从 `page_mapping` 中提取已有信息。

兼容字段来源包括但不限于：

```text
page_id
legacy_elements
new_elements
full_action_steps
executable_cases
locator_changes
missing_legacy_elements
missing_new_elements
entry_url
view_page
ready_selector
```

如果字段名不完全一致，请用防御式读取。

示例结构：

```json
{
  "page_id": "ProjectMemberUploadDisp.jsp",
  "entry_url": "ProjectMemberUpload.do",
  "view_page": "ProjectMemberUploadDisp.jsp",
  "ready_selector": "input[name='uploadFile']",

  "counts": {
    "form": 1,
    "file": 1,
    "button": 2,
    "link": 1,
    "input": 0,
    "select": 0,
    "textarea": 0,
    "table": 0
  },

  "forms": [
    {
      "locator": "form[action*='ProjectMemberUpload']",
      "action": "/ProjectMemberUpload.do",
      "method": "post",
      "enctype": "multipart/form-data",
      "target": "frMain"
    }
  ],

  "files": [
    {
      "locator": "input[name='uploadFile']",
      "name": "uploadFile",
      "property": "uploadFile"
    }
  ],

  "submit_actions": [
    {
      "locator": "input[name='entry']",
      "onclick": "fnSubmit('/ProjectMemberUpload.do')",
      "action_type": "submit"
    }
  ],

  "download_actions": [
    {
      "locator": "a[onclick*='TemplateDownload']",
      "onclick": "fnSubmit('/ProjectMemberUploadTemplateDownload.do')",
      "action_type": "download"
    }
  ],

  "close_actions": [
    {
      "locator": "input[onclick='javascript:parent.close();']",
      "onclick": "javascript:parent.close();",
      "action_type": "close_window"
    }
  ],

  "tables": [],

  "capabilities": {
    "initial_display": true,
    "form": true,
    "file_upload": true,
    "form_submit": true,
    "upload_submit": true,
    "template_download": true,
    "close_window": true,
    "text_input": false,
    "select": false,
    "search": false,
    "result_table": false,
    "popup": false
  }
}
```

---

## Capability 判定规则

请至少支持以下 capabilities。

### 通用

```text
initial_display
```

所有页面默认 true。

### form

条件：

```text
存在 form 元素
```

### file_upload

条件：

```text
存在 kind=file 或 input[type=file]
```

### form_submit

条件之一：

```text
存在 action_hint=submit
存在 onclick 包含 fnSubmit
存在 form action
存在 button/input submit
```

### upload_submit

条件：

```text
file_upload == true
且 form_submit == true
```

### template_download

条件之一：

```text
onclick / href / action 包含 TemplateDownload
action_hint == download
label_key / raw 文本包含 download / ダウンロード / template / 雛形
```

### close_window

条件之一：

```text
onclick 包含 window.close
onclick 包含 parent.close
action_hint == close_window
```

### text_input

条件：

```text
存在 input[type=text/password/search] 或 textarea
```

注意 file、hidden、button 不算 text_input。

### select

条件：

```text
存在 select
```

### search

条件之一：

```text
button label / name / onclick / action 包含 search / 検索
存在搜索按钮且有 text_input/select
```

### result_table

条件：

```text
存在 table
```

### popup

条件之一：

```text
form target 不是空且不是 _self
onclick / raw 包含 window.open
submit action 可能打开新窗口
```

---

## Fuzzy Template Registry

请先在代码中定义默认模板，不必一开始做外部 YAML。

建议结构：

```python
DEFAULT_CASE_TEMPLATES = [
    {
        "template_id": "initial_display",
        "case_type": "initial_display",
        "action_type": "snapshot",
        "requires": ["initial_display"],
        "priority": 10,
    },
    {
        "template_id": "upload_select",
        "case_type": "upload_select",
        "action_type": "upload",
        "requires": ["file_upload"],
        "priority": 30,
    },
    {
        "template_id": "upload_submit",
        "case_type": "upload_submit",
        "action_type": "upload_submit",
        "requires": ["upload_submit"],
        "priority": 40,
    },
    {
        "template_id": "upload_without_file",
        "case_type": "upload_without_file",
        "action_type": "submit",
        "requires": ["form_submit", "file_upload"],
        "priority": 50,
    },
    {
        "template_id": "download_template",
        "case_type": "download_template",
        "action_type": "download",
        "requires": ["template_download"],
        "priority": 60,
    },
    {
        "template_id": "close_window",
        "case_type": "close_window",
        "action_type": "close_window",
        "requires": ["close_window"],
        "priority": 70,
    },
    {
        "template_id": "search_normal",
        "case_type": "search_normal",
        "action_type": "search",
        "requires": ["search"],
        "priority": 80,
    },
    {
        "template_id": "result_table_verify",
        "case_type": "result_table_verify",
        "action_type": "verify",
        "requires": ["result_table"],
        "priority": 90,
    },
]
```

---

## Case 生成规则

每个生成出的 case 应尽量兼容现有 Checklist sheet 字段。

建议字段：

```text
case_id
page_id
title
objective
precondition
steps
expected
severity
risk
automation_mode
enabled
case_type
action_type
locator
test_data
submit_locator
expected_type
expected_value
pre_steps
main_step
generated_by
matched_capabilities
```

### case_id 命名

建议：

```text
{page_base}-{template_id}-{index}
```

例如：

```text
ProjectMemberUploadDisp-upload_submit-001
```

### automation_mode

满足 requires 且可实例化 locator 时：

```text
auto
```

缺 locator 但仍有测试意义时：

```text
assist
```

本次任务优先只生成 auto。assist 可保留但不要滥用。

### enabled

默认：

```text
true
```

---

## Locator 实例化规则

### upload_select

需要：

```text
file locator
```

优先取：

```text
profile.files[0].locator
```

输出：

```json
{
  "action_type": "upload",
  "locator": "input[name='uploadFile']",
  "test_data": "${UPLOAD_FILE}"
}
```

### upload_submit

需要：

```text
file locator
submit locator 或 submit script 或 form locator
```

优先级：

```text
1. submit_actions[0].locator
2. executable_cases 里的 main_step.locator
3. forms[0].locator
```

输出：

```json
{
  "action_type": "upload_submit",
  "locator": "input[name='uploadFile']",
  "test_data": "${UPLOAD_FILE}",
  "submit_locator": "input[name='entry']",
  "expected_type": "page_or_message",
  "expected_value": ""
}
```

如果 submit action 是 `fnSubmit('/xxx.do')`，可以额外输出：

```json
{
  "submit_script": "fnSubmit('/xxx.do')"
}
```

但不要破坏现有字段。

### upload_without_file

需要：

```text
submit action
```

输出：

```json
{
  "action_type": "submit",
  "submit_locator": "input[name='entry']",
  "expected_type": "message_or_stay",
  "expected_value": ""
}
```

### download_template

需要：

```text
download action locator
```

输出：

```json
{
  "action_type": "download",
  "locator": "a[onclick*='TemplateDownload']",
  "expected_type": "download",
  "expected_value": ""
}
```

### close_window

需要：

```text
close action locator
```

输出：

```json
{
  "action_type": "close_window",
  "locator": "input[onclick='javascript:parent.close();']",
  "expected_type": "window_closed",
  "expected_value": ""
}
```

---

## SkippedTemplates sheet

新增或更新 checklist Excel 输出时，请增加 `SkippedTemplates` sheet。

字段建议：

```text
page_id
template_id
case_type
status
reason
missing_capabilities
matched_capabilities
```

示例：

```text
ProjectMemberUploadDisp.jsp | search_normal | search_normal | skipped | missing required capabilities | search | initial_display,file_upload,form_submit
```

跳过原因建议包括：

```text
missing required capabilities
missing file locator
missing submit locator
missing download locator
missing close locator
not applicable to page profile
```

---

## PageProfile sheet

新增或更新 checklist Excel 输出时，请增加 `PageProfile` sheet。

字段建议：

```text
page_id
entry_url
view_page
ready_selector
capabilities
form_count
file_count
button_count
link_count
input_count
select_count
textarea_count
table_count
submit_action_count
download_action_count
close_action_count
```

其中 `capabilities` 可以是逗号拼接字符串：

```text
initial_display,file_upload,form_submit,upload_submit,template_download
```

---

## 修改 checklist_generator.py

请将 `checklist_generator.py` 的生成逻辑改为优先使用 `PageCasePlanner`。

伪代码：

```python
from src.page_case_planner import PageCasePlanner

planner = PageCasePlanner()

for page_mapping in page_mappings:
    cases, skipped, profile = planner.plan(page_mapping)

    checklist_rows.extend(cases)
    skipped_rows.extend(skipped)
    profile_rows.append(profile_to_row(profile))
```

注意：

1. 保留现有 CLI 参数
2. 保留现有输出路径
3. 不破坏现有 Checklist sheet 字段
4. 新增 PageProfile / SkippedTemplates sheet
5. 如果现有代码已经有 executable_cases，请不要删除，可作为实例化 case 的辅助信息
6. 如果 planner 生成 0 个 case，至少生成 initial_display

---

## 与 RegressionEngine 的关系

本次任务尽量不要大改 `regression_engine.py`。

只需要确保 Checklist sheet 中生成的 auto case 能被当前 runtime loader 读取。

如果发现当前 loader 需要字段，请在 checklist_generator 输出中补齐字段，而不是优先改执行器。

---

## 兼容性要求

请保证：

```text
python -m pytest
python -m src.checklist_generator
```

不会因为 import 路径失败。

如果使用 `src.page_case_planner` 导入失败，请兼容脚本直接运行场景：

```python
try:
    from src.page_case_planner import PageCasePlanner
except ImportError:
    from page_case_planner import PageCasePlanner
```

---

## 测试建议

请至少增加或手动验证以下场景：

### 页面 A：只有 file + submit

期望生成：

```text
initial_display
upload_select
upload_submit
upload_without_file
```

不应生成：

```text
search_normal
result_table_verify
download_template
```

### 页面 B：file + template download + close

期望生成：

```text
initial_display
upload_select
download_template
close_window
```

若没有 submit，不应生成：

```text
upload_submit
upload_without_file
```

### 页面 C：search form + result table

期望生成：

```text
initial_display
search_normal
result_table_verify
```

不应生成：

```text
upload_select
upload_submit
download_template
```

### 页面 D：普通显示页

期望至少生成：

```text
initial_display
```

其余模板应进入 SkippedTemplates。

---

## 质量要求

请注意：

1. 不要把所有模板都默认生成为 auto
2. 不要只根据 page_id 猜 case
3. 优先根据页面实际元素、onclick、form action、label_key、raw 文本判断
4. 所有判断都要防御式编程，字段不存在时不能报错
5. 输出 skipped reason，便于后续调模板
6. 不要删除现有功能
7. 代码尽量小步修改，避免一次性重构整个项目
8. 生成的 case 要“少而准”，不是“多而泛”

---

## 完成标准

完成后应满足：

1. 新增 `src/page_case_planner.py`
2. `checklist_generator.py` 使用 `PageCasePlanner`
3. Checklist sheet 里的 auto case 更贴合每个页面能力
4. 新增 `PageProfile` sheet
5. 新增 `SkippedTemplates` sheet
6. 页面没有对应能力时，不再生成无法执行的 auto case
7. 现有回归执行入口不被破坏
8. 代码能通过基本 pytest 或至少无语法错误

---

## 建议提交信息

```text
feat: generate page-specific checklist cases from fuzzy templates
```
