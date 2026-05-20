# Legacy/New JSP 降维比对摘要

## 总览

- Legacy 页面数：1673
- New 页面数：1685
- 同名页面匹配数：1659
- Legacy 独有页面数：0
- New 独有页面数：11
- 高风险页面数：235
- 中风险页面数：414
- 公共导航路径数：588
- Action 映射数：861

## 一致性比对执行流规划

1. 读取 `page_mapping.json`，按 `risk` 优先级选择页面；先执行 High，再执行 Medium/Low。
2. Legacy 环境打开目标页面，使用 Legacy 定位器执行动作，记录 URL、DOM 快照、网络请求、弹窗、下载文件和截图。
3. New 环境打开同一 `page_id`，通过映射后的定位器复现同一 Action；定位器变化时优先使用 New locator，缺失时标记为阻断。
4. 对比两端结果：截图差异、URL/action、关键文本、表格数据、下载文件名/大小/hash、服务端错误页。
5. 输出每个 Action 的 `PASS / DIFF / BLOCKED`，并把缺失元素和定位器变更回写到风险摘要。

## 公共导航路径 Top 30

- `AbstListViewEntry`
- `AbstSetSearch`
- `AccessLogDownload`
- `AdminDivisionLogin`
- `AdminLogin`
- `AdminTopMain`
- `AiAutoClsAdministrationMain`
- `AiAutoClsClassSelect`
- `AiAutoClsEntry`
- `AiAutoClsEntryDisp`
- `AiAutoClsEntryFile`
- `AiAutoClsEntryFileCheck`
- `AiAutoClsEntryList`
- `AiAutoClsListPredictDisp`
- `AiAutoClsListPredictMain`
- `AiAutoClsLogin`
- `AiAutoClsPredictFile`
- `AiAutoClsPredictFileCheck`
- `AiEvalEntry`
- `AssigneeIdentificationDictionaryDisp`
- `AzureADUserUpAdminUpload`
- `BiblioComplexSetEntry`
- `BiblioListSaveUpdateListSelect`
- `BiblioListViewEntry`
- `BiblioSetSearch`
- `ClsCopy`
- `ClsDeleteFromContext`
- `ClsDtlDisp`
- `ClsFileDownload`
- `ClsFileUploadConf`

## 高风险页面 Top 30

| 页面 | Legacy元素 | New元素 | 缺失 | 定位器变更 | 公共导航 |
|---|---:|---:|---:|---:|---|
| AiAutoClsEntry.jsp | 2 | 23 | 1 | 0 | `AiAutoClsEntry` |
| AiAutoClsEntryList.jsp | 2 | 22 | 1 | 0 | `AiAutoClsEntryList` |
| AiAutoClsEvalRankSelect.jsp | 3 | 4 | 1 | 0 | `AiAutoClsEntryDisp` |
| AiAutoClsSettingClass.jsp | 3 | 3 | 1 | 0 | - |
| AiAutoClsSettingEvalRank.jsp | 3 | 3 | 1 | 0 | - |
| AzureADUserListUpload.jsp | 3 | 2 | 1 | 0 | `AzureADUserUpAdminUpload`, `UserUpUserListCtrl` |
| BiblioListSaveUpdateListSelect.jsp | 3 | 9 | 1 | 0 | `BiblioListSaveUpdateListSelect` |
| BookmarkWord.jsp | 2 | 2 | 1 | 1 | - |
| ClassificationCodeToolTip.jsp | 7 | 7 | 0 | 7 | - |
| ClassificationMatrix.jsp | 12 | 12 | 0 | 12 | - |
| ClassificationMatrixHeader.jsp | 8 | 10 | 0 | 6 | - |
| ClsDeleteForClsSystem.jsp | 3 | 3 | 1 | 0 | `ClsDeleteFromContext` |
| ClsDeleteForClsSystemMg.jsp | 3 | 3 | 1 | 0 | `ClsDeleteFromContext` |
| ClsDtlForClsSystem.jsp | 3 | 5 | 1 | 0 | `ClsUpdateFromContext` |
| ClsFileDownload.jsp | 3 | 12 | 2 | 0 | `ClsFileDownload` |
| ClsNewEntryForClsSystem.jsp | 3 | 5 | 1 | 0 | `ClsNewEntryFromContext` |
| CnAbstList.jsp | 50 | 148 | 11 | 1 | `CnAbstListPaging` |
| CnBiblioList.jsp | 18 | 57 | 3 | 1 | `CnAbstListForBiblioList` |
| CnBiblioListHeader.jsp | 29 | 84 | 5 | 0 | - |
| CnBiblioListTableBody.jsp | 7 | 9 | 3 | 0 | - |
| CnClassCodeDetail.jsp | 3 | 3 | 1 | 1 | - |
| CnClsAid.jsp | 3 | 12 | 1 | 0 | `CnClsAidSearch` |
| CnCpcAidResult.jsp | 3 | 3 | 1 | 0 | - |
| CnEpcAidResult.jsp | 3 | 3 | 1 | 0 | - |
| CnGazetteImageWin_list.jsp | 3 | 3 | 1 | 1 | - |
| CnGazetteImageWin_zoomin.jsp | 3 | 3 | 1 | 1 | - |
| CnIpcAidResult.jsp | 3 | 3 | 1 | 0 | - |
| CnPersonAidResult.jsp | 3 | 3 | 1 | 0 | - |
| CnSearchAid.jsp | 4 | 17 | 2 | 0 | `CnSearchAidSearch` |
| CnTechDicResult.jsp | 3 | 3 | 1 | 0 | - |

