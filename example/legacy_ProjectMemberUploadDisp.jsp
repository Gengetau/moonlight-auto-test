<!DOCTYPE html>
<%@ page contentType="text/html; charset=Windows-31J" pageEncoding="UTF-8"%>
<%response.setContentType("text/html; charset=" + session.getServletContext().getAttribute("PATENT_ENCODING"));%>
<%@ taglib uri="/WEB-INF/struts-html.tld" prefix="html" %>
<%@ taglib uri="/WEB-INF/struts-bean.tld" prefix="bean" %>
<%@ taglib uri="/WEB-INF/struts-logic.tld" prefix="logic" %>
<%@ taglib uri="/WEB-INF/patlics.tld" prefix="patlics" %>
<%@ taglib uri="/WEB-INF/patlics-layout.tld" prefix="layout" %>
<%@page import="jp.panasonic.patlics.patweb.util.vo.LicenceInfo"%>
<%
String enl = LicenceInfo.getLanguage();
%>
<html lang="ja">
<head>
<jsp:include page="./CommonHeader.jsp" flush="true" />
<meta http-equiv="Content-Type" content="text/html; charset=<%=session.getServletContext().getAttribute("PATENT_ENCODING")%>" />

<title><bean:message key="label.patlics.all453" bundle="PATLICS_MESSAGE" /></title>
<script>
<!--
// アプリケーションURL
var APP_URL = "<bean:message bundle='APP' key='env.appURL' />";

function fnSubmit(url){
	form = document.forms[0];
	form.action=APP_URL+url;
	form.target="WinUserUpload";
	form.submit();
}

//-->
</script>
<!--[if gte IE 7]><link rel="stylesheet" href="css/default_ie7.css" media="screen,print" type="text/css"><![endif]-->
<link rel="stylesheet" href="./css/default.css" media="screen,print" type="text/css">
<link rel="stylesheet" href="css/ProjectMemberUploadDisp.css" type="text/css">
</head>

<body style="margin:10px;">
<html:form action="/ProjectMemberUpload" target="frMain" enctype="multipart/form-data" onsubmit="return false;">
<input type="hidden" name="userId" value="<bean:write name='E_USER'/>"> 
<input type="hidden" name="userLevel" value="<bean:write name='USER_LEVEL'/>">
<input type="hidden" name="projectId" value="<bean:write name='ProjectMemberUploadForm' property="projectId" />">
<input type="hidden" name="userName" value="<bean:write name='ProjectMemberUploadForm' property='userName' />">
<input type="hidden" name="u_lv" value="<bean:write name='ProjectMemberUploadForm' property='u_lv' />">
<input type="hidden" name="projectName" value="<bean:write name='ProjectMemberUploadForm' property="projectName" />">
<div style="padding:10px;" class="align_center"></div>
<div class="center">
<table border="0" class="page_cellpadding_0px page_cellspacing_0px align_margin_auto" >
	<tr class="align_left">
		<td class="page_width_50px"><img src="image/icon_project.gif" width="45" height="45" alt=""></td>
		<td class="nowrap"><span class="t18_b"><b><bean:message key="label.patlics.all453" bundle="PATLICS_MESSAGE" /></b></span></td>
	</tr>
</table>
<table border="0" class="page_cellpadding_0px page_cellspacing_0px align_margin_auto">
    <tr>
    <%--  2018/02/14 PSTC ougikou UPDATE START(No.C1803_プロジェクト最大登録人数拡張) --%>
    <%--    <td align="center" style="padding:0px 0px 0px 0px;"><font class="t12"><bean:message key="label.patlics.all454" bundle="PATLICS_MESSAGE" /></font></td> --%>
		<td style="padding:0px 0px 0px 0px;" class="align_center"><span class="t12">
			<bean:message key="label_info_674" bundle="PATLICS_MESSAGE" />
			<bean:message bundle='APP' key='project.memberCnt.max' />
			<bean:message key="label_info_675" bundle="PATLICS_MESSAGE" />
		</span></td>
	<%--  2018/02/14 PSTC ougikou UPDATE END --%>
	</tr>
