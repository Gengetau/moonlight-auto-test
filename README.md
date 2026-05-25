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
- **页面专属 Case**: 当输入为 `page_mapping.json` 时，先由 `page_case_planner.py` 生成 PageProfile，再按页面实际能力生成少而准的自动化 case；不适用的模板会写入 `SkippedTemplates` sheet。
- **Runtime PageProfile**: 路径验证成功后会抓取浏览器真实渲染 DOM，输出到 `generated/valid/runtime_profile/*.json`；后续生成 checklist 时会优先使用同名目标页的 runtime profile，再回退到静态 JSP 扫描结果。
- **观点扩展 Case**: PageProfile 先生成可执行 seed case，再由 `CaseExpansionRules` 扩展异常系、边界系、复帰系观点；Excel 中通过 `parent_case_id` / `viewpoint_id` 追踪来源。

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

Windows PowerShell:
```powershell
py -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m playwright install
```

### 2. 注入时空坐标 (`.env`)
在根目录创建 `.env` 文件，这是连接两世界的唯一凭证：
```ini
# 时空终点 URL
LEGACY_URL=https://old-world.example.com
NEW_URL=https://new-world.example.com

# 可选：配置多个登录入口，启动时人工选择
# 序号一一对应：第 1 个 Legacy URL 对第 1 个 New URL。
LOGIN_ENTRY_NAMES=dev-a,dev-b
LEGACY_URLS=https://old-a.example.com,https://old-b.example.com
NEW_URLS=https://new-a.example.com,https://new-b.example.com
# 可选：无人值守时预选入口，支持名称或序号
# LOGIN_ENTRY=dev-a

# 侦察兵凭证
TEST_USERNAME=your_username
TEST_PASSWORD=your_password

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
如果输入包含 `page_mappings`，Excel 会额外生成：
- `PageProfile`: 每个页面的结构画像与 capabilities。
- 如果存在 `generated/valid/runtime_profile/<side>_<Target>_<route>.json`，`checklist_generator.py` 会优先用浏览器运行时 DOM 生成该目标页的 PageProfile；`PageProfile` sheet 中的 `profile_source` / `runtime_profile_path` 可确认来源。
- `SkippedTemplates`: 没有生成的 fuzzy template 及跳过原因。

这可以避免把搜索、结果表、下载、关闭窗口、上传 submit 等模板无差别套到所有页面上。

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
  --login-entry=dev-a \
  --checklist-path=generated/valid/migration_checklist.xlsx \
  --upload-file=test_data/upload/プロジェクトリストアップロード.tsv \
  --force-route-map \
  --route-map-path=generated/valid/route \
  --html=output/AbstListEdit_report.html
```
`--force-route-map` 会在存在可用路径图时优先按 `usable_route_map*.json` 导航，而不是先尝试 URL 直达。
`--upload-file` 可选；指定后，所有自动化上传动作会优先使用该真实本地文件，覆盖 checklist 或录制路径中的上传样本。未指定时仍使用 checklist 的 `test_data`，如果只有浏览器安全占位路径 `C:\fakepath\...`，工具会按文件名在 `test_data/upload` 下查找。

半自动接管模式 (Takeover Mode)：当旧系统由于 Frame 嵌套或 Session 状态复杂导致直接 `page.goto` 白屏时，由主祭人工操作浏览器至目标页面，自动化脚本负责后续接管。
```bash
pytest tests/test_migration.py \
  --test-browser=chrome_port \
  --target-page=ProjectMemberUploadDisp.jsp \
  --manual \
  --html=output/manual_report.html
```
*注：此模式下，引擎会暂停执行并等待你在终端按下回车，请在接管前确保浏览器已停留在正确的 JSP 画面。*

### 5. 单页面路径建图
全量路径建图会枚举大量静态候选路径，实际排查时建议按目标 JSP 单独生成、单独验证。

先只为一个页面生成候选路径：
```powershell
.\venv\Scripts\python.exe -m src.route_catalog `
  --target AbstListEdit.jsp `
  --output generated\valid\route\route_candidates_AbstListEdit.json `
  --limit-per-target 5 `
  --max-sources 8 `
  --target-timeout-seconds 5
