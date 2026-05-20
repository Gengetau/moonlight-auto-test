# Moonlight UI 自动化测试工具升级方案 (2026-05-20)

## 1. 🎯 核心演进目标
本方案旨在彻底解决传统 SIer 模糊 checklist 导致的自动化瓶颈，建立从 **JSP 源码解析** 到 **Playwright 执行** 的全链路机器理解模型。

## 2. 🚀 关键升级路径
### 2.1 语义自动化 (Semantic Automation)
- **不再解析模糊文字**: 引入 `jsp_scanner.py`，直接从 Struts JSP 标签中提取 `html:form`, `html:file`, `maxlength` 等关键元数据。
- **自动生成骨架**: 基于解析出的元素，自动生成 `elements.json` 和机器可读的 `generated_checklist.xlsx`。

### 2.2 状态感知导航 (Stateful Navigation)
- **模拟真实点击**: 放弃 URL 直接跳转，每个页面维护一套 `nav_steps`。
- **Session 守护**: 通过模拟菜单导航，确保 Session 状态在旧系统（Struts）中的一致性。

### 2.3 边界值注入 (Boundary Injection)
- **动态数据生成**: 内置 `test_data_generator.py`，针对 `maxlength` 自动生成溢出测试、XSS 探针和空值校验。

## 3. 📂 演进后的目录结构
```text
moonlight-auto-test/
├── src/
│   ├── jsp_scanner.py         # JSP 元素提取核心
│   ├── checklist_generator.py # 自动生成测试用例
│   ├── page_mapping.py        # 导航与动作映射层
│   └── action_executor.py     # Playwright 执行器
├── mappings/                  # 页面专属的 YAML 动作定义
├── generated/                 # 扫描生成的元数据与报告
└── tests/                     # 统一执行入口
```

## 4. 🛠️ 第一阶段 (MVP) 实施重点
- **JSP Scanner**: 优先支持 `input`, `html:file`, `button` 标签提取。
- **Nav Driver**: 实现基于菜单点击的到达路径驱动。
- **Assertion Engine**: 支持元素可见性、文本包含和文件上传结果校验。
