# Route Map / Runtime Capture 升级计划清单

## 目标

将当前“人工路径建图成功后，仅扫描目标页面并关闭”的流程，升级为 **Route + Runtime Page Verification**。

新的路径建图不只证明“能到达目标页面”，还要证明：

- 目标页面是否真正渲染完成；
- 页面上有哪些可操作元素；
- 哪些元素初期可见、哪些元素隐藏、哪些元素需要前置操作才出现；
- 哪些元素可以安全验证；
- 哪些元素需要生成 `pre_steps` / `required_state`；
- 哪些元素必须降级为 `semi-auto` 或 `manual/assist`。

最终让 runtime profile 直接服务于 checklist 生成和自动化执行。

---

## 当前问题

当前路径建图流程大致是：

```text
人工 / 自动 route replay
  ↓
到达目标 JSP / .do 页面
  ↓
扫描一次 rendered DOM
  ↓
保存 runtime profile
  ↓
关闭页面
```

这个流程存在几个限制：

1. 只能证明路径可达，不能证明页面元素可操作。
2. 对隐藏菜单、弹出区域、搜索后出现的按钮等条件元素没有建模。
3. checklist_generator 只能基于初始 DOM 生成 case，容易漏掉后续状态中的元素。
4. 对 `td#download` 这类 hidden DOM 元素，会生成直接点击 case，执行时容易超时。
5. 对需要前置操作的元素，缺少 `pre_steps` / `interaction_state` 信息。
6. runtime profile 的可信度不足，无法区分“扫描到”与“验证过”。

---

## 升级后的整体流程

```text
Route replay / Manual route capture
  ↓
到达目标页面
  ↓
Runtime DOM Capture
  ↓
Element Inventory 生成
  ↓
Safe Element Verification
  ↓
Reveal / Interaction State Discovery
  ↓
Optional Action Verification
  ↓
Runtime Page Profile 输出
  ↓
Checklist Generator 使用 profile 生成 state-aware cases
```

---

## 元素分类模型

### 1. Static Elements

页面初期渲染完成后即可见、可操作的元素。

示例：

- 普通 text input
- 初期可见 checkbox
- 初期可见 select
- 初期可见 search button
- 初期可见 link

处理策略：

```text
直接生成 auto case
```

---

### 2. Hidden DOM Elements

DOM 中存在，但初期不可见，例如 `display:none` / hidden / invisible。

示例：

```html
<td id="download" style="display:none">ダウンロード</td>
```

处理策略：

```text
不直接生成普通 click case
必须先发现 trigger step
生成带 pre_steps / required_state 的 case
```

---

### 3. Conditional Elements

初期 DOM 中不存在，必须通过前置操作后才渲染出来。

示例：

- 搜索后出现的结果表按钮
- 点击 tab 后出现的字段
- popup 内的按钮
- 选择某个 radio 后出现的追加输入区
- hover / click 菜单后出现的菜单项

处理策略：

```text
通过 runtime capture 比较 before/after DOM
建立 interaction_state
再生成 state-aware case
```

---

## interaction_state 设计

用于描述“执行某些前置操作后，页面进入某个状态，并出现一批新元素”。

示例：

```json
{
  "state_id": "bulk_action_menu_open",
  "description": "一括操作メニュー表示状態",
  "trigger_steps": [
    {
      "action_type": "hover",
      "locator": "text=一括操作"
    },
    {
      "action_type": "click",
      "locator": "text=一括操作"
    }
  ],
  "revealed_elements": [
    {
      "locator": "td#download",
      "label": "ダウンロード",
      "element_kind": "menu_item"
    }
  ]
}
```

对应 checklist case：

```json
{
  "case_id": "xxx-download-menu-001",
  "case_type": "hidden_menu_action",
  "action_type": "click",
  "locator": "td#download",
  "required_state": "bulk_action_menu_open"
}
```

执行器看到 `required_state` 后：

```text
执行 trigger_steps
  ↓
等待 revealed element visible / attached
  ↓
执行目标 locator 动作
```

---

## 验证层级设计

### Level 1: Safe Verification 默认开启

目标：验证元素是否存在、可见、可用，但不触发危险动作。

适用元素：

- input
- checkbox
- radio
- select
- button
- link

验证内容：

