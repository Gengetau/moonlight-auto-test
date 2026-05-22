# Legacy/New JSP 降维比对摘要

## 总览

- Legacy 页面数：1673
- New 页面数：1685
- 同名页面匹配数：1659
- Legacy 独有页面数：0
- New 独有页面数：11
- 高风险页面数：288
- 中风险页面数：581
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
| AbstListEdit.jsp | 352 | 158 | 30 | 1 | `AbstListViewEntry` |
| AbstPDFDownload.jsp | 86 | 86 | 10 | 1 | `JpAbstPDFDownload` |
| AiAutoClsEntry.jsp | 17 | 24 | 3 | 1 | `AiAutoClsEntry` |
| AiAutoClsEntryList.jsp | 17 | 23 | 3 | 1 | `AiAutoClsEntryList` |
| AiEvalBlocEntry.jsp | 13 | 14 | 3 | 1 | `AiEvalEntry` |
| AiEvalEntry.jsp | 15 | 16 | 3 | 1 | `AiEvalEntry` |
| BiblioComplexSelectList.jsp | 3 | 3 | 1 | 1 | `BiblioSetSearch` |
| BiblioFileDownloadDisp.jsp | 60 | 60 | 12 | 1 | `JpBiblioFileDownload` |
| ClassificationMatrixFrame.jsp | 24 | 24 | 0 | 6 | `CnClassificationMatrixHeader`, `EuClassificationMatrixHeader`, `JpClassificationMatrixHeader` |
| ClsCopy.jsp | 17 | 18 | 4 | 1 | `ClsCopy` |
| ClsFileUploadConf.jsp | 18 | 18 | 0 | 4 | `ClsFileUploadEntry` |
| CnAbstList.jsp | 158 | 148 | 53 | 3 | `CnAbstListPaging` |
| CnAbstList0.jsp | 103 | 95 | 46 | 1 | `CnAbstListPaging` |
| CnAbstPDFDownload.jsp | 86 | 86 | 11 | 1 | `CnAbstPDFDownload` |
| CnAnalyzeRankingHeader.jsp | 23 | 23 | 3 | 0 | - |
| CnBiblioFileDownloadDisp.jsp | 58 | 58 | 12 | 1 | `CnBiblioFileDownload` |
| CnBiblioList.jsp | 63 | 57 | 2 | 3 | `CnAbstListForBiblioList` |
| CnBiblioList0.jsp | 102 | 94 | 45 | 1 | `CnAbstListForBiblioList` |
| CnBiblioListHeader.jsp | 92 | 84 | 48 | 0 | - |
| CnBiblioListTableBody.jsp | 9 | 9 | 3 | 0 | - |
| CnClsAid.jsp | 11 | 12 | 4 | 1 | `CnClsAidSearch` |
| CnEvalDataFileDownloadDisp.jsp | 46 | 46 | 8 | 1 | `CnEvalDataFileDownload` |
| CnEvalFileDownloadDisp.jsp | 49 | 49 | 10 | 1 | `CnEvalFileDownload` |
| CnEvalFreeAid.jsp | 9 | 10 | 4 | 1 | `CnRankAxisAidSearch` |
| CnExternalMapFileDownloadDisp.jsp | 28 | 28 | 5 | 1 | `CnExternalMapFileDownload` |
| CnListFocusSet.jsp | 46 | 88 | 3 | 1 | `CnListFocus` |
| CnRankAxisAid.jsp | 15 | 16 | 3 | 1 | `CnRankAxisAidSearch` |
| CnRankEleAid.jsp | 18 | 18 | 4 | 1 | `CnRankEleAidSearch` |
| CnSearchAid.jsp | 13 | 17 | 3 | 1 | `CnSearchAidSearch` |
| CntChargeAddUserSearchList.jsp | 3 | 3 | 1 | 1 | `CntChargeAddUserSearch` |

## 代表性缺失/定位器变更

