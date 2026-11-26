# ruff: noqa: E501  动作名、栏目名是数据，不按代码行宽折行
"""What an audit record says was done, worked out in one place for every endpoint.

* The action: every write route (and every download) has a readable name in ``ROUTE_ACTIONS``,
  looked up by the route's template, so no record shows an address. A test walks all routes of the
  application and fails when one is missing.
* The content: one listener on the database session notes, during the request, which rows were
  added, changed or removed, and for a change which columns (and which keys of a filled-in answer
  set) went from what to what. ``record_request`` turns that into a summary such as
  “修改：合规评估项目《摩洛哥 → 中国》：保存期限 由“2 年”改为“3 年””.
* What the request acted on: the ids in the address are looked up to a name (project, form,
  document…), so downloads and deletions also say what they were about.

Passwords, tokens, keys and uploaded file contents are never written: such a value appears as
“已修改”. Values longer than ``MAX_VALUE`` characters are cut and say how long they were. Nothing
here may stop the user's action: every step swallows its own errors.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

MAX_VALUE = 200
MAX_OBJECTS = 20
MAX_FIELDS = 20

# ---------------------------------------------------------------- 动作名（按接口模板）

ROUTE_ACTIONS: dict[tuple[str, str], str] = {
    ("POST", "/api/auth/user/login"): "登录",
    ("POST", "/api/auth/admin/login"): "登录",
    ("POST", "/api/auth/user/register"): "注册体验账号",
    ("POST", "/api/auth/portal/exchange"): "门户单点登录",
    ("POST", "/api/auth/admin/exchange"): "管理端单点登录",
    ("PATCH", "/api/auth/account"): "修改个人账号信息",
    ("POST", "/api/auth/password"): "修改登录密码",
    ("POST", "/api/admin/orgs"): "创建企业",
    ("PATCH", "/api/admin/orgs/{org_id}"): "修改企业信息或启用状态",
    ("PATCH", "/api/admin/orgs/{org_id}/redaction"): "设置调用外部模型时隐去的词",
    ("POST", "/api/admin/users"): "创建账号",
    ("PATCH", "/api/admin/users/{user_id}"): "修改账号（角色、下载权限、启停）",
    ("POST", "/api/admin/documents/{document_id}/mark-new"): "标记为新法规",
    ("PUT", "/api/admission/projects/{project_id}/filing"): "记录准入申报状态",
    ("PUT", "/api/notifications/subscription"): "设置企业的法规订阅",
    ("POST", "/api/admin/sites"): "新增采集站点",
    ("PATCH", "/api/admin/sites/{site_id}"): "修改采集站点",
    ("DELETE", "/api/admin/sites/{site_id}"): "删除采集站点",
    ("POST", "/api/admin/sites/{site_id}/terms"): "确认来源使用条款",
    ("POST", "/api/admin/sites/{site_id}/run"): "立即抓取",
    ("POST", "/api/admin/documents/upload"): "上传官方原文",
    ("PATCH", "/api/admin/documents/{document_id}/relevance"): "修改法规相关性",
    ("POST", "/api/admin/versions/{version_id}/check"): "核对扫描件原文",
    ("POST", "/api/admin/versions/{version_id}/pages/{page}/reread"): "重新识别扫描件的一页",
    ("POST", "/api/admin/quality-cards/references/repair"): "修复指空的条文引用",
    ("POST", "/api/admin/versions/{version_id}/check-items/{clause_id}"): "核对扫描件的一段文字",
    ("POST", "/api/admin/knowledge/extract"): "抽取知识图谱",
    ("POST", "/api/admin/knowledge/glossary"): "新增法律术语",
    ("POST", "/api/admin/knowledge/glossary/{term_id}/review"): "审核法律术语",
    ("POST", "/api/admin/knowledge/clauses/{clause_id}/translate"): "翻译条文",
    ("POST", "/api/admin/knowledge/documents/{document_id}/translate"): "补全法规译文",
    ("POST", "/api/admin/knowledge/translations/{translation_id}/review"): "审核译文",
    ("POST", "/api/admin/knowledge/review-points"): "新增核对要点",
    ("POST", "/api/admin/knowledge/review-points/{point_id}/decide"): "处理核对要点",
    ("GET", "/api/admin/knowledge/review-points/export"): "导出核对要点",
    ("POST", "/api/admin/knowledge/review-points/import"): "导入核对结论",
    ("POST", "/api/admin/knowledge/edges/{edge_id}/review"): "核对图谱关系",
    ("POST", "/api/admin/knowledge/nodes/{node_id}/review"): "核对图谱节点",
    ("PATCH", "/api/admin/knowledge/nodes/{node_id}"): "修改图谱节点",
    ("DELETE", "/api/admin/knowledge/nodes/{node_id}"): "停用图谱节点",
    ("POST", "/api/admin/knowledge/nodes"): "新增图谱节点",
    ("POST", "/api/admin/knowledge/edges"): "新增图谱关系",
    ("PATCH", "/api/admin/knowledge/edges/{edge_id}"): "修改图谱关系",
    ("DELETE", "/api/admin/knowledge/edges/{edge_id}"): "停用图谱关系",
    ("POST", "/api/admin/knowledge/restore"): "恢复图谱记录",
    ("POST", "/api/admin/knowledge/batch-import"): "批量导入知识图谱节点与关系",
    ("GET", "/api/knowledge/whitepapers/{country_code}/export.pdf"): "下载国家简报（PDF）",
    ("GET", "/api/knowledge/whitepapers/{country_code}/export.docx"): "下载国家简报（Word）",
    ("POST", "/api/admin/knowledge/whitepapers/{country_code}/update"): "更新国家简报",
    ("POST", "/api/admin/knowledge/whitepapers/{country_code}/sync-dimensions"): "按受影响章节更新国家简报",
    ("PATCH", "/api/admin/knowledge/whitepapers/{country_code}/blocks"): "在线修订国家简报",
    ("POST", "/api/admin/knowledge/whitepapers/{country_code}/review"): "审核归档国家简报",
    ("POST", "/api/admin/knowledge/whitepapers/{country_code}/versions/{version_no}/rollback"): "回退国家简报历史版本",
    ("GET", "/api/admin/knowledge/whitepapers/{country_code}/export.pdf"): "下载国家简报（PDF）",
    ("GET", "/api/admin/knowledge/whitepapers/{country_code}/export.docx"): "下载国家简报（Word）",
    ("GET", "/api/analysis/tasks/{task_id}/attachments/merged-review/export.{kind}"): "下载制度对照结果",
    ("POST", "/api/analysis/tasks/{task_id}/attachments"): "上传制度文件",
    ("POST", "/api/analysis/tasks/{task_id}/attachments/review-pending"): "对照制度文件",
    ("DELETE", "/api/analysis/tasks/{task_id}/attachments/{attachment_id}"): "删除制度文件",
    ("GET", "/api/analysis/tasks/{task_id}/attachments/{attachment_id}/review/export.pdf"): "下载制度对照结果（PDF）",
    ("GET", "/api/analysis/tasks/{task_id}/attachments/{attachment_id}/review/export.docx"): "下载制度对照结果（Word）",
    ("POST", "/api/admin/knowledge/terminology/rebuild"): "重建法律术语",
    ("POST", "/api/admin/knowledge/terminology/references"): "登记术语出处",
    ("POST", "/api/admin/knowledge/terminology/ambiguities"): "处理术语歧义",
    ("POST", "/api/admin/agent-studio/workflow/versions/{version}/activate"): "启用流程版本",
    ("GET", "/api/admin/agent-studio/workflow/export"): "导出流程配置",
    ("POST", "/api/admin/agent-studio/workflow/import"): "导入流程配置",
    ("DELETE", "/api/admin/agent-studio/tasks/{task_id}"): "删除企业对话",
    ("POST", "/api/admin/agent-studio/tasks/{task_id}/rerun"): "重新运行评估",
    ("POST", "/api/admin/agent-studio/evaluations/{batch_id}/cases/{question_id}"): "运行评测案例",
    ("POST", "/api/analysis/tasks"): "新建评估对话",
    ("DELETE", "/api/analysis/tasks/{task_id}"): "删除对话",
    ("PATCH", "/api/analysis/tasks/{task_id}"): "重命名对话",
    ("POST", "/api/analysis/tasks/{task_id}/restore"): "恢复对话",
    ("POST", "/api/analysis/tasks/{task_id}/clone"): "复制咨询对话",
    ("POST", "/api/analysis/tasks/batch-archive"): "一键归档项目或对话",
    ("POST", "/api/analysis/tasks/batch-restore"): "一键恢复归档项目或对话",
    ("POST", "/api/analysis/tasks/batch-delete"): "一键清空已归档项目或对话",
    ("POST", "/api/analysis/tasks/{task_id}/facts"): "补充业务信息",
    ("POST", "/api/analysis/tasks/{task_id}/run"): "开始评估",
    ("POST", "/api/analysis/tasks/{task_id}/reports/edit"): "修改评估报告",
    ("GET", "/api/analysis/tasks/{task_id}/reports/{version_no}/export.{ext}"): "下载评估报告",
    ("GET", "/api/analysis/tasks/{task_id}/chat-export.{fmt}"): "下载咨询问答纪要",
    ("POST", "/api/analysis/chats"): "新建咨询对话",
    ("POST", "/api/analysis/tasks/{task_id}/messages"): "向咨询助手提问",
    ("POST", "/api/analysis/tasks/{task_id}/documents/{code}/answers"): "填写合规文件",
    ("POST", "/api/analysis/tasks/{task_id}/documents/{code}/paper"): "生成合规文件论述",
    ("GET", "/api/analysis/tasks/{task_id}/documents/{code}/export.docx"): "下载合规文件（Word）",
    ("GET", "/api/analysis/tasks/{task_id}/documents/{code}/export.xlsx"): "下载合规文件（Excel）",
    ("GET", "/api/analysis/tasks/{task_id}/documents/{code}/export.pdf"): "下载合规文件（PDF）",
    ("GET", "/api/analysis/tasks/{task_id}/documents/{code}/export-en.docx"): "下载合规文件英文版（Word）",
    ("GET", "/api/analysis/tasks/{task_id}/documents/{code}/export-en.pdf"): "下载合规文件英文版（PDF）",
    ("GET", "/api/analysis/tasks/{task_id}/documents/dpia/export-edpb-en.docx"): "下载欧盟标准英文范本（Word）",
    ("GET", "/api/analysis/tasks/{task_id}/documents/dpia/export-edpb-en.pdf"): "下载欧盟标准英文范本（PDF）",
    ("POST", "/api/analysis/tasks/{task_id}/documents/{code}/saved"): "保存合规文件",
    ("PATCH", "/api/analysis/tasks/{task_id}/documents/{code}/saved"): "修改合规文件",
    ("DELETE", "/api/analysis/tasks/{task_id}/documents/{code}/saved"): "恢复自动生成版合规文件",
    ("POST", "/api/analysis/tasks/{task_id}/documents/{code}/versions"): "生成合规文件历史版本",
    ("POST", "/api/analysis/tasks/{task_id}/documents/{code}/versions/{version_no}/rollback"): "回退合规文件历史版本",
    ("POST", "/api/analysis/doc-projects"): "新建合规评估项目",
    ("PATCH", "/api/analysis/doc-projects/{project_id}"): "修改合规评估项目",
    ("POST", "/api/analysis/doc-projects/{project_id}/clone"): "复制合规评估项目",
    ("GET", "/api/analysis/doc-projects/{project_id}/official-forms/{form_code}/export.pdf"): "下载官方表格",
    ("GET", "/api/analysis/doc-projects/{project_id}/official-forms/{form_code}/export.docx"): "下载官方表格（Word）",
    ("POST", "/api/analysis/doc-projects/{project_id}/chat"): "向项目助手提问",
    ("POST", "/api/analysis/doc-projects/{project_id}/copilot-extract"): "识别对话中的填报信息",
    ("POST", "/api/analysis/doc-projects/{project_id}/questions/{key}/ask"): "就填写项提问",
    ("PUT", "/api/analysis/doc-projects/{project_id}/answers"): "填写合规评估信息",
    ("POST", "/api/analysis/doc-projects/{project_id}/risks/diagnose"): "智能研判或动态复评风险",
    ("POST", "/api/analysis/doc-projects/{project_id}/risks/remediate"): "把风险措施生成整改项",
    ("POST", "/api/analysis/doc-projects/{project_id}/suggest"): "识别业务情况",
    ("POST", "/api/analysis/doc-projects/{project_id}/suggest-file"): "从文件识别业务情况",
    ("POST", "/api/analysis/doc-projects/{project_id}/suggestions/accept"): "采用识别结果",
    ("POST", "/api/analysis/doc-projects/{project_id}/documents/{code}/include"): "设置合规文件是否纳入本次出件",
    ("POST", "/api/analysis/doc-projects/{project_id}/documents/{code}/paper"): "生成论述",
    ("GET", "/api/analysis/doc-projects/{project_id}/delivery-aid/{aid_kind}/export.{fmt}"): "下载项目交付辅助清单",
    ("GET", "/api/analysis/doc-projects/{project_id}/export.zip"): "下载成稿包",
    ("PUT", "/api/admin/document-templates/verdict-rules/{code}"): "修改标准判定结论范本",
    ("PUT", "/api/admin/document-templates/{code}"): "修改文件模板",
    ("GET", "/api/admin/document-templates/official/{code}/original"): "下载官方表格原件",
    ("GET", "/api/admin/document-templates/official-forms/{code}/original"): "下载官方表格原件",
    ("GET", "/api/admin/audit-logs/export.xlsx"): "导出审计记录",
    ("GET", "/api/admin/audit-logs/export.csv"): "导出审计记录（CSV）",
    ("POST", "/api/forms/instances"): "新建官方表格",
    ("DELETE", "/api/forms/instances/{instance_id}"): "删除官方表格",
    ("PUT", "/api/forms/instances/{instance_id}/answers"): "填写官方表格",
    ("POST", "/api/forms/instances/{instance_id}/assist"): "官方表格对话填报助手",
    ("GET", "/api/forms/instances/{instance_id}/export.pdf"): "下载官方表格",
    ("GET", "/api/forms/instances/{instance_id}/export.docx"): "下载官方表格（Word）",
    ("POST", "/api/admission/projects/{project_id}/check"): "检查准入材料",
    ("POST", "/api/admission/projects"): "新建准入项目",
    ("DELETE", "/api/admission/projects/{project_id}"): "删除准入项目",
    ("PUT", "/api/admission/projects/{project_id}/approval"): "登记批文",
    ("PUT", "/api/admission/projects/{project_id}/link"): "关联合规评估项目",
    ("POST", "/api/admission/projects/{project_id}/answers"): "填写准入申报信息",
    ("POST", "/api/admission/projects/{project_id}/assist"): "准入材料对话填报助手",
    ("POST", "/api/admission/projects/{project_id}/drafts"): "生成准入材料",
    ("GET", "/api/admission/drafts/{draft_id}/export.{fmt}"): "下载准入材料",
    ("POST", "/api/admission/drafts/{draft_id}/foreign"): "生成准入申请书外文正式稿",
    ("GET", "/api/admission/drafts/{draft_id}/foreign/export.{fmt}"): "下载准入申请书外文正式稿",
    ("POST", "/api/admission/projects/{project_id}/issues"): "新增补正意见",
    ("PATCH", "/api/admission/issues/{issue_id}"): "处理补正意见",
    ("DELETE", "/api/admission/issues/{issue_id}"): "删除补正意见",
    ("PATCH", "/api/admin/admission/entries/{entry_id}"): "确认准入事项或设为范围外",
    ("PUT", "/api/admin/admission/entries/{entry_id}/content"): "修改准入事项资料",
    ("GET", "/api/admission/drafts/{draft_id}/forms/{code}"): "下载某一版申报材料里的官方表格",
    ("POST", "/api/notifications/read-all"): "查看更新通知",
    ("POST", "/api/notifications/{notification_id}/read"): "标记通知已读",
    ("POST", "/api/notifications/{notification_id}/reevaluate"): "按法规更新重新评估",
    ("PUT", "/api/admin/opinion/digest-config"): "配置舆情合规周报推送",
    ("POST", "/api/admin/opinion/digest-send"): "生成并推送舆情合规周报",
    ("POST", "/api/admin/opinion/digest-test"): "发送舆情周报验证邮件",
    ("POST", "/api/admin/opinion/collect-now"): "立即抽取最新舆情动态",
    ("GET", "/api/admin/updates/report/export.xlsx"): "导出法规监测与联动时效报表",
}

# 新接口还没登记时的兜底：按模块写一个人话的类别，绝不显示地址
_FALLBACK: list[tuple[str, str]] = [
    (r"^/api/admin/knowledge/(nodes|edges)", "知识图谱"),
    (r"^/api/admin/knowledge/(translations|glossary|terminology)", "译文与术语"),
    (r"^/api/admin/knowledge/whitepapers|^/api/knowledge/whitepapers", "国家简报"),
    (r"^/api/admin/knowledge", "法规知识库"),
    (r"^/api/admin/orgs", "企业"),
    (r"^/api/admin/users", "账号"),
    (r"^/api/admin/admission|^/api/admission", "海外属地准入"),
    (r"^/api/admin/document-templates", "文件模板"),
    (r"^/api/admin/sites", "采集站点"),
    (r"^/api/admin/(documents|versions)", "法规文档"),
    (r"^/api/admin/agent-studio", "评估执行记录"),
    (r"^/api/forms", "官方表格"),
    (r"^/api/analysis/doc-projects", "合规评估项目"),
    (r"^/api/analysis", "咨询对话"),
    (r"^/api/notifications|^/api/admin/updates", "更新通知"),
    (r"^/api/auth|^/api/user", "账号"),
]
_METHOD_ZH = {"POST": "新增", "PUT": "修改", "PATCH": "修改", "DELETE": "删除", "GET": "下载"}


def _compile(template: str) -> re.Pattern:
    out, pos = "", 0
    for m in re.finditer(r"\{(\w+)\}", template):
        out += re.escape(template[pos:m.start()]) + f"(?P<{m.group(1)}>[^/]+?)"
        pos = m.end()
    return re.compile("^" + out + re.escape(template[pos:]) + "$")


_COMPILED = [(method, template, _compile(template)) for (method, template) in ROUTE_ACTIONS]


def match(method: str, path: str) -> tuple[str | None, dict[str, str]]:
    """(route template, path parameters) of a request, or (None, {})."""
    for m, template, rx in _COMPILED:
        if m == method:
            hit = rx.match(path)
            if hit:
                return template, hit.groupdict()
    return None, {}


def action_name(method: str, path: str) -> str:
    template, _ = match(method, path)
    if template is not None:
        return ROUTE_ACTIONS[(method, template)]
    for pattern, name in _FALLBACK:
        if re.search(pattern, path):
            return f"{_METHOD_ZH.get(method, '操作')}：{name}"
    return f"{_METHOD_ZH.get(method, '操作')}：其他"


# ---------------------------------------------------------------- 表与栏目的中文名

TABLE_ZH: dict[str, str] = {
    "analysis_tasks": "对话",
    "analysis_messages": "对话消息",
    "report_versions": "评估报告",
    "task_attachments": "制度文件",
    "attachment_reviews": "制度对照结果",
    "document_instances": "合规文件",
    "document_edits": "合规文件修改",
    "document_instance_versions": "合规文件历史版本",
    "document_narratives": "论述",
    "document_templates": "文件模板",
    "form_instances": "官方表格",
    "admission_projects": "准入项目",
    "admission_entries": "准入事项",
    "admission_steps": "办理环节",
    "admission_progress": "办理进度",
    "admission_drafts": "准入材料",
    "admission_issues": "补正意见",
    "admission_files": "准入材料文件",
    "organizations": "企业",
    "users": "账号",
    "documents": "法规",
    "document_versions": "法规版本",
    "clauses": "条文",
    "clause_readings": "条文校读",
    "clause_translations": "译文",
    "glossary_terms": "法律术语",
    "legal_term_definitions": "法律术语定义",
    "review_points": "核对要点",
    "kg_nodes": "图谱节点",
    "kg_edges": "图谱关系",
    "knowledge_change_proposals": "待审批修改",
    "country_whitepapers": "国家简报",
    "country_page_texts": "国家页概述",
    "source_sites": "采集站点",
    "standard_entries": "标准目录",
    "user_notifications": "更新通知",
    "user_subscriptions": "法规订阅",
    "update_impacts": "更新影响",
    "agent_workflow_configs": "流程配置",
    "opinion_items": "舆情资讯",
    "opinion_digest_configs": "舆情周报配置",
    "opinion_digest_runs": "舆情周报推送记录",
}
# 只记内部过程、与“做了什么”无关的表
SKIP_TABLES = frozenset({
    "audit_logs", "llm_call_audit", "pipeline_events", "analysis_events", "analysis_step_traces",
    "translation_chunk_cache", "knowledge_audit_records", "source_fetch_observations", "crawl_runs",
})
COLUMN_ZH: dict[str, str] = {
    "title": "名称", "name": "名称", "name_zh": "名称", "display_name": "姓名", "email": "邮箱",
    "password_hash": "登录密码", "can_export": "文书导出权限", "signup_source": "账号来源",
    "role": "角色", "is_active": "是否启用", "status": "状态", "state": "状态", "review_status": "审核状态",
    "verify_state": "核验状态", "facts_json": "填写内容", "facts": "填写内容", "answers": "填写内容",
    "approval": "批文登记", "archived_at": "归档时间", "content": "内容", "translated_text": "译文",
    "reviewer_note": "审核意见", "relevance": "相关性", "relevance_note": "相关性说明",
    "llm_redaction_terms": "调用外部模型时隐去的词", "summary": "摘要", "summary_zh": "摘要", "note": "备注",
    "filename": "文件名", "question": "问题", "answer": "答复", "kind": "类别", "target": "对象",
    "country_code": "国家", "vehicle_model": "车型", "scenario_type": "业务场景", "applicant": "申请人",
    "function_key": "业务", "task_id": "关联合规评估项目", "blocks": "正文", "definition": "模板内容",
    "version_no": "版本", "target_zh": "中文译名", "source_term": "原文术语", "authority_zh": "受理机构",
    "category": "类别", "profile": "事项栏目", "functions": "适用业务", "entry_url": "入口地址",
    "terms_status": "使用条款", "enabled": "是否启用", "read_at": "已读时间", "lawyer_reply": "答复",
    "relation_type": "关系类型", "evidence_quote": "依据原文", "is_demo": "演示用",
    "title_zh": "名称", "detail_zh": "说明", "materials": "材料", "materials_plan": "材料准备清单",
    "steps": "办理环节", "risks": "风险评估记录", "risk_rating": "风险评估记录",
    "number": "批文编号", "issued_on": "签发日期", "expires_on": "有效期至", "long_term": "长期有效",
    "result_on": "批复日期", "submitted_on": "递交日期", "on": "发生日期", "reason": "原因说明",
    "questions": "补正问题", "milestones": "申报进度", "countries": "订阅国家",
}
_LABEL_ATTRS = ("title", "name_zh", "name", "filename", "display_name", "email", "target_zh", "source_term", "question", "number")
_NOISE = re.compile(r"(^id$|_id$|_by$|^created|^updated|hash$|sha256|^embedding|archive_path|^tokens_|duration_ms|^version$|_custom$|_json_version$)")
_SECRET = re.compile(r"(password|secret|api_?key|access_token|refresh_token|jwt)", re.IGNORECASE)
# 上传文件的内容（以及从文件里取出的全文）只写“已上传”，不进审计
_FILE_CONTENT = {("task_attachments", "text"), ("admission_files", "data"), ("admission_files", "content")}

# ---------------------------------------------------------------- 本次请求的写库记录

_changes: contextvars.ContextVar[list | None] = contextvars.ContextVar("audit_changes", default=None)
_timing: contextvars.ContextVar[list | None] = contextvars.ContextVar("audit_timing", default=None)
ENABLED = True  # 测时用：关掉后监听什么也不做


def begin() -> tuple[contextvars.Token, contextvars.Token]:
    """Start collecting this request's writes (called by the HTTP middleware)."""
    return _changes.set([]), _timing.set([0.0])


