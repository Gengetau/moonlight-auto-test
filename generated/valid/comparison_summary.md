# Legacy/New JSP 降维比对摘要

## 总览

- Legacy 页面数：1673
- New 页面数：1685
- 同名页面匹配数：1659
- Legacy 独有页面数：0
- New 独有页面数：11
- 高风险页面数：377
- 中风险页面数：466
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
| AbstListEdit.jsp | 352 | 158 | 33 | 0 | `AbstListViewEntry` |
| AbstPDFDownload.jsp | 86 | 86 | 5 | 2 | `JpAbstPDFDownload` |
| AiAutoClsSettingClass.jsp | 3 | 3 | 1 | 0 | - |
| AiAutoClsSettingEvalRank.jsp | 3 | 3 | 1 | 0 | - |
| AzureADUserListUpload.jsp | 3 | 2 | 1 | 0 | `AzureADUserUpAdminUpload`, `UserUpUserListCtrl` |
| BiblioComplexSelectList.jsp | 3 | 3 | 1 | 0 | `BiblioSetSearch` |
| BiblioFileDownloadDisp.jsp | 60 | 60 | 5 | 0 | `JpBiblioFileDownload` |
| BookmarkWord.jsp | 2 | 2 | 1 | 1 | - |
| ClassificationCodeToolTip.jsp | 7 | 7 | 0 | 7 | - |
| ClassificationMatrix.jsp | 12 | 12 | 0 | 12 | - |
| ClassificationMatrixFrame.jsp | 24 | 24 | 6 | 0 | `CnClassificationMatrixHeader`, `EuClassificationMatrixHeader`, `JpClassificationMatrixHeader` |
| ClassificationMatrixHeader.jsp | 8 | 10 | 0 | 6 | - |
| ClsCopy.jsp | 17 | 18 | 3 | 0 | `ClsCopy` |
| ClsDeleteForClsSystem.jsp | 3 | 3 | 1 | 0 | `ClsDeleteFromContext` |
| ClsDeleteForClsSystemMg.jsp | 3 | 3 | 1 | 0 | `ClsDeleteFromContext` |
| ClsDtlForClsSystem.jsp | 5 | 5 | 2 | 0 | `ClsUpdateFromContext` |
| ClsNewEntryForClsSystem.jsp | 5 | 5 | 2 | 0 | `ClsNewEntryFromContext` |
| CnAbstList.jsp | 158 | 148 | 15 | 1 | `CnAbstListPaging` |
| CnAbstList0.jsp | 103 | 95 | 4 | 0 | `CnAbstListPaging` |
| CnAbstPDFDownload.jsp | 86 | 86 | 6 | 0 | `CnAbstPDFDownload` |
| CnAnalyzeRankingHeader.jsp | 23 | 23 | 1 | 5 | - |
| CnBiblioFileDownloadDisp.jsp | 58 | 58 | 5 | 0 | `CnBiblioFileDownload` |
| CnBiblioList.jsp | 63 | 57 | 4 | 1 | `CnAbstListForBiblioList` |
| CnBiblioList0.jsp | 102 | 94 | 4 | 0 | `CnAbstListForBiblioList` |
| CnBiblioListHeader.jsp | 92 | 84 | 7 | 0 | - |
| CnBiblioListTableBody.jsp | 9 | 9 | 4 | 0 | - |
| CnClassCodeDetail.jsp | 3 | 3 | 1 | 1 | - |
| CnClsAid.jsp | 11 | 12 | 4 | 0 | `CnClsAidSearch` |
| CnCpcAidResult.jsp | 3 | 3 | 1 | 0 | - |
| CnEpcAidResult.jsp | 3 | 3 | 1 | 0 | - |

## 代表性缺失/定位器变更

### AbstListEdit.jsp
- 缺失：field `AbstListViewEditForm` locator=[name='AbstListViewEditForm'] action=-
- 缺失：field `<%= slctItemIds[index].toString()%>` locator=- action=-
- 缺失：field `SET_ID` locator=[name='SET_ID'] action=-
- 缺失：field `zumen` locator=[name='zumen'] action=-
- 缺失：field `1` locator=- action=-

### AbstPDFDownload.jsp
- 缺失：field `<%= entSecItemIds[index].toString()%>` locator=- action=-
- 缺失：field `JpAbstListForm` locator=[name='JpAbstListForm'] action=-
- 缺失：field `<%= slctItemIds[index].toString()%>` locator=- action=-
- 缺失：field `SLCT_ITEM_ID` locator=[name='SLCT_ITEM_ID'] action=-
- 缺失：field `INIT_ITEM_ID` locator=[name='INIT_ITEM_ID'] action=-
- 定位器变更：field `JpAbstListForm` [name='JpAbstListForm'] -> [name='standardFlag']
- 定位器变更：field `JpAbstListForm` [name='JpAbstListForm'] -> [name='standardFlag']

### AiAutoClsSettingClass.jsp
- 缺失：button `<bean:message bundle='PATLICS_MESSAGE' key='btn.close'/>` locator=- action=-

### AiAutoClsSettingEvalRank.jsp
- 缺失：button `<bean:message bundle='PATLICS_MESSAGE' key='btn.close'/>` locator=- action=-

### AzureADUserListUpload.jsp
- 缺失：file `UserUpUploadForm` locator=[name='UserUpUploadForm'] action=-

### BiblioComplexSelectList.jsp
- 缺失：field `INIT_ITEM_ID` locator=[name='INIT_ITEM_ID'] action=-

### BiblioFileDownloadDisp.jsp
- 缺失：field `JpBiblioFileDownloadForm` locator=[name='JpBiblioFileDownloadForm'] action=-
- 缺失：field `<%= entSecItemIds[index].toString()%>` locator=- action=-
- 缺失：field `<%= slctItemIds[index].toString()%>` locator=- action=-
- 缺失：field `SLCT_ITEM_ID` locator=[name='SLCT_ITEM_ID'] action=-
- 缺失：field `INIT_ITEM_ID` locator=[name='INIT_ITEM_ID'] action=-

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
