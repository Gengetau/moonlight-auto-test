# 🌕 Moonlight UI 自动化测试工具箱 (Moonlight UI Toolkit)

> “既然你向本公主求助，我就绝不允许你的系统里存在这种低级丑陋的错误。” —— 露娜

Moonlight 是为 **TargetApp 从 Struts 迁移到 Spring** 架构而设计的“降维打击”工具箱。它能够穿透混乱的 JSP 源码，精准建立“旧世界（Legacy）”与“新世界（New）”之间的逻辑映射，并自动生成、执行测试方案。

---

## 📡 核心使命：跨越时空的对齐

在架构迁移中，前端 UI 的结构变动（如 ID/Name 变更、Spring 标签引入）往往是自动化失效的罪魁祸首。Moonlight 通过四个阶段将混乱归于秩序：

### Phase 1: [月眸扫描] 结构提取 (`jsp_scanner.py`)
递归扫描 JSP 源码，通过 `BeautifulSoup` 与正则双擎，精准区分：
- **容器 (Container)**: `form`, `form:form`, `html:form`。
- **字段 (Field)**: `form:input`, `html:text`, `form:select` 等（已实现智能分类，字段会自动归属于最近的容器，不再造成报告膨胀）。
- **动作 (Action)**: `input[type=button]`, `html:link`, `html:file`。

### Phase 2: [星轨映射] 差异比对 (`page_mapping.py`)
比对“旧世界”与“新世界”的扫描结果：
- **同名页面对齐**: 自动寻找路径一致的 JSP 页面。
- **定位器追踪**: 识别 ID、Name 或 Property 的变更。
- **风险评估**: 丢失关键字段或定位器大幅变动的页面将被标记为 **High Risk**。

### Phase 3: [恩赐清单] 报告生成 (`checklist_generator.py`)
将结构化的 JSON 映射转化为可读的测试契约：
- **Markdown**: 适合在 Git/Chat 中快速审阅。
- **Excel**: 适合主祭（ミカ）进行大规模资产核对与状态追踪。
- **字段证据**: 所有的表单用例都会附带其内部字段的完整性校验线索。

### Phase 4: [语义执行层] 自动比对 (`action_executor.py`, `regression_engine.py`)
利用 Playwright 驱动，在两个时空下执行相同的业务意图，而不是机械复读录制脚本：
- **Frame 穿透**: 自动扫描嵌套 frame，优先锁定包含业务 URL、`form`、`table` 或控件的目标 frame，并从该 frame 采集截图、文本和 DOM。
- **语义动作**: 将 `form`、`file`、`button`、`link`、`select/input` 等扫描结果推断为 `submit`、`upload`、`click`、`navigate`、`select/fill`。
- **稳定等待**: 组合 `networkidle`、2 秒稳定窗口、`body` 可见性和关键业务元素自适应等待，降低迁移页面的白屏 Diff。
- **诊断闭环**: HTML 报告展示 action type、Legacy/New locator、目标 frame、等待状态和 BLOCKED 原因，便于直接定位失效时空。

---

## 🛠️ 环境构筑 (Setup)

### 1. 准备赛博温床
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps
```

### 2. 注入时空坐标 (`.env`)
在根目录创建 `.env` 文件，这是连接两世界的唯一凭证：
```ini
# 时空终点 URL
LEGACY_URL=https://old-world.example.com
NEW_URL=https://new-world.example.com

# 侦察兵凭证
USERNAME=your_username
PASSWORD=your_password

# 浏览器阵地 (便携版路径，若使用系统浏览器可留空)
CHROME_PORTABLE_PATH=/path/to/chrome_portable/chrome.exe
```

---

## 🚀 谕旨：工具执行指南

### 1. 启动全量扫描
扫描旧/新世界的 JSP 资产：
```bash
# 扫描旧世界
python src/jsp_scanner.py /path/to/legacy/jsp -o mappings/legacy_elements.json
# 扫描新世界
python src/jsp_scanner.py /path/to/new/jsp -o mappings/new_elements.json
```

### 2. 生成跨时空映射
比对并识别风险页面，输出 JSON 映射文件与 Markdown 摘要报告：
```bash
python src/page_mapping.py mappings/legacy_elements.json mappings/new_elements.json \
  -o mappings/page_diff.json \
  --md generated/comparison_summary.md
```

### 3. 获取测试清单
将差异转化为可执行的 Excel 报告：
```bash
python src/checklist_generator.py mappings/page_diff.json -o generated/migration_checklist.xlsx
```

### 4. 运行回归测试
执行自动化比对逻辑：
```bash
pytest --test-browser=chrome_port --html=output/report.html
```

单画面精准打击：主祭指定一个 JSP 文件名，只执行该画面的回归测试；此模式会无视风险等级过滤。
```bash
pytest tests/test_migration.py \
  --test-browser=chrome_port \
  --target-page=AbstListEdit.jsp \
  --html=output/AbstListEdit_report.html
```

---

## 📂 架构图谱

```text
moonlight-auto-test/
├── mappings/             # [证据层] 存储各时空的元素映射与差异 JSON
├── generated/            # [交付层] 存储导出的测试建议报告 (Excel/MD)
├── output/               # [运行层] 运行时生成的截图、HTML 报告与下载物
├── src/                  # [核心中枢]
│   ├── jsp_scanner.py        # 结构扫描引擎
│   ├── page_mapping.py       # 差异比对与风险评估
│   ├── checklist_generator.py # 报告渲染逻辑
│   ├── action_executor.py    # 跨端同步执行器
│   └── ...
├── tests/                # [验证场] 自动化测试用例与比对回归
└── README.md
```

---

## 📜 露娜的契约 (Rules)

1. **绝对对齐**: 任何在 Legacy 存在的 `form` 或 `file` 控件，如果在 New 中丢失或定位失效，必须在报告中置顶。
2. **优雅至上**: 严禁将数万个子字段展开为独立用例。字段必须作为表单的关联证据被审视。
3. **证据闭环**: 扫描、比对、生成、执行，每一步必须有对应的 JSON 或 截图存档。

---

*“月光所照之处，错误无所遁形。”*
*Developed with Luna's Grace | Moonlight Migration Project 2026*