def end(tokens) -> None:
    _changes.reset(tokens[0])
    _timing.reset(tokens[1])


def collected() -> list[dict]:
    return list(_changes.get() or [])


def spent_seconds() -> float:
    t = _timing.get()
    return t[0] if t else 0.0


_STATIC_FACT_ZH: dict[str, str] = {
    "source_country": "数据发出国", "recipient_country": "数据接收国", "vehicle_model": "车型",
    "scenario": "业务场景", "company_name": "企业名称", "country_name": "国家",
    "data_types": "涉及数据类型", "data_volume": "数据规模", "purpose": "处理目的",
    "recipient": "境外接收方", "risk_rating": "风险评估记录", "measures": "整改落实措施",
}


def _summarize_structured(col_or_key: str, value: Any) -> str | None:
    """Summarize list/dict or JSON-encoded structured payloads into concise Chinese prose without raw JSON."""
    parsed = value
    if isinstance(value, str):
        s = value.lstrip()
        if not (s.startswith("[{") or s.startswith('{"')):
            return None
        try:
            parsed = json.loads(s)
        except (TypeError, ValueError):
            return None
        if not isinstance(parsed, (list, dict)):
            return None
    if not isinstance(parsed, (list, dict)):
        return None
    key_low = (col_or_key or "").lower()
    if isinstance(parsed, list):
        if not parsed:
            return "无"
        first = parsed[0]
        if "block" in key_low or (
            isinstance(first, dict) and "type" in first and any(k in first for k in ("text", "rows", "items"))
        ):
            return f"正文共 {len(parsed)} 个段落"
        if "risk" in key_low or (
            isinstance(first, dict) and any(k in first for k in ("likelihood", "severity", "before_level", "risk"))
        ):
            return f"共 {len(parsed)} 项风险评估记录"
        if "step" in key_low or (isinstance(first, dict) and "title_zh" in first):
            return f"共 {len(parsed)} 个办理环节"
        if "material" in key_low:
            if all(isinstance(x, str) for x in parsed) and len(parsed) <= 4:
                return "、".join(x for x in parsed if x.strip()) or "无"
            return f"共 {len(parsed)} 项材料"
        if all(isinstance(x, (str, int, float, bool)) for x in parsed):
            items_zh = [VALUE_ZH.get(str(x), str(x)) for x in parsed if str(x).strip()]
            if not items_zh:
                return "无"
            if len(items_zh) <= 4 and sum(len(x) for x in items_zh) <= 40:
                return "、".join(items_zh)
            return f"共 {len(items_zh)} 项（{'、'.join(items_zh[:3])}{'等' if len(items_zh) > 3 else ''}）"
        return f"共 {len(parsed)} 项记录"

    if not parsed:
        return "无"
    if "definition" in key_low or ("sections" in parsed and "facts" in parsed):
        sec_n = len(parsed.get("sections") or [])
        return f"模板结构（共 {sec_n} 个章节）" if sec_n else "已更新模板配置"
    pairs: list[str] = []
    for dk, dv in parsed.items():
        ks = str(dk)
        if ks.startswith("_") or ks.endswith("_id") or ks in ("id", "content_hash", "password_hash") or dv in (None, "", [], {}):
            continue
        plain_k = ks[4:] if ks.startswith("doc_") else ks
        label_k = COLUMN_ZH.get(ks) or COLUMN_ZH.get(plain_k) or _STATIC_FACT_ZH.get(plain_k) or plain_k
        if isinstance(dv, (list, dict)):
            sub = _summarize_structured(plain_k, dv) or f"共 {len(dv)} 项"
            pairs.append(f"{label_k}：{sub}")
        else:
            sv = _summarize_structured(plain_k, dv)
            if sv is not None:
                pairs.append(f"{label_k}：{sv}")
            else:
                pairs.append(f"{label_k}：{VALUE_ZH.get(str(dv), str(dv))[:24]}")
    if not pairs:
        return f"共 {len(parsed)} 项配置"
    return "；".join(pairs[:3]) + (f" 等 {len(pairs)} 项" if len(pairs) > 3 else "")


