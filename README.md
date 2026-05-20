# Patlics UI 自动化测试工具箱 (Patlics Auto Test)

## 🚀 项目概述 (Project Overview)
本项目是专为 **PatentSQUARE 在线应用** 从 Struts 架构向 Spring 架构迁移而设计的 UI 自动化回归测试框架。通过解析 Excel 格式的迁移测试检查单，利用 Python + Playwright 执行数据驱动测试 (DDT)，旨在自动检测 UI 布局及核心功能的退化 (Regression)。

## 🛠️ 技术栈 (Tech Stack)
- **语言**: Python 3.9+
- **UI 自动化**: [Playwright](https://playwright.dev/python/) (Sync API)
- **测试框架**: [pytest](https://docs.pytest.org/)
- **报告生成**: [pytest-html](https://pytest-html.readthedocs.io/)
- **数据处理**: pandas, openpyxl
- **环境管理**: python-dotenv

## 📡 核心特性 (Key Features)
- **多端联测适配**: 原生支持 Edge、Firefox 及 Chrome Portable (便携版) 的三端适配。
- **环境高度解耦**: 所有的 URL、路径及凭证均通过 `.env` 集中管理。
- **三端运行隔离**: 截图、下载文件及用户数据目录均带浏览器标识，防止数据跨维度覆盖。
- **健壮容错机制**: 针对不同浏览器渲染延迟 (特别是 Firefox) 及 Edge 下载拦截植入了自适应等待与补丁。
- **自动错误取证**: 监控 500/504 等服务器异常，并在检测到故障时自动执行全屏截图。

## 📂 目录结构 (Project Structure)
```text
patlics-auto-test/
├── data/               # 存放 checklist.xlsx 检查单
├── output/             # 存放报告、截图及下载文件
├── src/
│   ├── config_parser.py # 全局配置解析层
│   ├── data_loader.py   # Excel 数据驱动核心
│   ├── action_executor.py # UI 动作执行引擎 (含浏览器适配)
│   ├── assert_engine.py # 断言与一致性比对
│   └── error_handler.py # 服务器异常监控
├── tests/
│   ├── conftest.py      # Fixture 与浏览器调度核心
│   └── test_migration.py # 核心测试逻辑
├── requirements.txt
└── .env                # 环境配置文件
```

## ⚙️ 快速开始 (Quick Start)

### 1. 环境初始化
```bash
# 克隆仓库后创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: .\venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 安装浏览器驱动
playwright install --with-deps
```

### 2. 配置环境协议
在根目录下配置 `.env` 文件：
```ini
LEGACY_URL=https://...
NEW_URL=https://...
TEST_USERNAME=your_user
TEST_PASSWORD=your_pass
CHROME_PORTABLE_PATH=C:/path/to/chrome.exe
```

### 3. 执行测试轨道
```bash
# 指定浏览器运行 (默认 chrome_port)
pytest --test-browser=firefox --html=output/report.html
```

---
*Developed with Luna's Grace | Patlics Migration Project 2026*
