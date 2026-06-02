import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from src.page_aliases import page_aliases, page_matches


DEFAULT_CHECKLIST_PATH = "generated/valid/migration_checklist.xlsx"
DEFAULT_ROUTE_MAP_PATH = "generated/valid/route"
GUIDED_CHECKLIST_SCHEMA = "moonlight.guided_checklist.v1"
GUIDED_CHECKLIST_DIR = Path("generated/valid/guided_checklists")
GUIDED_CHECKLIST_TARGETS = [
    {
        "page_id": "JpGazetteForNumberSearch.do",
        "actual_page_id": "GazetteMainFrame.jsp",
        "template_id": "gazette_detail",
        "label": "Gazette detail window",
        "filename": "jpgazettefornumbersearch.guided_checklist.json",
        "direct_url_allowed": False,
        "description": "High-frequency gazette detail window opened from result/list flows.",
    },
    {
        "page_id": "JpNonjavaScreeningForEasySearch.do?method=unRead",
        "actual_page_id": "NonjavaScreeningMainFrame.jsp",
        "template_id": "screening_workbench",
        "label": "Unread screening workbench",
        "filename": "jpnonjavascreeningforeasysearch_unread.guided_checklist.json",
        "direct_url_allowed": False,
        "description": "High-frequency non-Java screening workbench opened from business flows.",
    },
    {
        "page_id": "WwPersonalNameDicDispForEasySearch.do",
        "actual_page_id": "WwPersonAidMain.jsp",
        "template_id": "person_name_dictionary_assist",
        "label": "Ww person name dictionary assist",
        "filename": "wwpersonaidmain.guided_checklist.json",
        "direct_url_allowed": False,
        "description": "Person-name dictionary assist popup opened from Ww easy/professional search name fields.",
    },
]
DEFAULT_NEGATIVE_PROFILES = [
    {
        "profile": "negative_js_error",
        "label": "JS error",
        "description": "console.error + JavaScript runtime error",
    },
    {
        "profile": "negative_http_500",
        "label": "HTTP 500",
        "description": "Mock target request as HTTP 500",
    },
    {
        "profile": "negative_network_abort",
        "label": "Network abort",
        "description": "Abort target request",
    },
    {
        "profile": "negative_file_upload",
        "label": "Invalid file upload",
        "description": "Invalid/negative file upload cases",
    },
]


def quote(value: Any) -> str:
    text = str(value)
    return '"' + text.replace('"', '\\"') + '"'


def browser_key(browser_name: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "_", str(browser_name or "browser").lower()).strip("_") or "browser"


def safe_page_key(page_id: str) -> str:
    text = Path(str(page_id or "selected_pages").replace("\\", "/")).name
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "selected_pages"


def split_case_types(value: Any) -> List[str]:
    return [item.strip() for item in re.split(r"[\r\n,;]+", str(value or "")) if item.strip()]


def target_page_name(value: Any) -> str:
    text = Path(str(value or "").replace("\\", "/")).name
    return text.lower()


def target_page_aliases(value: Any) -> set[str]:
    return page_aliases(value)


def page_matches_target(row_page: Any, page_id: Any) -> bool:
    return target_page_name(row_page) == target_page_name(page_id) or page_matches(row_page, page_id)


def guided_checklist_target_labels() -> List[str]:
    return [
        f"{target['page_id']}    {target['label']} / actual: {target['actual_page_id']} / {target['template_id']}"
        for target in GUIDED_CHECKLIST_TARGETS
    ]


def guided_checklist_meta(page_id: Any) -> Optional[Dict[str, Any]]:
    aliases = target_page_aliases(page_id)
    raw = str(page_id or "").strip().lower()
    for target in GUIDED_CHECKLIST_TARGETS:
        target_aliases = target_page_aliases(target["page_id"])
        if raw == target["page_id"].lower() or aliases & target_aliases:
            return target
    return None


def guided_checklist_path_for(page_id: Any, base_dir: Any = GUIDED_CHECKLIST_DIR) -> Optional[Path]:
    meta = guided_checklist_meta(page_id)
    if meta:
        return Path(base_dir) / str(meta["filename"])

    raw_page = Path(str(page_id or "").strip().split("?", 1)[0].replace("\\", "/")).name
    if not raw_page:
        return None
    screen_id = re.sub(r"\.(?:jsp|do)$", "", raw_page, flags=re.IGNORECASE).strip()
    if not screen_id:
        return None
    return Path(base_dir) / f"{screen_id}_checklist.json"


def _starter_case(
    *,
    case_id: str,
    title: str,
    steps: List[Dict[str, Any]],
    expected: Optional[Dict[str, Any]] = None,
    risk_level: str = "safe",
    automation_mode: str = "auto",
    destructive: bool = False,
    requires: Optional[List[str]] = None,
    case_type: Optional[str] = None,
    action_type: Optional[str] = None,
    value: Optional[str] = None,
    generated_by: Optional[str] = None,
) -> Dict[str, Any]:
    payload = {
        "case_id": case_id,
        "title": title,
        "risk_level": risk_level,
        "automation_mode": automation_mode,
        "destructive": destructive,
        "steps": steps,
        "expected": expected or {"type": "visible", "value": "page evidence captured"},
    }
    if requires:
        payload["requires"] = requires
    if case_type:
        payload["case_type"] = case_type
    if action_type:
        payload["action_type"] = action_type
    if value:
        payload["value"] = value
        payload["test_data"] = value
    if generated_by:
        payload["generated_by"] = generated_by
    return payload