def short(value: Any, col_or_key: str = "") -> Any:
    """A value fit for the record: plain types, structured summaries, or long text cut with its length stated."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "已上传（文件内容不记录）"
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    summarized = _summarize_structured(col_or_key, value)
    if summarized is not None:
        return summarized
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            value = str(value)
    return value if len(value) <= MAX_VALUE else value[:MAX_VALUE] + f"…（共 {len(value)} 字）"


def _secret(table: str, column: str) -> bool:
    return bool(_SECRET.search(column)) or (table, column) in _FILE_CONTENT


def _label(obj) -> str:
    for attr in _LABEL_ATTRS:
        value = getattr(obj, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()[:80]
    if getattr(obj, "__tablename__", "") == "form_instances":
        return str(getattr(obj, "form_code", "") or "")
    return ""


def _json_diff(before: Any, after: Any) -> list[tuple[str, Any, Any]]:
    if isinstance(before, dict) or isinstance(after, dict):
        b, a = before if isinstance(before, dict) else {}, after if isinstance(after, dict) else {}
        return [(k, b.get(k), a.get(k)) for k in sorted(set(b) | set(a), key=str) if b.get(k) != a.get(k) and not str(k).startswith("_")]
    return []


def _capture(session, flush_context, instances) -> None:  # noqa: ARG001
    """Session ``before_flush`` listener: note what this flush adds, changes and removes."""
    bucket = _changes.get()
    if bucket is None or not ENABLED or len(bucket) >= MAX_OBJECTS:
        return
    started = time.perf_counter()
    try:
        from sqlalchemy import inspect

        def columns(obj):
            return [c.key for c in inspect(obj).mapper.column_attrs]

        for kind, objs in (("新增", session.new), ("修改", session.dirty), ("删除", session.deleted)):
            for obj in list(objs):
                table = getattr(obj, "__tablename__", "")
                if not table or table in SKIP_TABLES or len(bucket) >= MAX_OBJECTS:
                    continue
                state = inspect(obj)
                fields: list[dict] = []
                for col in columns(obj):
                    if _NOISE.search(col) and col != "archived_at" and not _secret(table, col):
                        continue
                    if col not in COLUMN_ZH and not _secret(table, col):
                        continue
                    if kind == "修改":
                        hist = state.attrs[col].history
                        if not hist.has_changes():
                            continue
                        before = hist.deleted[0] if hist.deleted else None
                        after = hist.added[0] if hist.added else getattr(obj, col, None)
                    elif kind == "新增":
                        before, after = None, getattr(obj, col, None)
                        if after in (None, "", [], {}):
                            continue
                    else:
                        continue
                    if _secret(table, col):
                        fields.append({"column": col, "secret": True})
                        continue
                    keys = _json_diff(before, after) if kind == "修改" else []
                    if keys:
                        for k, b, a in keys[:MAX_FIELDS]:
                            fields.append({"column": col, "key": str(k), "before": short(b, str(k)), "after": short(a, str(k))})
                    elif kind == "修改" and (isinstance(before, dict) or isinstance(after, dict)):
                        continue  # 只是字典里内部标记变了
                    else:
                        fields.append({"column": col, "before": short(before, col), "after": short(after, col)})
                    if len(fields) >= MAX_FIELDS:
                        break
                if kind == "修改" and not fields:
                    continue
                bucket.append({
                    "kind": kind, "table": table, "label": _label(obj),
                    "form_code": getattr(obj, "form_code", None) if table == "form_instances" else None,
                    "task_kind": getattr(obj, "kind", None) if table == "analysis_tasks" else None,
                    "fields": fields,
                })
    except Exception:  # noqa: BLE001  审计只记，不挡业务
        logger.exception("审计：记录写库内容失败")
    finally:
        t = _timing.get()
        if t:
            t[0] += time.perf_counter() - started


def install() -> None:
    """Attach the listener once to every database session of the process."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    if not event.contains(Session, "before_flush", _capture):
        event.listen(Session, "before_flush", _capture)