</table>
<br>
<table border="0" class="page_width_380px page_cellpadding_0px page_cellspacing_0px align_margin_auto">
	<tr>
		<td>
            <table border="0" class="page_cellpadding_0px page_cellspacing_1px width_100per bgcolor_b5b5b5 align_left">
				<tr class="bgcolor_88aaee">
				    <td style="border:1px solid #ffffff;" class="width_100per align_center nowrap">
						<span class="t12_w"><bean:message key="label.pms.summaryList.sort.prjName" bundle="PATLICS_MESSAGE" /></span>
					</td>
				</tr>
				<tr>
					<td style="border:1px solid #ffffff;" class="bgcolor_ffffff width_100per align_center nowrap">
						<span class="t12"><bean:write name='ProjectMemberUploadForm' property="projectName" /></span>
					</td>
				</tr>
			</table>
	    </td>
	    <td class="align_left">
	    <br><br><span class="t12"><bean:message key="label.patlics.all455" bundle="PATLICS_MESSAGE" /></span>
	    </td>
	</tr>
	<tr>
		<td colspan="2" class="align_left">
		    <table border="0" class="page_cellpadding_0px page_cellspacing_0px width_100per align_center">
				<tr >
				    <td class="width_100per align_center nowrap">
						<span class="t12"><a href="#" onclick="fnSubmit('/ProjectMemberUploadTemplateDownload.do');return false"><bean:message key="label.patlics.all456" bundle="PATLICS_MESSAGE" /></a></span>
					</td>
				</tr>
				<tr>
					<td class="width_100per align_center nowrap">
<% if(enl.equals("en")) { %>
						<span class="t12"><bean:message key="label.patlics.all457" bundle="PATLICS_MESSAGE" /></span><a href="/help_en/project_106_01.html" target="winHelp"><span class="t12"><bean:message bundle='PATLICS_MESSAGE' key='label.mapCitationHead.comment3'/></span></a>
<% } else { %>
						<span class="t12"><bean:message key="label.patlics.all457" bundle="PATLICS_MESSAGE" /></span><a href="/help/project_106_01.html" target="winHelp"><span class="t12"><bean:message bundle='PATLICS_MESSAGE' key='label.mapCitationHead.comment3'/></span></a>
<% } %>
					</td>
				</tr>
			</table>
		</td>
	</tr>
</table>
<br>
<table border="0" class="width_100per page_cellpadding_0px page_cellspacing_0px align_margin_auto">
	<tr>
		<td style="padding:0px 10px 10px 60px;" class="align_center">	
		    <table border="0" class="page_cellpadding_0px page_cellspacing_1px page_width_430px align_margin_auto bgcolor_b5b5b5">
				<tr class="bgcolor_b2b2cc">
					<td style="border:1px solid #ffffff;" class="width_30per align_center nowrap">
						<span class="t12_w"><bean:message bundle='PATLICS_MESSAGE' key='label.evalListUpload.file' /></span>
					</td>
					<td style="border:1px solid #ffffff;" class="bgcolor_ffffff width_70per align_center nowrap">
						<html:file name="ProjectMemberUploadForm" property="uploadFile" style="width:100%;"/>
					</td>
				</tr>
			</table>
		</td>
	</tr>
	<tr>
		<td style="padding-top:10px;" class="align_center">
		    <input type="button" name="entry" value="<bean:message key="btn.userListUpload.adminDivTool.fileUpload" bundle="PATLICS_MESSAGE" />" onClick="fnSubmit('/ProjectMemberUpload.do')" ></a>&nbsp;
            <input type="button" name="cancell" value="<bean:message key="btn.pms.ownPatentDelete.cancel" bundle="PATLICS_MESSAGE" />" onClick="javascript:window.close()">
		</td>
	</tr>
</table>
</div>
</html:form>
</body>
</html>