## 代表性缺失/定位器变更

### AiAutoClsEntry.jsp
- 缺失：file `trainFile` locator=[name='trainFile'] action=-

### AiAutoClsEntryList.jsp
- 缺失：file `trainFile` locator=[name='trainFile'] action=-

### AiAutoClsEvalRankSelect.jsp
- 缺失：button `<bean:message bundle='PATLICS_MESSAGE' key='btn.clsSelect.close'/>` locator=- action=-

### AiAutoClsSettingClass.jsp
- 缺失：button `<bean:message bundle='PATLICS_MESSAGE' key='btn.close'/>` locator=- action=-

### AiAutoClsSettingEvalRank.jsp
- 缺失：button `<bean:message bundle='PATLICS_MESSAGE' key='btn.close'/>` locator=- action=-

### AzureADUserListUpload.jsp
- 缺失：file `UserUpUploadForm` locator=[name='UserUpUploadForm'] action=-

### BiblioListSaveUpdateListSelect.jsp
- 缺失：button `<bean:message bundle='PATLICS_MESSAGE' key='btn.cancel' />` locator=- action=-

### BookmarkWord.jsp
- 缺失：button `<bean:message bundle='PATLICS_MESSAGE' key='btn.close'/>` locator=- action=-
- 定位器变更：form `fmMain` [name='fmMain'] -> #fmMain

### ClassificationCodeToolTip.jsp
- 定位器变更：form `CnClassCodeDetailForm` [name='CnClassCodeDetailForm'] -> #CnClassCodeDetailForm
- 定位器变更：form `EpClassCodeDetailForm` [name='EpClassCodeDetailForm'] -> #EpClassCodeDetailForm
- 定位器变更：form `EuClassCodeDetailForm` [name='EuClassCodeDetailForm'] -> #EuClassCodeDetailForm
- 定位器变更：form `JpClassCodeDetailForm` [name='JpClassCodeDetailForm'] -> #JpClassCodeDetailForm
- 定位器变更：form `PctClassCodeDetailForm` [name='PctClassCodeDetailForm'] -> #PctClassCodeDetailForm