# ---------------------------------------------------------------- 组装成人话

_PARAM_OBJECTS: list[tuple[str, str, str, str]] = [
    # (地址前缀, 参数, 表, 对象类别)
    ("/api/analysis/doc-projects", "project_id", "analysis_tasks", "合规评估项目"),
    ("/api/admission/projects", "project_id", "admission_projects", "准入项目"),
    ("", "task_id", "analysis_tasks", "对话"),
    ("", "instance_id", "form_instances", "官方表格"),
    ("", "org_id", "organizations", "企业"),
    ("", "site_id", "source_sites", "采集站点"),
    ("", "document_id", "documents", "法规"),
    ("", "node_id", "kg_nodes", "图谱节点"),
    ("", "term_id", "glossary_terms", "法律术语"),
    ("", "point_id", "review_points", "核对要点"),
    ("", "file_id", "admission_files", "准入材料文件"),
    ("", "issue_id", "admission_issues", "补正意见"),
    ("", "draft_id", "admission_drafts", "准入材料"),
    ("", "entry_id", "admission_entries", "准入事项"),
    ("", "attachment_id", "task_attachments", "制度文件"),
]
_TABLE_LABEL_SQL = {
    "analysis_tasks": "select title, kind from analysis_tasks where id = :id",
    "admission_projects": "select title, null from admission_projects where id = :id",
    "form_instances": "select form_code, null from form_instances where id = :id",
    "organizations": "select name, null from organizations where id = :id",
    "source_sites": "select publisher, null from source_sites where id = :id",
    "documents": "select coalesce(nullif(title_zh, ''), title), null from documents where id = :id",
    "kg_nodes": "select name_zh, null from kg_nodes where id = :id",
    "glossary_terms": "select coalesce(nullif(target_zh, ''), source_term), null from glossary_terms where id = :id",
    "review_points": "select title, null from review_points where id = :id",
    "admission_files": "select filename, null from admission_files where id = :id",
    "admission_issues": "select question, null from admission_issues where id = :id",
    "admission_drafts": "select name_zh, null from admission_drafts where id = :id",
    "admission_entries": "select name_zh, null from admission_entries where id = :id",
    "task_attachments": "select filename, null from task_attachments where id = :id",
}