def starter_guided_checklist(page_id: Any) -> Dict[str, Any]:
    meta = guided_checklist_meta(page_id) or {
        "page_id": str(page_id or "TargetPage"),
        "template_id": "guided_page",
        "label": "Guided page",
        "direct_url_allowed": False,
        "description": "Starter guided checklist.",
    }
    target_page = str(meta["page_id"])
    template_id = str(meta.get("template_id") or "guided_page")

    if template_id == "gazette_detail":
        cases = [
            _starter_case(
                case_id="gazette_detail_layout_frames_visible",
                title="公報明細の全体 layout と主要 frame が表示される",
                steps=[
                    {"action_type": "assert_visible", "locator": "#gazetteMainFrame"},
                    {"action_type": "assert_visible", "locator": "#rightFrame"},
                    {
                        "action_type": "assert_visible",
                        "locator": "frame[name='frMenuFrame'], iframe[name='frMenuFrame']",
                    },
                ],
                expected={"type": "visible", "value": "gazette layout/menu frame"},
            ),
            _starter_case(
                case_id="gazette_menu_title_close_visible",
                title="左メニューに公報明細タイトルと閉じるボタンが表示される",
                steps=[
                    {
                        "action_type": "assert_text",
                        "locator": "form[name='GazetteForm']",
                        "value": "公報明細表示",
                    },
                    {
                        "action_type": "assert_visible",
                        "locator": "input[onclick*='parent.fnClose'], input[value='閉じる']",
                    },
                ],
                expected={"type": "visible", "value": "title and close control"},
            ),
            _starter_case(
                case_id="gazette_document_paging_controls_visible",
                title="文献件数、表示位置、呼出元文献一覧の操作が表示される",
                steps=[
                    {
                        "action_type": "assert_text",
                        "locator": "form[name='GazetteForm']",
                        "value": "件目を表示",
                    },
                    {"action_type": "assert_visible", "locator": "select[name='pageId']"},
                    {"action_type": "assert_visible", "locator": "select[name='historyNo']"},
                ],
                expected={"type": "visible", "value": "document paging controls"},
            ),
            _starter_case(
                case_id="gazette_document_action_links_visible",
                title="評価、ハイライト、ウォッチ登録、テキスト印刷入口が表示される",
                steps=[
                    {"action_type": "assert_visible", "locator": "a[onclick*='evalEntry']"},
                    {"action_type": "assert_visible", "locator": "#highlightId, a[onclick*='highlightOpen']"},
                    {"action_type": "assert_visible", "locator": "a[onclick*='fnWatch']"},
                    {"action_type": "assert_visible", "locator": "a[onclick*='fnTextPrint']"},
                ],
                expected={"type": "visible", "value": "document action links"},
            ),
            _starter_case(
                case_id="gazette_related_info_links_visible",
                title="評価情報、紙公報PDF、経過情報、類似文書検索入口が表示される",
                steps=[
                    {"action_type": "assert_visible", "locator": "a[onclick*='evalList']"},
                    {
                        "action_type": "assert_visible",
                        "locator": "a[onclick*='fnViewPDF']",
                    },
                    {"action_type": "assert_visible", "locator": "a[onclick*='fnViewKEIKA']"},
                    {"action_type": "assert_visible", "locator": "a[onclick*='fnSearchSimilarDoc']"},
                ],
                expected={"type": "visible", "value": "related information links"},
            ),
            _starter_case(
                case_id="gazette_family_map_links_visible",
                title="Family、引用、分割変更、社内分類の関連マップ入口が表示される",
                steps=[
                    {"action_type": "assert_visible", "locator": "a[onclick*='fnViewFamily']"},
                    {"action_type": "assert_visible", "locator": "a[onclick*='fnViewFamilyMap']"},
                    {"action_type": "assert_visible", "locator": "a[onclick*='fnViewCitationMap']"},
                    {"action_type": "assert_visible", "locator": "a[onclick*='fnViewDivideChangeMap']"},
                    {"action_type": "assert_visible", "locator": "a[onclick*='fnViewCpc']"},
                ],
                expected={"type": "visible", "value": "family/citation/map links"},
            ),
            _starter_case(
                case_id="gazette_text_anchor_links_visible",
                title="書誌、要約、詳細な説明、請求項の本文アンカーが表示される",
                steps=[
                    {"action_type": "assert_visible", "locator": "a[value='BIBLIO']"},
                    {"action_type": "assert_visible", "locator": "a[value='ABST']"},
                    {"action_type": "assert_visible", "locator": "a[value='DESCRIPT']"},
                    {"action_type": "assert_visible", "locator": "a[value='CLAIMS']"},
                ],
                expected={"type": "visible", "value": "text anchor links"},
            ),
            _starter_case(
                case_id="gazette_batch_operation_links_visible",
                title="ファイル保存、PDF印刷、業務連携入口が表示される",
                steps=[
                    {"action_type": "assert_visible", "locator": "a[onclick*='fnSaveDocs']"},
                    {"action_type": "assert_visible", "locator": "a[onclick*='fnPrint']"},
                    {"action_type": "assert_visible", "locator": "a[onclick*='fnBizLink']"},
                ],
                expected={"type": "visible", "value": "batch operation links"},
            ),
            _starter_case(
                case_id="gazette_text_body_bibliographic_content_visible",
                title="本文 frame に公開番号、公開日、発明名称、分類情報が表示される",
                steps=[
                    {"action_type": "assert_text", "locator": "#tableBody", "value": "【公開番号】"},
                    {"action_type": "assert_text", "locator": "#tableBody", "value": "【公開日】"},
                    {"action_type": "assert_text", "locator": "#tableBody", "value": "【発明の名称】"},
                    {"action_type": "assert_text", "locator": "#tableBody", "value": "【国際特許分類】"},
                ],
                expected={"type": "text_visible", "value": "bibliographic body text"},
            ),
            _starter_case(
                case_id="gazette_classification_links_visible",
                title="IPC/FI/Fターム等の分類リンクが本文内に表示される",
                steps=[
                    {"action_type": "assert_visible", "locator": ".classification_link"},
                    {"action_type": "assert_text", "locator": "#tableBody", "value": "Ａ６１"},
                ],
                expected={"type": "visible", "value": "classification links"},
            ),
            _starter_case(
                case_id="gazette_latest_information_panel_visible",
                title="最新情報 panel に権利者、IPC、FI 等の更新情報が表示される",
                steps=[
                    {"action_type": "assert_visible", "locator": "#lastInfoTable"},
                    {"action_type": "assert_text", "locator": "#lastInfoTable", "value": "出願人・権利者"},
                    {"action_type": "assert_text", "locator": "#lastInfoTable", "value": "IPC"},
                    {"action_type": "assert_text", "locator": "#lastInfoTable", "value": "FI"},
                ],
                expected={"type": "text_visible", "value": "latest information panel"},
            ),
            _starter_case(
                case_id="gazette_image_navigation_controls_visible",
                title="図面 frame の前後移動、ページ数、表示倍率操作が表示される",
                steps=[
                    {"action_type": "assert_visible", "locator": "a[href*='fnNextImg']"},
                    {"action_type": "assert_visible", "locator": "a[onclick*='rotateRight']"},
                    {"action_type": "assert_visible", "locator": "a[onclick*='scaleUp']"},
                    {"action_type": "assert_visible", "locator": "a[onclick*='scaleDown']"},
                ],
                expected={"type": "visible", "value": "image navigation controls"},
            ),
            _starter_case(
                case_id="gazette_anchor_biblio_jump",
                title="書誌アンカーを選択して本文の書誌位置へ移動できる",
                steps=[
                    {"action_type": "click", "locator": "a[value='BIBLIO']"},
                    {"action_type": "snapshot", "locator": "__page__"},
                ],
                expected={"type": "screenshot", "value": "biblio anchor result"},
            ),
            _starter_case(
                case_id="gazette_anchor_abstract_jump",
                title="要約アンカーを選択して本文の要約位置へ移動できる",
                steps=[
                    {"action_type": "click", "locator": "a[value='ABST']"},
                    {"action_type": "snapshot", "locator": "__page__"},
                ],
                expected={"type": "screenshot", "value": "abstract anchor result"},
            ),
            _starter_case(
                case_id="gazette_anchor_claims_jump",
                title="請求項アンカーを選択して本文の請求項位置へ移動できる",
                steps=[
                    {"action_type": "click", "locator": "a[value='CLAIMS']"},
                    {"action_type": "snapshot", "locator": "__page__"},
                ],
                expected={"type": "screenshot", "value": "claims anchor result"},
            ),
            _starter_case(
                case_id="gazette_image_next_page_operation",
                title="図面の次へ操作で図面表示が更新される",
                steps=[
                    {"action_type": "click", "locator": "a[href*='fnNextImg']"},
                    {"action_type": "snapshot", "locator": "__page__"},
                ],
                expected={"type": "screenshot", "value": "next image result"},
            ),
            _starter_case(
                case_id="gazette_image_zoom_controls_operation",
                title="図面の拡大、縮小、回転入口が操作可能である",
                steps=[
                    {"action_type": "click", "locator": "a[onclick*='scaleUp']"},
                    {"action_type": "click", "locator": "a[onclick*='scaleDown']"},
                    {"action_type": "click", "locator": "a[onclick*='rotateRight']"},
                    {"action_type": "snapshot", "locator": "__page__"},
                ],
                expected={"type": "screenshot", "value": "image transform controls result"},
            ),
            _starter_case(
                case_id="gazette_pdf_entry_semi_auto",
                title="紙公報PDF入口を半自動で確認する",
                automation_mode="semi-auto",
                steps=[
                    {"action_type": "click", "locator": "a[onclick*='fnViewPDF']"},
                    {"action_type": "snapshot", "locator": "__page__"},
                ],
                expected={"type": "popup_or_navigation", "value": "paper PDF entry"},
            ),
            _starter_case(
                case_id="gazette_pdf_print_entry_semi_auto",
                title="PDF印刷入口を半自動で確認する",
                automation_mode="semi-auto",
                steps=[
                    {"action_type": "print", "locator": "a[onclick*='fnPrint']"},
                ],
                expected={"type": "print_invocation", "value": "PDF print"},
            ),
            _starter_case(
                case_id="gazette_file_save_entry_semi_auto",
                title="ファイル保存入口を半自動で確認する",
                automation_mode="semi-auto",
                steps=[
                    {"action_type": "click", "locator": "a[onclick*='fnSaveDocs']"},
                    {"action_type": "snapshot", "locator": "__page__"},
                ],
                expected={"type": "popup_or_navigation", "value": "file save settings"},
            ),
            _starter_case(
                case_id="gazette_business_link_entry_semi_auto",
                title="業務連携入口を半自動で確認する",
                automation_mode="semi-auto",
                steps=[
                    {"action_type": "click", "locator": "a[onclick*='fnBizLink']"},
                    {"action_type": "snapshot", "locator": "__page__"},
                ],
                expected={"type": "popup_or_navigation", "value": "business link entry"},
            ),
            _starter_case(
                case_id="gazette_negative_pdf_http_500",
                title="紙公報PDF取得時の HTTP 500 表示・証跡を確認する",
                risk_level="negative",
                automation_mode="auto-negative",
                case_type="negative_http_500",
                action_type="negative_http_500",
                value="**/*PDF*",
                generated_by="negative_http_500",
                steps=[
                    {
                        "action_type": "negative_http_500",
                        "locator": "a[onclick*='fnViewPDF']",
                        "value": "**/*PDF*",
                    }
                ],
                expected={"type": "http_error", "value": "**/*PDF*"},
            ),
            _starter_case(
                case_id="gazette_negative_pdf_network_abort",
                title="紙公報PDF取得時の network abort 表示・証跡を確認する",
                risk_level="negative",
                automation_mode="auto-negative",
                case_type="negative_network_abort",
                action_type="negative_network_abort",
                value="**/*PDF*",
                generated_by="negative_network_abort",
                steps=[
                    {
                        "action_type": "negative_network_abort",
                        "locator": "a[onclick*='fnViewPDF']",
                        "value": "**/*PDF*",
                    }
                ],
                expected={"type": "network_abort", "value": "**/*PDF*"},
            ),
            _starter_case(
                case_id="gazette_negative_highlight_js_error",
                title="ハイライト操作時の JavaScript error 証跡を確認する",
                risk_level="negative",
                automation_mode="auto-negative",
                case_type="negative_js_error",
                action_type="negative_js_error",
                generated_by="negative_js_error",
                steps=[
                    {
                        "action_type": "negative_js_error",
                        "locator": "#highlightId, a[onclick*='highlightOpen']",
                    }
                ],
                expected={"type": "console_error", "value": "MOONLIGHT_NEGATIVE"},
            ),
            _starter_case(
                case_id="gazette_detail_snapshot",
                title="公報明細の表示証跡を保存する",
                steps=[{"action_type": "snapshot", "locator": "__page__"}],
                expected={"type": "screenshot", "value": "gazette detail captured"},
            ),
        ]
    elif template_id == "person_name_dictionary_assist":
        cases = [
            _starter_case(
                case_id="person_aid_popup_frames_visible",
                title="Person-name assist popup frames are visible",
                steps=[
                    {"action_type": "assert_visible", "locator": "a[href*='help.html'][target='winHelp']"},
                    {"action_type": "assert_visible", "locator": "form[name='WwPersonalNameDicSearchForm']"},
                    {"action_type": "assert_visible", "locator": "#rTable, #tableBody"},
                ],
                expected={"type": "visible", "value": "title/input/result frames"},
            ),
            _starter_case(
                case_id="person_aid_search_form_controls_visible",
                title="Keyword search controls and match type radios are visible",
                steps=[
                    {"action_type": "assert_visible", "locator": "input[name='keyword'][type='text']"},
                    {"action_type": "assert_visible", "locator": "input[name='searchType'][value='3']"},
                    {"action_type": "assert_visible", "locator": "input[name='searchType'][value='1']"},
                    {"action_type": "assert_visible", "locator": "input[name='searchType'][value='2']"},
                    {"action_type": "assert_visible", "locator": "input[name='searchType'][value='0']"},
                    {"action_type": "assert_visible", "locator": "form[name='WwPersonalNameDicSearchForm'] input[type='submit']"},
                    {"action_type": "assert_visible", "locator": "input[onclick*='dispAllClear']"},
                ],
                expected={"type": "visible", "value": "keyword/search type controls"},
            ),
            _starter_case(
                case_id="person_aid_result_panel_initial_controls_visible",
                title="Result panel confirm/cancel and AND/OR/NOT controls are visible",
                steps=[
                    {"action_type": "assert_text", "locator": "#rTable, #tableBody", "value": "\u51fa\u9858\u4eba"},
                    {"action_type": "assert_visible", "locator": "input[name='rbExpand'][value='*']"},
                    {"action_type": "assert_visible", "locator": "input[name='rbExpand'][value='+']"},
                    {"action_type": "assert_visible", "locator": "input[name='rbExpand'][value='#']"},
                    {"action_type": "assert_visible", "locator": "input[onclick*='setCondTech']"},
                    {"action_type": "assert_visible", "locator": "input[onclick*='window.close']"},
                ],
                expected={"type": "visible", "value": "result operation controls"},
            ),
            _starter_case(
                case_id="person_aid_middle_match_search_results",
                title="Middle-match keyword search refreshes the result frame",
                steps=[
                    {"action_type": "fill", "locator": "input[name='keyword'][type='text']", "value": "11"},
                    {"action_type": "check", "locator": "input[name='searchType'][value='3']"},
                    {"action_type": "click", "locator": "form[name='WwPersonalNameDicSearchForm'] input[type='submit']"},
                    {"action_type": "assert_text", "locator": "#tableBody", "value": "11 HEALTH"},
                ],
                expected={"type": "result_list", "value": "11 HEALTH"},
            ),
            _starter_case(
                case_id="person_aid_prefix_match_search_results",
                title="Prefix-match keyword search can be executed",
                automation_mode="semi-auto",
                steps=[
                    {"action_type": "fill", "locator": "input[name='keyword'][type='text']", "value": "11"},
                    {"action_type": "check", "locator": "input[name='searchType'][value='1']"},
                    {"action_type": "click", "locator": "form[name='WwPersonalNameDicSearchForm'] input[type='submit']"},
                    {"action_type": "assert_attached", "locator": "#tableBody"},
                    {"action_type": "snapshot", "locator": "__page__"},
                ],
                expected={"type": "result_list", "value": "prefix match result frame"},
            ),
            _starter_case(
                case_id="person_aid_suffix_match_search_results",
                title="Suffix-match keyword search can be executed",
                automation_mode="semi-auto",
                steps=[
                    {"action_type": "fill", "locator": "input[name='keyword'][type='text']", "value": "INC"},
                    {"action_type": "check", "locator": "input[name='searchType'][value='2']"},
                    {"action_type": "click", "locator": "form[name='WwPersonalNameDicSearchForm'] input[type='submit']"},
                    {"action_type": "assert_attached", "locator": "#tableBody"},
                    {"action_type": "snapshot", "locator": "__page__"},
                ],
                expected={"type": "result_list", "value": "suffix match result frame"},
            ),
            _starter_case(
                case_id="person_aid_exact_match_search_results",
                title="Exact-match keyword search can be executed",
                automation_mode="semi-auto",
                steps=[
                    {"action_type": "fill", "locator": "input[name='keyword'][type='text']", "value": "11 HEALTH AND TECH INC"},
                    {"action_type": "check", "locator": "input[name='searchType'][value='0']"},
                    {"action_type": "click", "locator": "form[name='WwPersonalNameDicSearchForm'] input[type='submit']"},
                    {"action_type": "assert_attached", "locator": "#tableBody"},
                    {"action_type": "snapshot", "locator": "__page__"},
                ],
                expected={"type": "result_list", "value": "exact match result frame"},
            ),
            _starter_case(
                case_id="person_aid_select_result_with_or_expand",
                title="Selected result can be confirmed with default OR expand",
                automation_mode="semi-auto",
                steps=[
                    {"action_type": "fill", "locator": "input[name='keyword'][type='text']", "value": "11"},
                    {"action_type": "check", "locator": "input[name='searchType'][value='3']"},
                    {"action_type": "click", "locator": "form[name='WwPersonalNameDicSearchForm'] input[type='submit']"},
                    {"action_type": "check", "locator": "input[name='chkBox'][value='11 HEALTH AND TECH INC']"},
                    {"action_type": "check", "locator": "input[name='rbExpand'][value='+']"},
                    {"action_type": "click", "locator": "input[onclick*='setCondTech']"},
                ],
                expected={"type": "popup_close_or_parent_reflect", "value": "i0120 contains selected person name"},
            ),
            _starter_case(
                case_id="person_aid_select_result_with_and_expand",
                title="Selected result can use AND expand before confirmation",
                automation_mode="semi-auto",
                steps=[
                    {"action_type": "fill", "locator": "input[name='keyword'][type='text']", "value": "11"},
                    {"action_type": "click", "locator": "form[name='WwPersonalNameDicSearchForm'] input[type='submit']"},
                    {"action_type": "check", "locator": "input[name='chkBox'][value='11 HEALTH AND TECH INC']"},
                    {"action_type": "check", "locator": "input[name='rbExpand'][value='*']"},
                    {"action_type": "click", "locator": "input[onclick*='setCondTech']"},
                ],
                expected={"type": "popup_close_or_parent_reflect", "value": "AND expand is applied"},
            ),
            _starter_case(
                case_id="person_aid_select_result_with_not_expand",
                title="Selected result can use NOT expand before confirmation",
                automation_mode="semi-auto",
                steps=[
                    {"action_type": "fill", "locator": "input[name='keyword'][type='text']", "value": "11"},
                    {"action_type": "click", "locator": "form[name='WwPersonalNameDicSearchForm'] input[type='submit']"},
                    {"action_type": "check", "locator": "input[name='chkBox'][value='11 HEALTH AND TECH INC']"},
                    {"action_type": "check", "locator": "input[name='rbExpand'][value='#']"},
                    {"action_type": "click", "locator": "input[onclick*='setCondTech']"},
                ],
                expected={"type": "popup_close_or_parent_reflect", "value": "NOT expand is applied"},
            ),
            _starter_case(
                case_id="person_aid_cancel_closes_popup",
                title="Cancel closes the assist popup",
                automation_mode="semi-auto",
                steps=[
                    {"action_type": "click", "locator": "input[onclick*='window.close']"},
                ],
                expected={"type": "popup_close", "value": "assist popup closes without parent update"},
            ),
            _starter_case(
                case_id="person_aid_parent_open_and_reflect_flow_manual",
                title="Parent easy-search flow opens person dictionary and reflects selected applicant",
                automation_mode="manual",
                steps=[
                    {"action_type": "check", "locator": "input[name='itemId'][value='0120']"},
                    {"action_type": "click", "locator": "input[onclick*=\"showAidMenu('m0120')\"]"},
                    {"action_type": "click", "locator": "a[onclick*='WwPersonalNameDicDispForEasySearch.do'][onclick*='i0120']"},
                    {"action_type": "fill", "locator": "input[name='keyword'][type='text']", "value": "11"},
                    {"action_type": "click", "locator": "form[name='WwPersonalNameDicSearchForm'] input[type='submit']"},
                    {"action_type": "check", "locator": "input[name='chkBox'][value='11 HEALTH AND TECH INC']"},
                    {"action_type": "click", "locator": "input[onclick*='setCondTech']"},
                    {"action_type": "assert_value", "locator": "input[name='i0120']", "value": "11"},
                ],
                expected={"type": "parent_field_value", "value": "i0120 includes 11 HEALTH AND TECH INC"},
            ),
            _starter_case(
                case_id="person_aid_negative_search_http_500",
                title="Search request HTTP 500 evidence is captured",
                risk_level="negative",
                automation_mode="auto-negative",
                case_type="negative_http_500",
                action_type="negative_http_500",
                value="**/WwPersonalNameDicSearch.do*",
                generated_by="negative_http_500",
                steps=[
                    {"action_type": "fill", "locator": "input[name='keyword'][type='text']", "value": "11"},
                    {
                        "action_type": "negative_http_500",
                        "locator": "form[name='WwPersonalNameDicSearchForm'] input[type='submit']",
                        "value": "**/WwPersonalNameDicSearch.do*",
                    },
                ],
                expected={"type": "http_error", "value": "**/WwPersonalNameDicSearch.do*"},
            ),
            _starter_case(
                case_id="person_aid_negative_search_network_abort",
                title="Search request network abort evidence is captured",
                risk_level="negative",
                automation_mode="auto-negative",
                case_type="negative_network_abort",
                action_type="negative_network_abort",
                value="**/WwPersonalNameDicSearch.do*",
                generated_by="negative_network_abort",
                steps=[
                    {"action_type": "fill", "locator": "input[name='keyword'][type='text']", "value": "11"},
                    {
                        "action_type": "negative_network_abort",
                        "locator": "form[name='WwPersonalNameDicSearchForm'] input[type='submit']",
                        "value": "**/WwPersonalNameDicSearch.do*",
                    },
                ],
                expected={"type": "network_abort", "value": "**/WwPersonalNameDicSearch.do*"},
            ),
            _starter_case(
                case_id="person_aid_negative_confirm_js_error",
                title="Confirm action JavaScript error evidence is captured",
                risk_level="negative",
                automation_mode="auto-negative",
                case_type="negative_js_error",
                action_type="negative_js_error",
                generated_by="negative_js_error",
                steps=[
                    {"action_type": "fill", "locator": "input[name='keyword'][type='text']", "value": "11"},
                    {"action_type": "click", "locator": "form[name='WwPersonalNameDicSearchForm'] input[type='submit']"},
                    {"action_type": "negative_js_error", "locator": "input[onclick*='setCondTech']"},
                ],
                expected={"type": "console_error", "value": "MOONLIGHT_NEGATIVE"},
            ),
            _starter_case(
                case_id="person_aid_popup_snapshot",
                title="Person-name assist popup visual evidence is captured",
                steps=[{"action_type": "snapshot", "locator": "__page__"}],
                expected={"type": "screenshot", "value": "person name assist captured"},
            ),
        ]
    elif template_id == "screening_workbench":
        cases = [
            _starter_case(
                case_id="screening_workspace_initial_display",
                title="スクリーニング工作台の主要操作が表示される",
                steps=[
                    {
                        "action_type": "assert_visible",
                        "locator": "*[id='btBunkenIchiran'], input[value*='文献一覧'], button:has-text('文献一覧')",
                    },
                    {
                        "action_type": "assert_visible",
                        "locator": "*[id='btClose'], input[value='閉じる'], button:has-text('閉じる')",
                    },
                ],
                expected={"type": "visible", "value": "document list and close controls"},
            ),
            _starter_case(
                case_id="screening_review_controls_visible",
                title="スクリーニングの確認・強調表示入口が表示される",
                steps=[
                    {
                        "action_type": "assert_visible",
                        "locator": "*[id='btHighlight'], input[value*='ハイライト'], button:has-text('ハイライト')",
                    },
                    {
                        "action_type": "assert_visible",
                        "locator": "*[id='btSave'], input[value*='保存'], button:has-text('保存')",
                    },
                ],
                expected={"type": "visible", "value": "highlight/save controls"},
            ),
            _starter_case(
                case_id="screening_workspace_snapshot",
                title="スクリーニング工作台の表示証跡を保存する",
                steps=[{"action_type": "snapshot", "locator": "__page__"}],
                expected={"type": "screenshot", "value": "screening workbench captured"},
            ),
        ]
    else:
        cases = [
            _starter_case(
                case_id="guided_initial_display",
                title="画面の初期表示を確認する",
                steps=[
                    {"action_type": "assert_visible", "locator": "__page__"},
                    {"action_type": "snapshot", "locator": "__page__"},
                ],
            )
        ]

    return {
        "schema": GUIDED_CHECKLIST_SCHEMA,
        "page_id": target_page,
        "template_id": template_id,
        "title": str(meta.get("label") or target_page),
        "route_policy": {
            "direct_url_allowed": bool(meta.get("direct_url_allowed", False)),
            "note": "通常はユーザーが業務経路で対象画面まで到達してから、この checklist を実行します。",
        },
        "notes": [
            "Web model should expand this starter with observed page states, safe assertions, prerequisites, and semi-auto/manual cases.",
            "Keep destructive save/delete/print cases as semi-auto or manual unless the user explicitly approves them.",
        ],
        "cases": cases,
    }