```text
attached
visible
enabled
editable
focusable
hoverable
option count
text / label / value
```

注意：

- 不默认点击 submit / delete / download / close_window。
- 对 input 可以尝试 fill + clear。
- 对 checkbox 可以在安全模式下仅检查 checked 状态，不一定改变。
- 对 select 可以读取 options，不一定改变选项。

输出：

```json
{
  "locator": "input[name='keyword']",
  "verified": true,
  "verification_level": "safe",
  "visible": true,
  "enabled": true,
  "editable": true
}
```

---

### Level 2: Reveal Verification 默认建议开启

目标：发现隐藏元素和条件元素。

典型 trigger：

- hover
- click menu trigger
- click tab
- expand/collapse
- open popup
- search smoke

流程：

```text
capture visible elements before
  ↓
execute safe trigger
  ↓
capture visible elements after
  ↓
newly_visible = after - before
  ↓
create interaction_state
```

输出：

```json
{
  "state_id": "menu_download_visible",
  "trigger_steps": [...],
  "newly_visible_count": 3,
  "revealed_elements": [...]
}
```

---

### Level 3: Action Verification 需要显式开启

目标：实际执行可能改变页面状态的动作。

需要参数控制：

```text
--verify-actions
--include-download
--include-destructive
--include-negative
```

动作类型：

- search submit
- download
- popup open
- navigation link
- close_window
- back
- delete
- update
- upload_submit
- negative_http_500
- negative_network_abort

要求：

- 必须有 recovery policy。
- 每个 action 后要记录当前页面状态。
- 如果页面关闭或跳转，后续 case 前必须重新进入目标页面。
- destructive action 默认不执行。

---

## Runtime Page Profile 输出结构

建议 runtime profile 扩展为：

```json
{
  "page_id": "Example.jsp",
  "route_verified": true,
  "ready_selector_found": true,
  "capture_url": "http://example/patlics/Example.do",
  "confidence": "high",
  "elements": [],
  "verified_elements": [],
  "hidden_elements": [],
  "conditional_elements": [],
  "interaction_states": [],
  "unsafe_actions": [],
  "verification_summary": {
    "total_elements": 120,
    "static_visible": 80,
    "hidden_dom": 20,
    "conditional_found": 10,
    "verified_safe": 75,
    "reveal_states": 5,
    "unsafe_skipped": 12
  }
}
```

---

## Checklist 生成策略

### static visible element

生成普通 auto case。

```text
input_normal
checkbox_toggle
select_change
button_click_smoke
link_navigation_smoke
```

---

### hidden but revealable element

生成带 `required_state` 的 auto case。

```json
{
  "case_type": "hidden_menu_action",
  "locator": "td#download",
  "required_state": "bulk_action_menu_open",
  "automation_mode": "auto"
}
```

---

### conditional element

如果 runtime capture 找到了触发状态，则生成 state-aware case。

如果未找到触发状态，则降级：

```text
semi-auto 或 manual/assist
excluded_reason = requires unknown precondition
```

---

### unsafe / destructive action

默认不自动执行。

```text
automation_mode = auto-db / manual/assist
excluded_reason = destructive=true; requires --include-destructive
```

---

## Web UI 改进计划

### Route Mapping / Verify Routes 页面

增加验证模式选项：

```text
[ ] Safe Element Verification
[ ] Reveal Hidden Elements
[ ] Verify Actions
[ ] Include Download Actions
[ ] Include Destructive Actions
[ ] Include Negative Cases
```

默认建议：

```text
Safe Element Verification = ON
Reveal Hidden Elements = ON
Verify Actions = OFF
Include Destructive Actions = OFF
```

---

### Runtime Profile Preview

显示：

```text
route verified: yes/no
ready selector found: yes/no
total elements
visible elements
hidden elements
conditional elements
interaction states
unsafe actions skipped
confidence
```

---

### Checklist Generation 页面

新增生成模式：

```text
viewpoint
  按页面能力生成代表性 case

element_grouped
  按可操作元素分组生成 case

element_full
  每个可操作元素生成 case，数量较大

state_aware
  使用 runtime profile + interaction_state 生成带 pre_steps 的 case
```

推荐默认：

```text
viewpoint + state_aware
```

---

## 实装计划清单

### Phase 1: Runtime Profile 扩展