### ClassificationMatrix.jsp
- 定位器变更：form `CnClassificationMatrixForm` [name='CnClassificationMatrixForm'] -> #CnClassificationMatrixForm
- 定位器变更：form `CnClassificationMatrixForm` [name='CnClassificationMatrixForm'] -> #CnClassificationMatrixForm
- 定位器变更：form `EuClassificationMatrixForm` [name='EuClassificationMatrixForm'] -> #EuClassificationMatrixForm
- 定位器变更：form `EuClassificationMatrixForm` [name='EuClassificationMatrixForm'] -> #EuClassificationMatrixForm
- 定位器变更：form `JpClassificationMatrixForm` [name='JpClassificationMatrixForm'] -> #JpClassificationMatrixForm

## Action -> 页面 Top 50

| Action | 页面数 | 示例页面 |
|---|---:|---|
| `ADD` | 6 | CnAnalyzeRankingHeader.jsp, EuAnalyzeRankingHeader.jsp, JpAnalyzeRankingHeader.jsp, PctAnalyzeRankingHeader.jsp, UsAnalyzeRankingHeader.jsp |
| `AbstListViewEntry` | 2 | AbstListEdit.jsp, UsAbstListEdit.jsp |
| `AbstSetSearch` | 1 | AbstSetSelectList.jsp |
| `AccessLogDownload` | 1 | AccessLogDownload.jsp |
| `AdminDivisionLogin` | 2 | AdminDivisionSsoError.jsp, login_adminDivisiontool.jsp |
| `AdminLogin` | 3 | AdminSsoError.jsp, Azure_Jump_Admin.jsp, login_admintool.jsp |
| `AdminTopMain` | 1 | adminLogin.jsp |
| `AiAutoClsAdministrationMain` | 3 | AiAutoClsAdministration.jsp, AiAutoClsFileAdministration.jsp, AiAutoClsListAdministration.jsp |
| `AiAutoClsClassSelect` | 1 | AiAutoClsClassSelect.jsp |
| `AiAutoClsEntry` | 2 | AiAutoClsEntry.jsp, AiAutoClsFileUpResult.jsp |
| `AiAutoClsEntryDisp` | 1 | AiAutoClsEvalRankSelect.jsp |
| `AiAutoClsEntryFile` | 1 | AiAutoClsEntryFile.jsp |
| `AiAutoClsEntryFileCheck` | 1 | AiAutoClsEntryFile.jsp |
| `AiAutoClsEntryList` | 1 | AiAutoClsEntryList.jsp |
| `AiAutoClsListPredictDisp` | 2 | AiAutoClsListPredictDisp.jsp, AiAutoClsListPredictMain.jsp |
| `AiAutoClsListPredictMain` | 1 | AiAutoClsListPredict.jsp |
| `AiAutoClsLogin` | 4 | AiAutoClsSsoError.jsp, Azure_Jump_AiAutoCls.jsp, JpAiAutoClsLogin.jsp, WwAiAutoClsLogin.jsp |
| `AiAutoClsPredictFile` | 1 | AiAutoClsPredictFile.jsp |
| `AiAutoClsPredictFileCheck` | 1 | AiAutoClsPredictFile.jsp |
| `AiEvalEntry` | 2 | AiEvalBlocEntry.jsp, AiEvalEntry.jsp |
| `AssigneeIdentificationDictionaryDisp` | 1 | AssigneeDictionary.jsp |
| `AzureADUserUpAdminUpload` | 2 | AzureADUserListUpload.jsp, UserUpUserListCtrl.jsp |
| `BiblioComplexSetEntry` | 1 | ListViewComplexSet.jsp |
| `BiblioListSaveUpdateListSelect` | 1 | BiblioListSaveUpdateListSelect.jsp |
| `BiblioListViewEntry` | 1 | ListViewEdit.jsp |
| `BiblioSetSearch` | 2 | BiblioComplexSelectList.jsp, BiblioSetSelectList.jsp |
| `ChangeKizashiSdi` | 1 | SdiExpEntry.jsp |
| `ClsCopy` | 1 | ClsCopy.jsp |
| `ClsDelete` | 3 | ClsDeleteForClsSystem.jsp, ClsDeleteForClsSystemMg.jsp, ClsDtl.jsp |
| `ClsDeleteFromContext` | 2 | ClsDeleteForClsSystem.jsp, ClsDeleteForClsSystemMg.jsp |
| `ClsDeleteOut` | 1 | ClsDtl.jsp |
| `ClsDtlDisp` | 1 | ClsSystem2.jsp |
| `ClsFileDownload` | 1 | ClsFileDownload.jsp |
| `ClsFileUploadConf` | 1 | ClsFileUpload.jsp |
| `ClsFileUploadEntry` | 1 | ClsFileUploadConf.jsp |
| `ClsGroupManageDisp` | 1 | ClsGroupManage.jsp |
| `ClsGroupNewEntry` | 1 | ClsGroupManage.jsp |
| `ClsNewEntry` | 2 | ClsNewEntry.jsp, ClsNewEntryForClsSystem.jsp |
| `ClsNewEntryDisp` | 1 | ClsDtl.jsp |
| `ClsNewEntryDispForClsSelectDisp` | 1 | ClsSelect.jsp |
| `ClsNewEntryFromContext` | 1 | ClsNewEntryForClsSystem.jsp |
| `ClsUpdate` | 2 | ClsDtl.jsp, ClsDtlForClsSystem.jsp |
| `ClsUpdateFromContext` | 1 | ClsDtlForClsSystem.jsp |
| `CnAbstListForBiblioList` | 4 | CnBiblioList.jsp, CnBiblioList0.jsp, CnBiblioListPrint.jsp, CnServerError.jsp |
| `CnAbstListPaging` | 3 | CnAbstList.jsp, CnAbstList0.jsp, CnAbstListPrint.jsp |
| `CnAbstPDFDownload` | 1 | CnAbstPDFDownload.jsp |
| `CnAbstPDFUnitDownload` | 1 | CnAbstPDFDownloadSetting.jsp |
| `CnAnalyzeRankingFrame` | 1 | CnAnalyzeRankingHeader.jsp |
| `CnAnalyzeRankingHeader` | 1 | CnAnalyzeRankingFrame.jsp |
| `CnBiblioFileDownload` | 1 | CnBiblioFileDownloadDisp.jsp |

