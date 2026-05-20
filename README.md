# Moonlight UI 自动化测试工具箱

Moonlight 是为 **TargetApp 从 Struts 迁移到 Spring** 准备的 UI 回归与 JSP 扫描工具箱。

它做两件事：

- 从 JSP 中扫描表单、上传控件、按钮和链接，生成页面元素映射。
- 根据映射自动生成迁移测试建议报告，帮助远程协作者快速发现高风险页面。

## 远程协作：分布式扫描与报告流程

适合多人各自扫描一部分 JSP，再把报告带回主仓库或主祭汇总。

1. 拉取最新代码。

   ```bash
   git clone https://github.com/Gengetau/moonlight-auto-test.git
   cd moonlight-auto-test
   git pull origin main
   ```

2. 准备运行环境。

   ```bash
   python -m venv venv
   source venv/bin/activate  # Windows: .\venv\Scripts\activate
   pip install -r requirements.txt
   ```

   如果你的机器只有 `python3`，下面所有 `python` 命令都可以替换成 `python3`。

3. 执行 `jsp_scanner`，把自己负责的 JSP 目录扫成 JSON。

   示例：扫描本机检出的 Struts 页面目录，并把映射放到 `mappings/valid/`。

   ```bash
   python src/jsp_scanner.py /path/to/targetapp/src/main/webapp/WEB-INF/jsp \
     -o mappings/valid/elements.json
   ```

   也可以只扫描单个页面：

   ```bash
   python src/jsp_scanner.py /path/to/targetapp/src/main/webapp/WEB-INF/jsp/order/detail.jsp \
     -o mappings/valid/order_detail.elements.json
   ```

4. 执行 `checklist_generator`，把 JSON 转成可读报告。

   生成 Markdown：

   ```bash
   python src/checklist_generator.py mappings/valid/elements.json \
     -o generated/valid/checklist.md
   ```

   生成 Excel：

   ```bash
   python src/checklist_generator.py mappings/valid/elements.json \
     -o generated/valid/checklist.xlsx
   ```

5. 检查报告内容。

   打开 `generated/valid/checklist.md` 或 `generated/valid/checklist.xlsx`，确认页面数量、元素统计和高优先级清单是否符合自己负责的范围。

6. 带回报告。

   如果只是交付扫描成果，把以下文件发回给主祭或汇总人：

   - `mappings/valid/elements.json`
   - `generated/valid/checklist.md` 或 `generated/valid/checklist.xlsx`

   如果需要通过 GitHub 回传：

   ```bash
   git add mappings/valid/elements.json generated/valid/checklist.md
   git commit -m "Add JSP scan report for valid pages"
   git push origin main
   ```

## 快速开始：UI 回归测试

如果你要跑 Playwright UI 自动化，而不是只生成 JSP 报告，继续按下面配置。

1. 安装浏览器驱动。

   ```bash
   playwright install --with-deps
   ```

2. 在根目录创建 `.env`。

   ```ini
   LEGACY_URL=https://...
   NEW_URL=https://...
   TEST_USERNAME=your_user
   TEST_PASSWORD=your_pass
   CHROME_PORTABLE_PATH=C:/path/to/chrome.exe
   ```

3. 执行测试。

   ```bash
   pytest --test-browser=firefox --html=output/report.html
   ```

## 目录结构

```text
moonlight-auto-test/
├── data/                 # 手工维护或外部导入的检查单数据
├── mappings/             # jsp_scanner 输出的元素映射 JSON；用于留存扫描证据
│   ├── valid/
│   ├── invalid/
│   └── boundary/
├── generated/            # checklist_generator 输出的 Markdown/Excel 报告
│   ├── valid/
│   ├── invalid/
│   └── boundary/
├── output/               # Playwright/pytest 运行时报告、截图和下载文件
├── src/
│   ├── jsp_scanner.py        # 扫描 JSP，提取 form/file/button/link
│   ├── checklist_generator.py # 根据扫描 JSON 生成测试建议报告
│   ├── config_parser.py      # 全局配置解析
│   ├── data_loader.py        # Excel 数据驱动加载
│   ├── action_executor.py    # UI 动作执行引擎
│   ├── assert_engine.py      # 断言与一致性比对
│   └── error_handler.py      # 服务器异常监控
├── tests/
│   ├── conftest.py           # pytest fixture 与浏览器调度
│   └── test_migration.py     # 迁移回归测试入口
├── requirements.txt
└── README.md
```

## 工具分工

- `mappings/` 是证据层：保存扫描到的页面元素、定位器、行号和原始 JSP 线索。
- `generated/` 是交付层：保存给人阅读和汇总的 Markdown 或 Excel 报告。
- `output/` 是自动化测试运行层：保存 pytest-html 报告、失败截图和浏览器下载物。

## 技术栈

- Python 3.9+
- BeautifulSoup：JSP/HTML 标签解析
- openpyxl：Excel 报告生成
- Playwright + pytest：UI 自动化回归
- pytest-html：测试报告
- pandas / python-dotenv：数据与环境配置

## Moonlight 原则

先让每个协作者在自己的页面范围内跑出清晰报告，再把映射和报告汇入同一条月光轨道。

扫描结果要可追溯，报告要可执行，回归测试要可重复。

---

*Developed with Luna's Grace | Moonlight Migration Project 2026*