_labels_cache: dict[str, Any] = {"at": 0.0, "facts": {}}


async def _fact_labels(session) -> dict[str, str]:
    """填写项 key → 中文名：取自各文件模板（合规评估项目与准入材料共用），十分钟刷新一次。"""
    from sqlalchemy import text

    if time.monotonic() - _labels_cache["at"] < 600 and _labels_cache["facts"]:
        return _labels_cache["facts"]
    labels: dict[str, str] = {
        "source_country": "数据发出国", "recipient_country": "数据接收国", "vehicle_model": "车型",
        "scenario": "业务场景", "company_name": "企业名称", "country_name": "国家",
        "_manual_docs": "纳入出件设置", "_suggest_diagnosis": "材料合规诊断",
    }
    rows = (await session.execute(text("select definition from document_templates where status = 'published'"))).all()
    for (definition,) in rows:
        for f in (definition or {}).get("facts", []) or []:
            if isinstance(f, dict) and f.get("key") and f.get("label"):
                labels.setdefault(str(f["key"]), str(f["label"]))
    if rows:  # 模板还没建好时不缓存，否则十分钟内都只认得几个基本项
        _labels_cache.update(at=time.monotonic(), facts=labels)
    return labels


def _form_labels(code: str | None) -> tuple[str, dict[str, str]]:
    if not code:
        return "", {}
    from app.forms import service

    form = service.get_form(code)
    if form:
        return form.get("name_zh") or code, {
            i["id"]: i.get("label_zh") or i["id"]
            for s in form.get("sections", [])
            for i in s.get("items", [])
        }
    official_map = _official_form_names()
    return official_map.get(code, code), {}