## 数量偏差分析

### 结论

`checklist.md` 行数远高于 `checklist_2.md`，主要不是页面数量导致，而是 New 环境的 Spring JSP 标签被 `jsp_scanner` 过度识别为 `form` 元素。当前 scanner 正则会把 `<form:hidden>`、`<form:option>`、`<form:input>`、`<form:select>` 等 Spring 表单字段按 `form` 命中；Legacy 的 `<html:hidden>`、`<html:text>` 等字段标签不在扫描范围内，因此没有产生对称膨胀。

数据上看，New 的 `form` 总数是 15,356，但其中真正的表单容器只有 1,369 个（`form:form` 829 + 原生 `form` 538 + `html:form` 2），与 Legacy 的 `form=1,369` 完全一致。额外的 13,987 个 `form` 实际是 Spring 字段/选项标签。

### 元素密度

| 指标 | New: elements.json | Legacy: elements_2.json | 差异 |
|---|---:|---:|---:|
| 页面数 | 1,685 | 1,673 | +12 |
| 元素总数 | 20,514 | 5,244 | +15,270 |
| 每页平均元素数 | 12.17 | 3.13 | 3.89 倍 |
| 每页中位数元素数 | 3 | 1 | 3.00 倍 |
| form | 15,356 | 1,369 | 11.22 倍 |
| button | 5,106 | 3,345 | 1.53 倍 |
| file | 0 | 21 | -21 |
| link | 52 | 509 | -457 |
| hidden 字段识别数 | 4,915 | 0 | +4,915 |
| locator 缺失元素数 | 15,587 | 2,215 | +13,372 |

New 中被误归类为 `form` 的 Spring 字段标签分布如下：