- [ ] 扩展 runtime profile schema。
- [ ] 为元素增加 `visibility_state` 字段。
- [ ] 区分 `static_visible` / `hidden_dom` / `conditional`。
- [ ] 保存元素的 `visible` / `enabled` / `editable` / `attached` 状态。
- [ ] 增加 `verification_summary`。
- [ ] 增加 `confidence` 字段。

---

### Phase 2: Safe Element Verification

- [ ] 在 route verify 到达目标页面后执行 safe verification。
- [ ] input 支持 focus / fill / clear 验证。
- [ ] checkbox / radio 支持状态读取。
- [ ] select 支持 option 读取。
- [ ] button / link 支持 visible / enabled / hover 验证。
- [ ] 不默认点击 destructive / submit / download / close_window。
- [ ] 将验证结果写入 `verified_elements`。

---

### Phase 3: Reveal Verification

- [ ] 识别可能的 trigger 元素。
- [ ] 支持 hover / click tab / click menu trigger。
- [ ] 执行 trigger 前后分别 capture DOM。
- [ ] 计算 newly visible elements。
- [ ] 生成 `interaction_states`。
- [ ] 为 revealed elements 绑定 `required_state`。
- [ ] 对失败 trigger 记录 `reveal_failed_reason`。

---

### Phase 4: Checklist Generator 对接

- [ ] checklist_generator 读取 runtime profile。
- [ ] static element 生成普通 auto case。
- [ ] hidden/revealed element 生成带 `required_state` 的 case。
- [ ] conditional element 生成带 `pre_steps` 的 case。
- [ ] 无法确认前置条件的元素降级为 `semi-auto` / `manual/assist`。
- [ ] 输出 `excluded_reason`。
- [ ] 支持 `case_generation_mode`。

---

### Phase 5: RegressionEngine / ActionExecutor 对接

- [ ] RuntimeCase 支持 `pre_steps`。
- [ ] RuntimeCase 支持 `required_state`。
- [ ] 执行目标 action 前先执行 state trigger steps。
- [ ] 如果 trigger 后目标元素仍不可见，返回 BLOCKED 并记录原因。
- [ ] terminal action 后支持页面恢复。
- [ ] 报告中显示 `pre_steps` 执行结果。

---

### Phase 6: Web UI 对接

- [ ] Route Mapping 页面增加 verification options。
- [ ] Checklist Generation 页面增加 generation mode。
- [ ] Runtime Profile Preview 展示 profile 质量。
- [ ] Checklist Coverage Matrix 展示 state-aware case。
- [ ] 报告中显示元素来源：JSP scanner / runtime capture / interaction_state。

---

### Phase 7: 测试补充

- [ ] 新增 hidden menu fixture。
- [ ] 新增 conditional tab fixture。
- [ ] 新增 search-result dynamic button fixture。
- [ ] 测试 hidden element 不会生成直接 click case。
- [ ] 测试 revealed element 会生成 required_state case。
- [ ] 测试 required_state 执行前会先跑 trigger_steps。
- [ ] 测试 trigger 失败时 case BLOCKED 且有明确原因。
- [ ] 测试 Web UI 参数能正确传给 route verifier。

---

## 推荐优先级

### P0

- [ ] Runtime profile 增加元素状态分类。
- [ ] Safe Element Verification。
- [ ] Reveal Verification。
- [ ] interaction_state 输出。

### P1

- [ ] checklist_generator 支持 state-aware cases。
- [ ] RegressionEngine 支持 `pre_steps` / `required_state`。
- [ ] 报告展示 state-aware 执行结果。

### P2

- [ ] Web UI 增加验证模式和 generation mode。
- [ ] Action Verification。
- [ ] negative / download / destructive 可选验证。

---

## 最终效果

升级完成后，路径建图将不再只是“到达目标页面并扫描一次”，而是变成：

```text
到达页面
  ↓
确认页面可用
  ↓
验证初期可操作元素
  ↓
发现隐藏/条件元素
  ↓
建立前置操作状态关系
  ↓
生成可执行的 state-aware checklist
```

这样复杂 JSP 页面中的隐藏菜单、弹窗、搜索后按钮、tab 内字段等元素，都能被纳入 checklist 覆盖，而不会因为缺少前置操作导致自动化超时。