def _country_zh(code: str) -> str:
    try:
        from app.analysis.workflow_engine import country_name_zh

        return country_name_zh(code)
    except Exception:  # noqa: BLE001
        return code


_DOC_NAMES: dict[str, str] = {}
_OFFICIAL_FORM_MAP: dict[str, str] = {}
_OFFICIAL_CODE_RE: re.Pattern | None = None


def _official_form_names() -> dict[str, str]:
    global _OFFICIAL_CODE_RE
    if not _OFFICIAL_FORM_MAP:
        try:
            from app.forms import service

            for fcode, fmeta in service.catalog().items():
                if fcode and fmeta.get("name_zh"):
                    _OFFICIAL_FORM_MAP[str(fcode)] = str(fmeta["name_zh"])
        except Exception:  # noqa: BLE001
            pass
        try:
            from app.analysis.documents_router import _CN_OFFICIAL

            for item in _CN_OFFICIAL:
                c = str(item.get("code") or "")
                n = str(item.get("name_zh") or "")
                cc = str(item.get("country") or "")
                if c and n:
                    prefix = "中国：" if cc == "CN" else ("欧盟：" if cc == "EU" else "")
                    _OFFICIAL_FORM_MAP[c] = f"{prefix}{n}" if prefix and not n.startswith(prefix) else n
        except Exception:  # noqa: BLE001
            pass
        if _OFFICIAL_FORM_MAP:
            escaped = sorted((re.escape(k) for k in _OFFICIAL_FORM_MAP), key=len, reverse=True)
            _OFFICIAL_CODE_RE = re.compile(rf"(?<![A-Za-z0-9_])({'|'.join(escaped)})(?![A-Za-z0-9_])")
    return _OFFICIAL_FORM_MAP


def replace_official_codes(text: str | None) -> str:
    if not text:
        return ""
    fmap = _official_form_names()
    if not fmap or _OFFICIAL_CODE_RE is None:
        return text

    def _sub(m: re.Match) -> str:
        code = m.group(1)
        zh = fmap.get(code, code)
        start, end = m.start(), m.end()
        prev_ch = text[start - 1] if start > 0 else ""
        next_ch = text[end] if end < len(text) else ""
        if prev_ch in ("《", "“", '"', "（", "(") or next_ch in ("》", "”", '"', "）", ")"):
            return zh
        return f"《{zh}》"

    return _OFFICIAL_CODE_RE.sub(_sub, text)


def _scrub_audit_jargon(text: str) -> str:
    from app.core.jargon import scrub_engineering_jargon

    return scrub_engineering_jargon(
        text,
        ocr_zh="版面文字识别",
        llm_zh="智能分析模型",
        rag_zh="条文检索增强",
        json_zh="结构化数据",
        uuid_zh="唯一标识",
        pydantic_zh="格式核验",
        cjk_zh="中文汉字",
    )