def write_starter_guided_checklist(path: Any, page_id: Any, *, overwrite: bool = False) -> Path:
    output = Path(path)
    if output.exists() and not overwrite:
        raise FileExistsError(str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(starter_guided_checklist(page_id), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output


def html_report_path(browser_name: str, page_id: str) -> Path:
    return Path("output/gui") / browser_key(browser_name) / safe_page_key(page_id) / "gui_report.html"


def regression_output_dir(browser_name: str) -> Path:
    return Path("output/regression") / browser_key(browser_name)


def upload_profile_config_path(browser_name: str, page_id: str) -> Path:
    return Path("generated/gui/upload_profiles") / browser_key(browser_name) / f"{safe_page_key(page_id)}.json"


def build_regression_command(config: Dict[str, Any], *, pytest_cmd: str) -> str:
    browser = str(config.get("browser") or "chrome_port")
    page_id = str(config.get("target_page") or "").strip()
    if not page_id:
        raise ValueError("target_page is required")

    cmd = f"{pytest_cmd} tests/test_migration.py --run-migration --test-browser={browser}"
    cmd += f" --target-page={quote(page_id)}"
    cmd += f" --regression-output-dir={quote(config.get('regression_output_dir') or regression_output_dir(browser))}"

    login_entry = str(config.get("login_entry") or "").strip()
    if login_entry:
        cmd += f" --login-entry={quote(login_entry)}"

    checklist_path = str(config.get("checklist_path") or "").strip()
    if checklist_path:
        cmd += f" --checklist-path={quote(checklist_path)}"

    route_map_path = str(config.get("route_map_path") or "").strip()
    if config.get("force_route_map"):
        cmd += " --force-route-map"
        if route_map_path:
            cmd += f" --route-map-path={quote(route_map_path)}"

    if config.get("manual"):
        cmd += " --manual"
    if config.get("risk_only"):
        cmd += " --risk-only"
    if config.get("include_semi_auto"):
        cmd += " --include-semi-auto"
    if config.get("include_destructive"):
        cmd += " --include-destructive"
    if config.get("include_negative"):
        cmd += " --include-negative"
        negative_profile = str(config.get("negative_profile") or "").strip()
        if negative_profile:
            cmd += f" --negative-profile={quote(negative_profile)}"

    upload_file = str(config.get("upload_file") or "").strip()
    if upload_file:
        cmd += f" --upload-file={quote(upload_file)}"

    upload_profile_config = str(config.get("upload_profile_config") or "").strip()
    if upload_profile_config:
        cmd += f" --upload-profile-config={quote(upload_profile_config)}"

    html_path = config.get("html_path") or html_report_path(browser, page_id)
    cmd += f" --html={quote(html_path)}"
    return cmd


def run_regression_queue(configs: Iterable[Dict[str, Any]], run_page: Callable[[Dict[str, Any]], Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for config in configs:
        try:
            result = dict(run_page(config) or {})
        except Exception as exc:
            result = {
                "return_code": None,
                "error": str(exc),
            }
        result.setdefault("target_page", config.get("target_page"))
        results.append(result)
    return results


def create_regression_queue_run(configs: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    queued = [dict(config) for config in configs]
    return {
        "status": "running" if queued else "complete",
        "next_index": 0,
        "configs": queued,
        "results": [],
    }


def current_regression_queue_config(queue_run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    configs = list(queue_run.get("configs") or [])
    next_index = int(queue_run.get("next_index", 0) or 0)
    if str(queue_run.get("status") or "") != "running" or next_index >= len(configs):
        return None
    return dict(configs[next_index])


def record_regression_queue_result(queue_run: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(queue_run)
    configs = list(updated.get("configs") or [])
    results = list(updated.get("results") or [])
    next_index = int(updated.get("next_index", 0) or 0)
    current = configs[next_index] if next_index < len(configs) else {}
    recorded = dict(result or {})
    recorded.setdefault("target_page", current.get("target_page"))
    results.append(recorded)
    next_index += 1
    updated["results"] = results
    updated["next_index"] = next_index
    updated["status"] = "complete" if next_index >= len(configs) else "running"
    return updated


def bounded_console_output(value: Any, *, max_chars: int = 40000) -> str:
    text = str(value or "")
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"[... {omitted} earlier character(s) omitted from live view ...]\n{text[-max_chars:]}"


def _page_name(value: Any) -> str:
    return Path(str(value or "").replace("\\", "/")).name


def _add_option(options: Dict[str, Dict[str, Any]], page_id: Any, **meta: Any) -> None:
    page = _page_name(page_id)
    if not page:
        return
    key = page.lower()
    existing = options.setdefault(key, {"page_id": page, "sources": []})
    for field in ("entry_url", "action", "risk", "route_map_path"):
        if meta.get(field) and not existing.get(field):
            existing[field] = meta[field]
    source = meta.get("source")
    if source and source not in existing["sources"]:
        existing["sources"].append(source)


def load_page_options(
    *,
    mapping_path: Path = Path("generated/valid/page_mapping.json"),
    route_dir: Path = Path("generated/valid/route"),
    report_dir: Path = Path("output/regression"),
) -> List[Dict[str, Any]]:
    options: Dict[str, Dict[str, Any]] = {}

    for target in GUIDED_CHECKLIST_TARGETS:
        _add_option(
            options,
            target["page_id"],
            action=target.get("template_id"),
            risk="guided",
            source="guided",
        )
        _add_option(
            options,
            target["actual_page_id"],
            action=target.get("page_id"),
            risk="guided",
            source="guided_actual",
        )

    if mapping_path.exists():
        try:
            payload = json.loads(mapping_path.read_text(encoding="utf-8"))
            for item in payload.get("page_mappings") or []:
                _add_option(
                    options,
                    item.get("page_id") or item.get("target_page"),
                    entry_url=item.get("entry_url") or item.get("resolved_entry_url"),
                    action=item.get("entry_action") or item.get("action") or item.get("action_path"),
                    risk=item.get("risk"),
                    source="mapping",
                )
        except Exception:
            pass

    if route_dir.exists():
        for path in route_dir.rglob("usable_route_map*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for route in list(payload.get("verified") or []) + list(payload.get("manual_verified") or []):
                source_route = route.get("source_route") or {}
                target = route.get("target_page") or route.get("target_page_name") or source_route.get("target_page") or source_route.get("target_page_name")
                _add_option(options, target, route_map_path=str(path), source="route")

    if report_dir.exists():
        for report in report_dir.rglob("regression_report.html"):
            parent = report.parent.name
            page = re.sub(r"^\d+_", "", parent)
            _add_option(options, page, source="recent")

    return sorted(options.values(), key=lambda item: item["page_id"].lower())


def page_option_labels(options: Iterable[Dict[str, Any]]) -> List[str]:
    labels = []
    for option in options:
        details = []
        if option.get("entry_url"):
            details.append(f"entry: {option['entry_url']}")
        if option.get("action"):
            details.append(f"action: {option['action']}")
        if option.get("risk"):
            details.append(f"risk: {option['risk']}")
        if option.get("route_map_path"):
            details.append("route: yes")
        suffix = f"    {' / '.join(details)}" if details else ""
        labels.append(f"{option['page_id']}{suffix}")
    return labels


def _column_index(headers: List[str], *names: str) -> Optional[int]:
    wanted = {name.strip().lower() for name in names if str(name or "").strip()}
    for index, header in enumerate(headers):
        if header.strip().lower() in wanted:
            return index
    return None


def _row_value(row: Iterable[Any], index: Optional[int]) -> str:
    values = list(row)
    if index is None or index >= len(values):
        return ""
    value = values[index]
    return "" if value is None else str(value).strip()


def _is_upload_case(
    *,
    case_type: Any,
    action_type: Any,
    title: Any,
    submit_locator: Any,
    main_step: Any,
) -> bool:
    case_lower = str(case_type or "").lower()
    action_lower = str(action_type or "").lower()
    title_text = str(title or "")
    main_step_lower = str(main_step or "").lower()
    if case_lower == "upload_submit" or action_lower == "upload_submit":
        return True
    if "アップロード確認" in title_text:
        return True
    if submit_locator:
        return True
    return "submit" in main_step_lower or "click" in main_step_lower


def _guided_json_cases(checklist: Path, page_id: Any) -> List[Dict[str, Any]]:
    try:
        payload = json.loads(checklist.read_text(encoding="utf-8"))
    except Exception:
        return []

    if isinstance(payload, list):
        raw_cases = payload
        default_page = page_id
    elif isinstance(payload, dict):
        raw_cases = (
            payload.get("cases")
            or payload.get("checklist_cases")
            or payload.get("checklist")
            or payload.get("items")
            or []
        )
        default_page = payload.get("page_id") or payload.get("target_page") or page_id
    else:
        return []

    if not isinstance(raw_cases, list):
        return []

    cases: List[Dict[str, Any]] = []
    for item in raw_cases:
        if not isinstance(item, dict):
            continue
        row_page = item.get("page_id") or item.get("page") or default_page
        if page_matches_target(row_page, page_id):
            cases.append(item)
    return cases


def _guided_case_steps(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    for key in ("pre_steps", "steps"):
        value = item.get(key)
        if isinstance(value, list):
            steps.extend(dict(step) for step in value if isinstance(step, dict))
    main_step = item.get("main_step")
    if isinstance(main_step, dict):
        steps.append(dict(main_step))
    if not steps:
        steps.append(
            {
                "action_type": item.get("action_type") or item.get("case_type") or "",
                "locator": item.get("locator") or item.get("legacy_locator") or item.get("new_locator") or "",
                "submit_locator": item.get("submit_locator") or "",
            }
        )
    return steps


def _guided_upload_options(checklist: Path, page_id: Any) -> List[Dict[str, str]]:
    options: List[Dict[str, str]] = []
    seen = set()
    for index, item in enumerate(_guided_json_cases(checklist, page_id), start=1):
        steps = _guided_case_steps(item)
        case_type = str(item.get("case_type") or item.get("action_type") or "").strip()
        action_type = str(item.get("action_type") or case_type).strip()
        title = str(item.get("title") or item.get("test_title") or "")
        submit_locator = str(item.get("submit_locator") or "").strip()
        main_step_text = json.dumps(steps[-1], ensure_ascii=False) if steps else ""
        if not _is_upload_case(
            case_type=case_type,
            action_type=action_type,
            title=title,
            submit_locator=submit_locator,
            main_step=main_step_text,
        ) and not any(str(step.get("action_type") or "").lower() in {"upload", "file", "upload_submit"} for step in steps):
            continue

        upload_step = next(
            (
                step
                for step in steps
                if str(step.get("action_type") or "").lower() in {"upload", "file", "upload_submit"}
                or "file" in str(step.get("locator") or "").lower()
            ),
            {},
        )
        submit_step = next(
            (
                step
                for step in reversed(steps)
                if str(step.get("submit_locator") or step.get("locator") or "").strip()
                and step is not upload_step
            ),
            {},
        )
        case_id = str(item.get("case_id") or item.get("id") or f"guided_upload_{index}").strip()
        key = case_id.lower()
        if key in seen:
            continue
        seen.add(key)
        options.append(
            {
                "case_id": case_id,
                "test_title": title,
                "automation_mode": str(item.get("automation_mode") or ""),
                "case_type": case_type or action_type or "upload_submit",
                "action_type": action_type or case_type or "upload_submit",
                "locator": str(upload_step.get("locator") or item.get("locator") or ""),
                "submit_locator": str(
                    submit_locator
                    or submit_step.get("submit_locator")
                    or submit_step.get("locator")
                    or ""
                ),
                "destructive": str(item.get("destructive") or "false"),
                "enabled": str(item.get("enabled") if "enabled" in item else "true"),
            }
        )
    return options


def load_upload_case_options(checklist_path: Any, page_id: Any) -> List[Dict[str, str]]:
    checklist = Path(str(checklist_path or ""))
    target = target_page_name(page_id)
    if not target or not checklist.exists():
        return []

    if checklist.suffix.lower() == ".json":
        return _guided_upload_options(checklist, page_id)

    try:
        from openpyxl import load_workbook
    except Exception:
        return []

    try:
        workbook = load_workbook(checklist, data_only=True, read_only=True)
        sheet = workbook["Checklist"] if "Checklist" in workbook.sheetnames else workbook[workbook.sheetnames[0]]
        rows = list(sheet.iter_rows(values_only=True))
    except Exception:
        return []

    if not rows:
        return []

    headers = [str(value or "").strip().lower() for value in rows[0]]
    page_col = _column_index(headers, "page_id", "page", "jsp")
    case_id_col = _column_index(headers, "case_id", "id", "no")
    title_col = _column_index(headers, "test_title", "title", "test_viewpoint")
    mode_col = _column_index(headers, "automation_mode", "mode")
    case_type_col = _column_index(headers, "case_type")
    action_type_col = _column_index(headers, "action_type")
    locator_col = _column_index(headers, "locator")
    submit_locator_col = _column_index(headers, "submit_locator")
    main_step_col = _column_index(headers, "main_step")
    destructive_col = _column_index(headers, "destructive")
    enabled_col = _column_index(headers, "enabled", "enable")

    options: List[Dict[str, str]] = []
    seen = set()
    for row in rows[1:]:
        row_page = _row_value(row, page_col)
        if not page_matches_target(row_page, page_id):
            continue

        case_id = _row_value(row, case_id_col)
        title = _row_value(row, title_col)
        case_type = _row_value(row, case_type_col)
        action_type = _row_value(row, action_type_col)
        submit_locator = _row_value(row, submit_locator_col)
        main_step = _row_value(row, main_step_col)
        locator = _row_value(row, locator_col)
        if not _is_upload_case(
            case_type=case_type,
            action_type=action_type,
            title=title,
            submit_locator=submit_locator,
            main_step=main_step,
        ):
            continue

        option_id = case_id or title or f"upload_case_{len(options) + 1}"
        key = option_id.lower()
        if key in seen:
            continue
        seen.add(key)
        options.append(
            {
                "case_id": option_id,
                "test_title": title,
                "automation_mode": _row_value(row, mode_col),
                "case_type": case_type or action_type or "upload_submit",
                "action_type": action_type or case_type or "upload_submit",
                "locator": locator,
                "submit_locator": submit_locator,
                "destructive": _row_value(row, destructive_col) or "false",
                "enabled": _row_value(row, enabled_col) or "true",
            }
        )
    return options


def upload_case_option_labels(cases: Iterable[Dict[str, Any]]) -> List[str]:
    labels = []
    for case in cases:
        details = []
        if case.get("test_title"):
            details.append(str(case["test_title"]))
        if case.get("automation_mode"):
            details.append(f"mode: {case['automation_mode']}")
        if case.get("locator"):
            details.append(f"locator: {case['locator']}")
        suffix = f"    {' / '.join(details)}" if details else ""
        labels.append(f"{case.get('case_id') or 'upload_case'}{suffix}")
    return labels


def _is_negative_case_type(case_type: Any, action_type: Any) -> bool:
    text = " ".join(str(value or "").lower() for value in (case_type, action_type))
    return "negative" in text or text.startswith("error_")


def _guided_negative_profile_options(checklist: Path, page_id: Any) -> List[Dict[str, str]]:
    defaults = {item["profile"]: item for item in DEFAULT_NEGATIVE_PROFILES}
    options: Dict[str, Dict[str, str]] = {}
    for item in _guided_json_cases(checklist, page_id):
        case_type = str(item.get("case_type") or "").strip()
        action_type = str(item.get("action_type") or "").strip()
        if not _is_negative_case_type(case_type, action_type):
            continue
        profile = case_type or action_type
        default = defaults.get(profile, {})
        options[profile] = {
            "profile": profile,
            "label": default.get("label") or profile,
            "description": str(item.get("title") or item.get("test_title") or default.get("description") or ""),
        }
    if not options:
        return DEFAULT_NEGATIVE_PROFILES
    for default in DEFAULT_NEGATIVE_PROFILES:
        options.setdefault(default["profile"], dict(default))
    return list(options.values())


def load_negative_profile_options(checklist_path: Any, page_id: Any) -> List[Dict[str, str]]:
    checklist = Path(str(checklist_path or ""))
    target = target_page_name(page_id)
    if not target or not checklist.exists():
        return DEFAULT_NEGATIVE_PROFILES

    if checklist.suffix.lower() == ".json":
        return _guided_negative_profile_options(checklist, page_id)

    try:
        from openpyxl import load_workbook
    except Exception:
        return DEFAULT_NEGATIVE_PROFILES

    try:
        workbook = load_workbook(checklist, data_only=True, read_only=True)
        sheet = workbook["Checklist"] if "Checklist" in workbook.sheetnames else workbook[workbook.sheetnames[0]]
        rows = list(sheet.iter_rows(values_only=True))
    except Exception:
        return DEFAULT_NEGATIVE_PROFILES

    if not rows:
        return DEFAULT_NEGATIVE_PROFILES

    headers = [str(value or "").strip().lower() for value in rows[0]]
    page_col = _column_index(headers, "page_id", "page", "jsp")
    case_type_col = _column_index(headers, "case_type")
    action_type_col = _column_index(headers, "action_type")
    title_col = _column_index(headers, "test_title", "title", "test_viewpoint")
    mode_col = _column_index(headers, "automation_mode", "mode")

    defaults = {item["profile"]: item for item in DEFAULT_NEGATIVE_PROFILES}
    options: Dict[str, Dict[str, str]] = {}
    for row in rows[1:]:
        row_page = _row_value(row, page_col)
        if not page_matches_target(row_page, page_id):
            continue
        case_type = _row_value(row, case_type_col)
        action_type = _row_value(row, action_type_col)
        if not _is_negative_case_type(case_type, action_type):
            continue
        profile = (case_type or action_type).strip()
        if not profile:
            continue
        default = defaults.get(profile, {})
        options[profile] = {
            "profile": profile,
            "label": default.get("label") or profile,
            "description": _row_value(row, title_col) or default.get("description") or _row_value(row, mode_col),
        }

    if not options:
        return DEFAULT_NEGATIVE_PROFILES
    for default in DEFAULT_NEGATIVE_PROFILES:
        options.setdefault(default["profile"], dict(default))
    return list(options.values())


def negative_profile_labels(options: Iterable[Dict[str, Any]]) -> List[str]:
    labels = []
    for option in options:
        label = option.get("label") or option.get("profile") or "negative"
        description = option.get("description")
        suffix = f"    {description}" if description else ""
        labels.append(f"{option.get('profile') or label}{suffix}")
    return labels