```

如果你已经知道路径应从菜单页开始，可以显式指定入口 JSP，避免默认源点集合漏掉中间入口：
```powershell
.\venv\Scripts\python.exe -m src.route_catalog `
  --target ProjectMemberUploadDisp.jsp `
  --entry PatlicsMenu.jsp `
  --output generated\valid\route\route_candidates_ProjectMemberUploadDisp.json `
  --limit-per-target 5
```

再只验证这个页面的候选路径。默认关闭自动登录；如入口页出现登录表单，工具会暂停等待人工登录或准备入口。
```powershell
.\venv\Scripts\python.exe -m src.route_map_runner `
  --candidates generated\valid\route\route_candidates_AbstListEdit.json `
  --target AbstListEdit.jsp `
  --output generated\valid\route\usable_route_map_legacy_AbstListEdit.json `
  --capture-dir output\route_map\AbstListEdit_legacy `
  --side legacy `
  --upload-file test_data\upload\プロジェクトリストアップロード.tsv `
  --manual-data
```

常用缩小范围参数：
- `--limit 1`: 只验证第 1 条候选路径。
- `--start-index 3`: 从第 3 条候选路径开始验证。
- `--login-entry dev-a`: 多入口环境下直接指定入口，避免启动时交互选择。
- `--side new`: 只验证新系统路径；一次只打开一个环境。
- `--auto-login`: 启用自动填账号密码并点击登录；默认不启用。
- `--upload-file <path>`: 路径验证或人工录制中遇到上传控件时，回放阶段使用这个真实本地文件；用于避免浏览器只暴露 `C:\fakepath\...`。
- `--runtime-profile-dir <path>`: 路径验证到达目标页后保存浏览器真实 DOM 画像；默认 `generated\valid\runtime_profile`，生成 checklist 时会自动优先读取。

人工介入时，工具会在页面中录制 `click`、`input/change`、`submit` 等事件，并写入 `usable_route_map*.json` 的 `manual_replay`。后续回归使用 route map 时会优先回放这些人工操作。旧的 route map 没有录制数据，如需启用该能力，需要重新验证对应路径。

如果已经有全量 `generated\valid\route\route_candidates.json`，也可以不重新生成候选，直接按目标页面过滤验证：
```powershell
.\venv\Scripts\python.exe -m src.route_map_runner `
  --candidates generated\valid\route\route_candidates.json `
  --target AbstListEdit.jsp `
  --output generated\valid\route\usable_route_map_legacy_AbstListEdit.json `
  --side legacy `
  --limit 3 `
  --manual-data
```

### 6. 🌕 Moonlight Control Center (GUI)

本项目提供两种图形化交互方式：

#### A. 桌面原生 GUI (推荐用于公司本地电脑)
如果您在公司本地环境运行，可以使用基于 `tkinter` 的原生窗口，无需启动浏览器服务：
```bash
python src/app_gui.py
```

Windows PowerShell:
```powershell
.\venv\Scripts\python.exe src\app_gui.py
```

启动后窗口分为三个主要区域：

1. `Scanner & Mapper`
   - `Legacy JSP Path` / `New JSP Path`: 分别选择旧系统和新系统的 JSP 根目录。
   - `Start Full Scan`: 扫描 JSP 元素，输出 `mappings\legacy_elements.json` 和 `mappings\new_elements.json`。
   - `Generate Mapping & Summary`: 生成页面/元素映射，输出 `generated\valid\page_mapping.json` 和 `generated\valid\comparison_summary.md`。
   - `Export Excel Checklist`: 根据映射结果导出自动化测试清单，输出 `generated\valid\migration_checklist.xlsx`。

2. `Regression`
   - `Target Page`: 可选，填写单个 JSP 文件名时只跑该页面，例如 `ProjectMemberUploadDisp.jsp`。
   - `Login Entry`: 从 `.env` 中配置的入口名生成下拉框。
   - `Browser`: 选择测试浏览器，支持 `Chrome portable`、`Microsoft Edge`、`Firefox`。
   - `Checklist Path`: 自动化测试清单，默认 `generated\valid\migration_checklist.xlsx`。文件存在时会传给 pytest，优先执行 Excel 中的 `automation_mode=auto` 用例。
   - `Use Upload File` / `Upload File`: 勾选后选择真实本地文件，GUI 会把它传给 `--upload-file`，后续上传动作统一读取该文件。
   - `Risk-Only Mode`: 只执行高/中风险差异页面。
   - `Takeover Mode`: 启用人工接管模式，用于需要手工登录、手工导航或准备数据的场景。
   - `Use Route Map`: 勾选后优先使用 `generated\valid\route\usable_route_map*.json` 到达目标页面；适用于不能通过 URL 直达页面的系统，默认开启。
   - `Launch Regression Engine`: 执行回归，报告输出到 `output\gui_regression_report.html`。