| Spring 标签 | 数量 |
|---|---:|
| `form:hidden` | 4,915 |
| `form:option` | 4,684 |
| `form:input` | 1,608 |
| `form:select` | 1,505 |
| `form:radiobutton` | 690 |
| `form:checkbox` | 284 |
| `form:options` | 146 |
| `form:textarea` | 125 |
| `form:password` | 30 |

如果把这些 Spring 字段标签从 New 的 `form` 中剥离，只保留真正表单容器，New 的可比元素约为 6,527 个（真实 form 1,369 + button 5,106 + link 52），与 Legacy 的 5,244 个接近得多。剩余差异主要来自 New 中按钮识别更多，以及 New 有少量 `_part.jsp` 片段页被单独扫描。

### checklist 行数为什么爆炸

`checklist_generator` 对元素是线性放大的：

- `form` / `file`：每个元素生成 4 条测试用例。
- `button` / `link`：每个元素生成 3 条测试用例。
- “元素证据”章节再为每个唯一元素追加 1 行。

因此当前数据可以精确解释 Markdown 行数：

| 指标 | New | Legacy | 差异 |
|---|---:|---:|---:|
| 生成测试用例数 | 76,898 | 17,122 | +59,776 |
| 元素证据行数 | 20,514 | 5,244 | +15,270 |
| 可解释主体行数 | 97,412 | 22,366 | +75,046 |
| 实际 checklist 行数 | 97,444 | 22,398 | +75,046 |

实际行数与公式差异只来自固定标题/表头等 32 行。因此 New checklist 更多，根因是“元素数量变了”，并且主要是 Spring 字段标签被当作 `form` 类型；不是 `checklist_generator` 改变了类型规则。

如果按“真实表单容器”重新估算，New 的用例数会降到约 20,950 条（form 1,369 * 4 + button 5,106 * 3 + link 52 * 3），不再是 76,898 条。

### 行数爆炸 Top 5 页面

“行数贡献”按当前 generator 规则计算：`form/file * 4 + button/link * 3 + 元素证据行`。以下是 New 相对 Legacy 增量最大的页面：

| 页面 | New 元素 | Legacy 元素 | New hidden | New 主要来源 | 预计行数增量 |
|---|---:|---:|---:|---|---:|
| `RsvDlListReserve.jsp` | 235 | 1 | 75 | `form:option` 104, `form:hidden` 75, `form:select` 34 | +1,163 |
| `RsvDlDocListReserveRegist_part.jsp` | 217 | 0 | 33 | New 独有 `_part.jsp`；`form:option` 125, `form:select` 37, `form:hidden` 33 | +1,082 |
| `WwEasyInputPullText.jsp` | 220 | 0 | 0 | `input type=button` 128, `form:input` 72 | +972 |
| `AbstListEdit_part.jsp` | 194 | 0 | 22 | New 独有 `_part.jsp`；`form:option` 115, `form:select` 36, `form:hidden` 22 | +970 |
| `UsEasyInput.jsp` | 199 | 15 | 1 | `form:input` 95, `input type=button` 84 | +850 |

这些 Top 页面说明了两件事：

1. New 中确实有 `_part.jsp` 片段页被单独扫描，带来少量页面级新增。
2. 更大的影响来自同一业务页内部的字段标签膨胀。例如 `RsvDlListReserve.jsp` 的 Legacy 只有 1 个 `html:form`，New 有 228 个被归为 `form` 的元素，其中 75 个是 `form:hidden`，104 个是 `form:option`。

### 对主祭疑问的回答

页面总数接近但 checklist 行数相差约 4.35 倍，是因为元素密度从每页 3.13 个上升到每页 12.17 个；而元素密度上升主要由 scanner 对 Spring `<form:*>` 标签的解析方式造成。New 架构中的公共片段页和按钮数量增加也有贡献，但不是主因。

后续如需让 New/Legacy 数量可比，建议在 `jsp_scanner` 中把 `form:hidden`、`form:input`、`form:select`、`form:option` 等字段标签分类为 `field` 或直接排除出 checklist 生成范围，只保留真正的 `form:form` / `html:form` / 原生 `form` 作为 `form` 测试入口。