def humanize_payload(payload: Any) -> Any:
    """Recursively replace raw official form codes and technical jargon inside audit ``before``/``after`` views on read."""
    if payload is None or isinstance(payload, (bool, int, float)):
        return payload
    if isinstance(payload, str):
        fmap = _official_form_names()
        if payload in fmap:
            return fmap[payload]
        return _scrub_audit_jargon(replace_official_codes(payload))
    if isinstance(payload, list):
        return [humanize_payload(x) for x in payload]
    if isinstance(payload, dict):
        return {humanize_payload(k) if isinstance(k, str) else k: humanize_payload(v) for k, v in payload.items()}
    return payload


def _doc_name(code: str) -> str:
    if not _DOC_NAMES:
        try:
            from app.analysis.document_templates_default import DEFAULT_TEMPLATES

            _DOC_NAMES.update({c: n for c, n, *_ in DEFAULT_TEMPLATES})
        except Exception:  # noqa: BLE001
            pass
    fmap = _official_form_names()
    if code in fmap:
        return f"《{fmap[code]}》"
    return _DOC_NAMES.get(code, code)


_AID_KIND_ZH = {
    "signoff": "正式申报递交前人工核签清单",
    "third-country": "库外法域属地法律待核查清单",
}


async def target_of(session, path: str, params: dict[str, str]) -> str:
    """What the request acted on, e.g. 合规评估项目《摩洛哥 → 中国》· 风险评估报告."""
    from sqlalchemy import text

    parts: list[str] = []
    for prefix, param, table, kind_zh in _PARAM_OBJECTS:
        if param not in params or (prefix and not path.startswith(prefix)):
            continue
        if any(p.startswith(kind_zh) for p in parts):
            continue
        try:
            row = (await session.execute(text(_TABLE_LABEL_SQL[table]), {"id": uuid.UUID(params[param])})).first()
        except (ValueError, KeyError):
            row = None
        if row is None:
            continue
        name, kind = row[0], row[1]
        if table == "form_instances":
            name = _form_labels(name)[0]
        if table == "analysis_tasks" and kind == "documents":
            kind_zh = "合规评估项目"
        parts.append(f"{kind_zh}《{name}》" if name else kind_zh)
    if "country_code" in params:
        parts.append(f"《{_country_zh(params['country_code'])}》")
    if "code" in params:
        parts.append(_doc_name(params["code"]))
    if "form_code" in params:
        fname = _form_labels(params["form_code"])[0]
        parts.append(f"《{fname}》" if fname and not fname.startswith("《") else fname)
    if "aid_kind" in params:
        parts.append(_AID_KIND_ZH.get(params["aid_kind"], params["aid_kind"]))
    return " · ".join(parts)


async def describe(session, method: str, path: str, changes: list[dict]) -> tuple[str, str, dict | None, dict | None]:
    """(what the request acted on, what it changed, before, after) for a finished request."""
    _, params = match(method, path)
    target = ""
    try:
        target = await target_of(session, path, params)
    except Exception:  # noqa: BLE001
        logger.exception("审计：找不到操作对象的名称")
    facts = await _fact_labels(session) if any(c["table"] in ("analysis_tasks", "admission_projects") for c in changes) else {}
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    phrases: list[str] = []
    for c in changes:
        table_zh = "合规评估项目" if c.get("task_kind") == "documents" else TABLE_ZH.get(c["table"], c["table"])
        obj = f"{table_zh}《{c['label']}》" if c["label"] else table_zh
        form_name, form_fields = _form_labels(c.get("form_code")) if c["table"] == "form_instances" else ("", {})
        if form_name:
            obj = f"{table_zh}《{form_name}》"
        if c["kind"] == "删除":
            phrases.append(f"删除{obj}")
            continue
        if c["kind"] == "新增":
            phrases.append(f"新增{obj}")
            for f in c["fields"]:
                name = COLUMN_ZH.get(f["column"])
                if name:
                    after[f"{obj}.{name}"] = "已修改" if f.get("secret") else f["after"]
            continue
        changed: list[str] = []
        for f in c["fields"]:
            col = COLUMN_ZH.get(f["column"], f["column"])
            if "key" in f:
                key = f["key"]
                plain = key[4:] if key.startswith("doc_") else key
                col = form_fields.get(key) or facts.get(plain) or facts.get(key) or COLUMN_ZH.get(plain) or COLUMN_ZH.get(key) or plain
            if f.get("secret"):
                after[f"{obj}.{col}"] = "已修改"
                changed.append(f"{col}（已修改）")
                continue
            before[f"{obj}.{col}"] = f["before"]
            after[f"{obj}.{col}"] = f["after"]
            if f["before"] in (None, ""):
                changed.append(f"{col} 填为“{_brief(f['after'])}”")
            elif f["after"] in (None, ""):
                changed.append(f"{col} 清空（原为“{_brief(f['before'])}”）")
            else:
                changed.append(f"{col} 由“{_brief(f['before'])}”改为“{_brief(f['after'])}”")
        if changed:
            more = f" 等 {len(changed)} 项" if len(changed) > 3 else ""
            # 改的就是这次操作的对象时不再重复写一遍名称
            lead = "" if obj in target else f"{obj}："
            phrases.append(lead + "；".join(changed[:3]) + more)
    seen: list[str] = []
    for p in phrases:
        if p not in seen:
            seen.append(p)
    text = "；".join(seen[:4]) + (f"；另有 {len(seen) - 4} 处" if len(seen) > 4 else "")
    return target, text, before or None, after or None


