🚀 Patlics UI 自动化测试工具箱 - 开发者规范 (Developer Specification)
1. 🎯 项目概述 (Project Overview)
本项目旨在为“PatentSQUARE 在线应用”从 Struts+JSP 向 Spring+JSP 的系统迁移提供支持，开发一款用于自动检测 UI 及功能退化（Regression）的测试工具。
工具将以 Excel/CSV 格式的“移行测试检查单 (移行テストチェックリスト)”作为数据源，利用 Python + Playwright 执行数据驱动测试 (DDT)。
2. 🛠️ 技术栈 (Tech Stack)
语言: Python 3.9+
核心框架: pytest (测试用例管理), pytest-html (测试报告生成)
UI 自动化: playwright (推荐使用同步 API sync_api)
数据处理: openpyxl (Excel 读写), pandas (数据清洗与格式化)
3. 📂 目录结构 (Directory Structure)
请 Agent 按照以下结构生成并配置代码文件：
patlics-auto-test/
├── data/
│ ├── checklist.xlsx # 迁移测试检查单 (数据输入源)
│ └── test_data/ # 用于上传测试的文件集合
├── output/
│ ├── downloads/ # 下载测试的文件保存路径
│ ├── screenshots/ # 错误页面(504/500等)及UI比对截图保存路径
│ └── report.html # Pytest 执行结果可视化报告
├── src/
│ ├── __init__.py
│ ├── data_loader.py # 负责从 Excel 解析读取测试用例的逻辑
│ ├── action_executor.py # 封装 Playwright 的 UI 操作 (点击、输入、上传等)
│ ├── assert_engine.py # 封装与预期值的比对断言逻辑 (文本、URL、下载文件)
│ └── error_handler.py # 监控并处理 504/500 等服务器异常与截图抓取
├── tests/
│ ├── conftest.py # Pytest Fixture 配置 (浏览器初始化、登录态保持)
│ └── test_migration.py # Pytest 核心执行脚本
├── requirements.txt # 项目依赖列表
└── cloud.md # 本设计说明文档
4. 📊 数据模型与输入规范 (Data Model)
工具需读取提供的“移行测试检查单”模板，并将其解析为 Python 字典 (Dictionary) 列表。
【给开发人员/Agent 的指令】: 在 data_loader.py 中解析 Excel 后，必须将具有以下结构的 JSON/Dict 对象传递给 pytest.mark.parametrize 进行参数化执行：
{
 "test_id": "No.1", // 对应 [No.] 列
 "test_category": "初期表示", // 对应 [テスト内容・観点] 列
 "action_target": "#btn-submit", // 【扩展】操作目标的 CSS 定位器 (从备注或隐藏列提取)
 "action_type": "click", // 【扩展】操作类型 (click, fill, upload, download, check)
 "input_value": "边界值数据", // 对应从 [確認事項] 列提取的输入值
 "expected_text": "正常に登録", // 对应从 [期待値] 列提取的断言文本
 "expected_url": "/sdi/success", // 对应从 [期待値] 列提取的预期跳转 URL
 "target_env": "new" // 标记执行环境 (legacy 旧系统 或 new 新系统)
}
5. ⚙️ 核心模块设计要求 (Core Module Specs)
5.1 action_executor.py (动作执行模块)
接收 Playwright 的 Page 对象，并根据 action_type 执行相应的分支逻辑：
* fill: 执行 page.fill(selector, value)。（主要用于边界值测试的输入）
* click: 执行 page.click(selector)。
* upload: 执行 page.set_input_files(selector, filepath)。
* download: 必须使用以下逻辑实现文件下载拦截：
with page.expect_download(timeout=30000) as download_info:
 page.click(selector)
download = download_info.value
download.save_as(f"./output/downloads/{download.suggested_filename}")
5.2 error_handler.py (服务器异常处理模块)
考虑到测试环境可能存在不稳定因素（如 504 Gateway Time-out, 500 Error, Mixed Content）。
【给开发人员/Agent 的指令】: 在所有涉及页面加载的操作（如 page.goto、page.click 后的 wait_for_load_state）中，必须嵌入以下健壮性逻辑：
​使用 try-except 捕获 TimeoutError。
​当捕获到异常时，检查页面 DOM (page.content()) 中是否包含 504 或 500 等错误标识字符串。
​确认为服务器异常后，立刻执行全屏截图：page.screenshot(path=f"./output/screenshots/error_{test_id}.png")，并将该测试用例标记为失败 (Fail) 且记录中断原因，以便作为 QA 凭证提交给运维。
5.3 assert_engine.py (断言与预期比对模块)
负责校验“迁移前 (Struts)”与“迁移后 (Spring)”的画面与逻辑一致性：
​文本校验: 提取 page.locator(selector).inner_text() 并与 expected_text 对比。※请特别注意全角/半角字符差异，以及日文网页特有的编码（UTF-8/Shift-JIS）引起的乱码问题。
​URL 校验: 检查 page.url 是否包含 expected_url（需考虑到 Spring 迁移后，可能去除了 Struts 时代的 .do 后缀，建议使用部分匹配或正则匹配）。
6. 🔄 执行流程 (Execution Flow)
在 tests/test_migration.py 中的标准执行顺序如下：
​触发 conftest.py，启动 Playwright 浏览器实例，自动完成登录并保留 Cookie/Session 状态。
​data_loader.py 解析 Excel 文件，生成测试用例数据集。
​pytest 开始循环执行参数化的测试用例。
​脚本导航至目标页面 -> 调用 action_executor 执行用户交互 -> 调用 assert_engine 进行预期值断言。
​记录执行状态（Pass/Fail），测试结束后自动输出可视化 report.html。
7. ⚠️ 开发注意事项 (Important Notes)
等待策略 (Wait Strategy): 迁移后的 Spring 架构可能大量使用了异步请求 (Ajax)。严禁在代码中硬编码 time.sleep()。必须使用 Playwright 原生的 page.wait_for_selector() 或 page.wait_for_load_state("networkidle") 来保证页面渲染完毕。
​定位器抽象化 (Locator Abstraction): 切勿让代码强依赖于模板中感性的日文描述（如“点击确认按钮”）。应设计映射逻辑，将这些文本转化为稳定的 CSS 选择器或 XPath（例如：text="コーポレート登録区分"）。