### AbstListEdit.jsp
- 缺失：field `<%= slctItemIds[index].toString()%>` locator=- action=-
- 缺失：field `<%= slctItemIds[index].toString()%>` locator=- action=-
- 缺失：field `SET_ID` locator=[name='SET_ID'] action=-
- 缺失：field `1` locator=- action=-
- 缺失：field `2` locator=- action=-
- 定位器变更：form `AbstListViewEntry` form[action*='AbstListViewEntry'] -> form[name='AbstListViewEditForm']

### AbstPDFDownload.jsp
- 缺失：field `1` locator=- action=-
- 缺失：field `0` locator=- action=-
- 缺失：field `<%= entSecItemIds[index].toString()%>` locator=- action=-
- 缺失：field `<%= key_value %>` locator=- action=-
- 缺失：field `2` locator=- action=-
- 定位器变更：form `JpAbstPDFDownload` form[action*='JpAbstPDFDownload'] -> form[name='JpAbstPDFDownloaForm']

### AiAutoClsEntry.jsp
- 缺失：field `0` locator=- action=-
- 缺失：field `1` locator=- action=-
- 缺失：field `clsGroupId` locator=[name='clsGroupId'] action=-
- 定位器变更：form `AiAutoClsEntry` form[action*='AiAutoClsEntry'] -> form[name='AiAutoClsEntryForm']

### AiAutoClsEntryList.jsp
- 缺失：field `0` locator=- action=-
- 缺失：field `1` locator=- action=-
- 缺失：field `clsGroupId` locator=[name='clsGroupId'] action=-
- 定位器变更：form `AiAutoClsEntryList` form[action*='AiAutoClsEntryList'] -> form[name='AiAutoClsEntryForm']

### AiEvalBlocEntry.jsp
- 缺失：field `0` locator=- action=-
- 缺失：field `1` locator=- action=-
- 缺失：field `2` locator=- action=-
- 定位器变更：form `AiEvalEntry` form[action*='AiEvalEntry'] -> form[name='AiEvalEntryForm']

### AiEvalEntry.jsp
- 缺失：field `0` locator=- action=-
- 缺失：field `1` locator=- action=-
- 缺失：field `2` locator=- action=-
- 定位器变更：form `AiEvalEntry` form[action*='AiEvalEntry'] -> form[name='AiEvalEntryForm']

### BiblioComplexSelectList.jsp
- 缺失：field `INIT_ITEM_ID` locator=[name='INIT_ITEM_ID'] action=-
- 定位器变更：form `BiblioSetSearch` form[action*='BiblioSetSearch'] -> form[name='BiblioListViewEntryForm']

### BiblioFileDownloadDisp.jsp
- 缺失：field `tsv` locator=- action=-
- 缺失：field `csv` locator=- action=-
- 缺失：field `0` locator=- action=-
- 缺失：field `1` locator=- action=-
- 缺失：field `2` locator=- action=-
- 定位器变更：form `JpBiblioFileDownload` form[action*='JpBiblioFileDownload'] -> form[name='JpBiblioFileDownloadForm']

### ClassificationMatrixFrame.jsp
- 定位器变更：form `classMatrixForm` form[action*='CnClassificationMatrixHeader'] -> form[name='CnClassificationMatrixForm']
- 定位器变更：form `classMatrixForm` form[action*='EuClassificationMatrixHeader'] -> form[name='EuClassificationMatrixForm']
- 定位器变更：form `classMatrixForm` form[action*='JpClassificationMatrixHeader'] -> form[name='JpClassificationMatrixForm']
- 定位器变更：form `classMatrixForm` form[action*='PctClassificationMatrixHeader'] -> form[name='PctClassificationMatrixForm']
- 定位器变更：form `classMatrixForm` form[action*='UsClassificationMatrixHeader'] -> form[name='UsClassificationMatrixForm']

### ClsCopy.jsp
- 缺失：field `html:option` locator=- action=-
- 缺失：field `<%= key_value %>` locator=- action=-
- 缺失：field `<%= key_value %>` locator=- action=-
- 缺失：field `clsGroupId` locator=[name='clsGroupId'] action=-
- 定位器变更：form `ClsCopy` form[action*='ClsCopy'] -> form[name='ClsCopyForm']

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