# 审计里改前改后的值：状态这类内部取值换成页面上的说法
VALUE_ZH = {
    "pending_review": "待复核", "approved": "已复核", "rejected": "有误", "out_of_scope": "不在清单", "stale": "已失效",
    "superseded": "已被替代", "machine_translated": "机器翻译", "translation_failed": "翻译未通过", "not_translated": "未翻译",
    "active": "进行中", "archived": "已归档", "open": "待处理", "closed": "已处理", "confirmed": "已确认",
    "collecting_facts": "收集业务信息", "running": "处理中", "completed": "已完成", "failed": "未完成",
    "awaiting_user_facts": "待补充信息", "partial_sample": "已生成样稿",
    "documents": "合规评估项目", "chat": "法规咨询对话", "advice": "法规咨询问答",
    "autonomous_trial": "自动驾驶道路测试", "connected_vehicle_cloud": "智能网联车联网上云",
    "ota_diagnostics": "跨境远程诊断与OTA", "cockpit_biometrics": "智能座舱与生物识别",
    "hd_mapping": "高精地图采集与测绘", "fleet_mobility": "车队与出行服务",
    "sales_service": "整车销售与售后客户数据", "insurance_ubi": "车险与驾驶行为分析",
    "charging_energy": "智能充电与能源服务",
    "vehicle_approval": "整车型式认证", "telecom_approval": "无线电与通信设备核准",
    "av_testing": "自动驾驶测试许可", "data_filing": "个人数据处理登记与出境许可",
    "preparing": "准备中", "submitted": "已递交", "correcting": "补正中", "renewing": "续期准备中",
    "assistant": "合规顾问", "user": "提问人", "system": "系统记录", "admin": "后台开通", "portal": "门户注册",
    "in_force": "现行有效", "upcoming": "即将施行", "draft": "征求意见", "repealed": "已废止", "amended": "已修订", "unknown": "待确认",
    "relevant": "核心相关", "related": "配套相关", "irrelevant": "不相关", "needs_review": "待人工复核",
    "before_translation_replacement": "更新译文前留档", "before_node_replacement": "更新图谱节点前留档", "before_edge_replacement": "更新图谱关系前留档",
    "CN": "中国", "SG": "新加坡", "EU": "欧盟", "DE": "德国", "FR": "法国", "MA": "摩洛哥", "TN": "突尼斯", "EG": "埃及",
    "SA": "沙特阿拉伯", "AE": "阿联酋", "TH": "泰国", "BR": "巴西", "US": "美国", "JP": "日本", "KR": "韩国", "GB": "英国",
    "ID": "印度尼西亚", "MY": "马来西亚", "VN": "越南", "IN": "印度", "AU": "澳大利亚", "CA": "加拿大", "MX": "墨西哥", "ZA": "南非",
    "platform_admin": "超级管理员", "ops_admin": "运维管理员", "org_admin": "企业用户", "member": "企业用户", "viewer": "普通查看者", "reviewer": "已停用账号",
    "ocr": "版面文字识别", "text_layer": "原文件文字层", "llm": "智能分析模型", "rule": "规则判定", "manual": "人工设定",
    "True": "是", "False": "否", "true": "是", "false": "否",
}

_STATUS_REASON_ZH: dict[int, str] = {
    400: "请求参数不符合要求",
    401: "登录状态已失效或未认证",
    403: "无操作权限",
    404: "目标记录不存在",
    405: "不支持该请求方式",
    409: "记录已存在或状态冲突",
    413: "上传内容超出大小限制",
    422: "填写内容格式有误",
    429: "操作过于频繁",
    500: "服务内部处理异常",
    502: "上游服务暂不可用",
    503: "服务暂时繁忙",
}


def status_reason_zh(status: int) -> str:
    return _STATUS_REASON_ZH.get(status, f"处理未完成 {status}")


def fallback_summary(category: str, action: str, method: str, path: str, status: int) -> str:
    if path == "/api/notifications/read-all":
        return "将全部更新通知标为已读（当前无新增未读项）" if status < 400 else f"标记更新通知未通过（{status_reason_zh(status)}）"
    if "/foreign/export." in path:
        ext = "PDF" if path.endswith(".pdf") else "Word"
        return f"导出海外属地准入外文申请书（{ext}）"
    if status >= 400:
        return f"{action or '请求'}未通过（{status_reason_zh(status)}）"
    if category == "export":
        return f"完成文件导出：{action or '业务文档'}"
    return f"执行操作：{action or '常规业务处理'}"


def _brief(value: Any, col_or_key: str = "") -> str:
    summarized = _summarize_structured(col_or_key, value)
    if summarized is not None:
        summarized = _scrub_audit_jargon(replace_official_codes(summarized))
        return summarized if len(summarized) <= 40 else summarized[:40] + "…"
    text = "" if value is None else str(value)
    fmap = _official_form_names()
    text = fmap.get(text, VALUE_ZH.get(text, text))
    text = _scrub_audit_jargon(replace_official_codes(text))
    return text if len(text) <= 40 else text[:40] + "…"


def readable(value: Any) -> str:
    """审计里改前改后的内容，按页面上的说法逐行写“栏目：值”；编号、内部字段不列。"""
    import re as _re

    if value in (None, "", {}, []):
        return ""
    if not isinstance(value, dict):
        return _brief(value)
    lines = []
    for k, v in value.items():
        if k.endswith("_id") or k in ("id", "content_hash") or (k == "password_hash" and v != "已修改") or (isinstance(v, str) and _re.fullmatch(r"[0-9a-f-]{36}", v)):
            continue
        name = COLUMN_ZH.get(k)
        if name is None and "." in k and not _re.fullmatch(r"[A-Za-z0-9_.]+", k):
            name = k
        if name is None:
            continue
        if isinstance(v, dict | list):
            text = _summarize_structured(k, v) or (f"共 {len(v)} 项" if v else "无")
        else:
            text = _brief(v, k)
        lines.append(f"{name}：{text}")
    return "\n".join(lines)


def humanize(summary: str | None) -> str | None:
    """旧记录的摘要里带着内部取值（“由“pending_review”改为……”）：显示时换成页面上的说法。"""
    if not summary:
        return summary
    import re as _re

    summary = _re.sub(r"；?人工改过的栏目 由“[^”]*”改为“[^”]*”", "", summary)  # 内部标记，11.11 一条走查记录里带出来过
    summary = _re.sub(
        r"未通过（状态码\s*(\d+)）",
        lambda m: f"未通过（{status_reason_zh(int(m.group(1)))}）",
        summary,
    )
    summary = _re.sub(r"（体验空间-[0-9a-fA-F]{6,12}）", "（门户独立体验空间）", summary)
    summary = _re.sub(r"\[\s*\{[^”\n]*", "结构化正文内容", summary)
    if "/api/" in summary:
        return ""
    summary = _re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "", summary, flags=_re.I)
    summary = _re.sub(r"(新增|删除|修改)([a-z][a-z_]+)", lambda m: f"{m.group(1)}{TABLE_ZH.get(m.group(2), m.group(2))}", summary)
    summary = replace_official_codes(summary)
    fmap = _official_form_names()
    out_s = _re.sub(
        r"“([A-Za-z0-9_]+)”",
        lambda m: f"“{fmap.get(m.group(1), VALUE_ZH.get(m.group(1), m.group(1)))}”",
        summary,
    ).strip(" ：:；;")
    return _scrub_audit_jargon(out_s)