3. `Route Intelligence`
   - `Target JSP`: 需要建图的目标页面，默认留空，使用时手工填写，例如 `ProjectMemberUploadDisp.jsp`。
   - `Entry JSP`: 候选路径入口页面，例如 `PatlicsMenu.jsp`。
   - `Scout Paths`: 调用 struts-tracer 缓存生成候选路径，输出 `generated\valid\route\route_candidates_<Target>.json`。
     如果 `Target JSP` 填写 `/docroot/adminTool/ProjectListUploadErr.jsp` 这样的带目录路径，文件名中的目录分隔符会自动转换为 `_`。
   - `Side`: 选择验证旧系统 `legacy` 或新系统 `new`，一次只打开一个系统。
   - `Login Entry`: 从 `.env` 中配置的入口名生成下拉框。验证路径时会传给 `route_map_runner`，避免命令在浏览器启动前停在入口选择。
   - `Browser`: 选择路径验证浏览器，支持 `Chrome portable`、`Microsoft Edge`、`Firefox`。
   - `Auto Login`: 勾选后打开入口页时自动填写 `.env` 中的测试账号密码；不勾选时，遇到登录页会等待人工登录。
   - `Use Upload File` / `Upload File`: 路径验证需要上传数据时，勾选并选择真实本地文件，录制出的 `manual_replay` 会使用该文件路径。
   - `Verify Selected Route`: 验证候选路径是否可实际到达，输出 `generated\valid\route\usable_route_map_<side>_<Target>.json`。

窗口底部的 `Console Output` 会实时显示实际执行的命令和日志。如果按钮执行失败，先看这里的最后几行错误。

注意：原生 GUI 的日志区不能输入内容；如果路径验证进入 `Enter/r/m/s/q` 等人工确认，请在启动 GUI 的 PowerShell 窗口中输入。

#### B. Web 指挥台 (Streamlit)
如果您需要更丰富的图表分析或远程协作，可以启动 Web 服务：
```bash
streamlit run src/gui.py
```

Windows PowerShell:
```powershell
.\venv\Scripts\streamlit.exe run src\gui.py
```

首次使用前请确认已安装依赖：
```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

Web 指挥台包含四个页签：

- `Regression`: 与原生 GUI 的回归页一致，勾选 `Use Upload File` 后可上传一个文件作为本次自动化的上传数据，输出 `output\gui_report.html`。
- 单页面回归的详细报告会写入该页面截图目录，例如 `output\regression\0001_ProjectListUploadDisp.jsp\regression_report.html`，方便和截图一起查看。
- `Route Mapping`: 先生成候选路径，再验证 `legacy` 或 `new` 的可用路径；`Target JSP` 默认留空，`Login Entry` 从 `.env` 入口列表中选择；验证页也支持 `Use Upload File`。
- Streamlit 版路径验证成功后会保存 runtime profile，并在存在 `generated\valid\page_mapping.json` 时自动重新生成 `generated\valid\migration_checklist.xlsx`。
- `Scanner & Mapper`: 执行 JSP 扫描、映射生成与 Excel checklist 导出。Streamlit 版在 `Generate Mapping` 后默认自动生成 `generated\valid\migration_checklist.xlsx`，也可以单独点击 `Export Excel Checklist`。
- `Analysis`: 读取 `generated\valid\page_mapping.json`，展示风险分布和页面列表。

日常本地排查建议优先使用原生 GUI；需要图表概览或远程共享时再使用 Streamlit。

关于 Edge IE 模式：Playwright 无法像普通浏览器参数一样可靠地开启 IE mode。当前 GUI 不单独提供 `IE mode` 选项；如果公司电脑已经通过 Edge 企业策略配置了 IE mode site list，可以选择 `Microsoft Edge`，实际是否进入 IE mode 由 Edge 策略决定。

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
