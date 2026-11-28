# ruff: noqa: E501
"""The application material for one admission item, written for the enterprise to file.

Built for the item itself (not the multi-item compliance report): who applies (by the item's own
applicant requirement), what to prepare (each material with how it is prepared and the clause it
rests on), the descriptions the law asks for (from what the enterprise gave once), the language to
file in, the route (law steps, the official forms, where to file, what comes back), the filing
checklist, and the clauses relied on — each once, in Chinese with the original. What the project
still lacks becomes a question; the answers are kept on the project."""

from __future__ import annotations

import re

from sqlalchemy import text as sql
from sqlalchemy.ext.asyncio import AsyncSession

from app.admission.material_kinds import NEW_FACTS, fact_registry, plan_of
from app.admission.models import AdmissionEntry, AdmissionProject
from app.analysis.chat import SCENARIO_LABELS
from app.analysis.document_templates import (
    DOC_FACT_PREFIX,
    MAX_ANSWER_CHARS,
    Question,
    Rendered,
    task_values,
)
from app.analysis.workflow_engine import country_name_zh

CODE = "admission"
DOC_NAME = "申请材料"  # 全站统一的叫法：平台为这一事项生成的那份文件
UNKNOWN = "待核实"
# 申请人资格要求是当地的主体（当地登记的企业、当地代理商……）：不能拿国内公司名代填，得企业自己写
LOCAL_APPLICANT = re.compile(r"当地|本地|境内|在(埃及|新加坡|突尼斯|摩洛哥|黎巴嫩|该国|本国)|商业登记|代理商|当地代理|籍")
LANG_ZH = {"阿拉伯语": "阿拉伯语", "法语": "法语", "英语": "英语"}


def doc_name(entry: AdmissionEntry) -> str:
    return f"{re.sub(r'(申请办理|申请|办理)$', '', entry.name_zh) or entry.name_zh}{DOC_NAME}"


def known_value(profile: dict | None, key: str) -> str:
    v = str((profile or {}).get(key) or "").strip()
    return "" if not v or v == UNKNOWN else v


def applicant_label(entry: AdmissionEntry) -> str:
    return "数据处理方" if entry.category == "data_filing" else "申请企业"


def project_facts(project: AdmissionProject, entry: AdmissionEntry, task=None) -> dict[str, str]:
    """What is already known. The linked assessment's answers come first so the project only has
    to add what the assessment never asked."""
    known: dict[str, str] = dict(task_values(task)) if task is not None else {}
    known.update(
        {
            "country_name": country_name_zh(entry.country_code),
            "company_name": project.applicant or known.get("company_name", ""),
            "vehicle_model": project.vehicle_model or known.get("vehicle_model", ""),
        }
    )
    if task is not None:
        known["scenario"] = SCENARIO_LABELS.get(task.scenario_type, "") or known.get("scenario", "")
    elif project.function_key and not known.get("scenario"):
        from app.admission.plan import FUNCTIONS  # 项目是为哪项业务准备的，平台知道，不再问

        known["scenario"] = FUNCTIONS.get(project.function_key, "")
    return known


async def clause_texts(session: AsyncSession, ids: list[str]) -> dict[str, dict]:
    """For each clause: where it is (“《中文法规名》第X条”), its current Chinese translation (one that
    passed the check) and its original text."""
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return {}
    from app.ingestion.display_text import for_reading
    from app.knowledge.reading import article_label

    rows = (
        await session.execute(
            sql(
                """select k.id::text, k.clause_number, coalesce(d.title_zh, d.title), k.original_text, d.id::text,
                   (select t.translated_text from clause_translations t where t.clause_id = k.id and t.is_current
                      and t.quality_check_passed and t.review_status not in ('translation_failed', 'not_translated', 'rejected', 'stale', 'superseded')
                      and not exists (
                        select 1 from clause_translations t2
                        where t2.clause_id = k.id and t2.id <> t.id and t2.is_current
                          and t2.quality_check_passed and t2.review_status not in ('translation_failed', 'not_translated', 'rejected', 'stale', 'superseded')
                      )
                    order by (t.review_status = 'approved') desc, t.updated_at desc limit 1)
                   from clauses k join document_versions v on v.id = k.version_id join documents d on d.id = v.document_id
                   where k.id = any(cast(:ids as uuid[]))
                     and d.relevance not in ('irrelevant', 'out_of_scope')
                     and v.legal_status not in ('repealed', 'draft')
                     and v.review_status <> 'rejected'
                     and not exists (
                       select 1 from knowledge_change_proposals p
                       where p.target_type = 'documents' and p.target_id = d.id
                         and p.status in ('pending_review', 'approved')
                         and coalesce((p.payload->>'safety_hold')::boolean, false)
                     )
                     and not exists (
                       select 1 from knowledge_change_proposals p
                       where p.target_type = 'clauses'
                         and p.status in ('pending_review', 'approved')
                         and p.payload->'source_parse_revision'->>'scope' = 'whole_source_page'
                         and (
                           p.target_id = k.id
                           or (
                             p.payload->'source_parse_revision'->>'version_id' = k.version_id::text
                             and (p.payload->'source_parse_revision'->>'page_number')::int >= k.page_start
                             and (p.payload->'source_parse_revision'->>'page_number')::int <= k.page_end
                           )
                         )
                     )"""
            ),
            {"ids": ids},
        )
    ).all()
    out = {}
    from app.analysis.clause_gate import law_name as one_name

    for cid, number, title, original, did, zh in rows:
        title = one_name(title, did)
        label = article_label(number or "")
        # 公报页眉、网页导航、地址电话、下一章标题、译文里的阿拉伯文残片：引用时去掉（与合规文件、知识库同一套）
        zh_text = _glyphs(for_reading((zh or "").strip()))
        from app.analysis.clause_gate import why_unusable

        verified = (
            bool(zh_text)
            and not why_unusable(zh_text, title, original, forms_ok=True)
            and bool(re.search(r"第[0-9一二三四五六七八九十百]+(?:[.\-][0-9]+)*(?:条|段|节|款|项|章|篇|部分)|单一条款", label))
        )
        out[cid] = {"source": f"《{law_name(title)}》{label}", "zh": zh_text, "original": _glyphs(" ".join(for_reading(original or "").split())), "verified": verified}
    return out


_UNCLEAR = re.compile(r"OCR|识别不清|原文不清|字迹不清|（不清）")


def _glyphs(text: str) -> str:
    return re.sub(r"[\uE000-\uF8FF]", "•", text)


def _tighten_zh_spaces(text: str) -> str:
    """Remove redundant ASCII half-width spaces around Chinese characters while preserving full-width Ideographic spaces."""
    if not text:
        return text
    s = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])", "", text)
    s = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[，。；：、！？）》】])", "", s)
    s = re.sub(r"(?<=[（《【])[ \t]+(?=[\u4e00-\u9fff])", "", s)
    s = re.sub(r"(?<=[）》】\)])[ \t]+(?=[\u4e00-\u9fff，。；：、！？])", "", s)
    s = re.sub(r"(?<=[向在由从对与及或按报经至为])[ \t]+(?=[A-Za-z0-9\u4e00-\u9fff])", "", s)
    return s


def law_name(title: str) -> str:
    """A law's name as shown: the Chinese name, without notes on where the text came from."""
    from app.analysis.clause_gate import law_name as one_name

    return one_name(re.sub(r"[（(](英文|法文|阿拉伯文)?(官方)?(译本|版本|英译本|文本)[）)]", "", title or "").strip())


def _cut(text: str, limit: int) -> str:
    from app.analysis.clause_gate import (
        _close_delims_before_ellipsis,
        _strip_dangling_tail,
        find_clean_sentence_cut,
    )

    if len(text) <= limit:
        return text
    cut_end = find_clean_sentence_cut(text, limit)
    if cut_end > 0:
        clean = _strip_dangling_tail(text[:cut_end]).rstrip(" 、；;，,：:—–-/\n\t")
        if clean:
            return _close_delims_before_ellipsis(clean) + "……"
    end = limit
    if end < len(text) and text[end - 1].isascii() and text[end].isascii():
        while end > limit - 24 and text[end - 1].isalnum():
            end -= 1
    clean_fb = _strip_dangling_tail(text[:end]).rstrip(" 、；;，,：:—–-/\n\t")
    return _close_delims_before_ellipsis(clean_fb or text[:end].rstrip()) + "……"


async def render_project(
    session: AsyncSession,
    project: AdmissionProject,
    entry: AdmissionEntry,
    detail: dict,
    task=None,
    profile: dict[str, str] | None = None,
    forms: list[str] | None = None,
) -> tuple[Rendered, str]:
    """(rendered document, name). ``detail``: the item as the page shows it (steps, authority,
    portal, card facts); ``profile``: company facts given in any official form; ``forms``: names of
    the item's official forms."""
    forms = forms or []
    known = {**{k: v for k, v in (profile or {}).items() if v}, **{k: v for k, v in project_facts(project, entry, task).items() if v}}
    answers = dict(project.facts or {})
    out = Rendered()
    asked: set[str] = set()

    def value(key: str) -> str:
        return str(answers.get(DOC_FACT_PREFIX + key) or known.get(DOC_FACT_PREFIX + key) or known.get(key) or "").strip()

    def ask(key: str, label: str, question: str, why: str) -> None:
        if key not in asked:
            asked.add(key)
            out.missing.append(Question(key, label, question, why, True))

    facts = detail.get("profile") or {}
    eligibility = known_value(facts, "eligibility")
    law_steps = law_steps_of(detail)
    plan = plan_of(entry, [m for st in law_steps for m in st.get("materials") or []])
    who = applicant_label(entry)
    title = doc_name(entry)
    # ===== ①对外申请文书：递交给受理机构的那一份。不出现平台、本文件、企业递交时自带、在哪递交这类内部说法 =====
    lang = known_value(facts, "language").removeprefix("按官方表格：")
    foreign = bool(lang) and not re.search(r"中文|汉语", lang)
    lang_clean = re.sub(r"[（(][^）)]*[）)]", "", lang).strip() if lang else ""
    lang_main = re.split(r"[；;，,/、]|或|和|及", lang_clean)[0].strip() if lang_clean else ""
    item_name = re.sub(r"(申请办理|申请|办理)$", "", entry.name_zh) or entry.name_zh
    letter_title = f"{item_name}申请书"
    out.blocks.append({"type": "title", "text": letter_title})
    if foreign:
        out.blocks.append({"type": "paragraph", "text": f"受理机构要求以{lang_main}提交：本稿供企业核对内容，递交前译成{lang_main}正式稿，中文稿作对照。"})
    from app.analysis.doc_sections import addressee

    to = addressee(detail)
    if to:
        out.blocks.append({"type": "paragraph", "text": f"致：{to}"})

    from app.analysis.doc_rules import entity_for

    applicant = entity_for(value("applicant"), entry.country_code)
    if not applicant and not (eligibility and LOCAL_APPLICANT.search(eligibility)):
        applicant = entity_for(value("company_name"), entry.country_code)  # 别国公司不代填
    if not usable_value(applicant):
        applicant = ""
        ask("applicant", who, ("由哪家当地法人（或授权代表）提出申请？请写全称" if eligibility and LOCAL_APPLICANT.search(eligibility) else "由哪家企业提出申请？请写全称") + (f"（申请条件：{eligibility}）" if eligibility else ""),
            "申请书要写明申请人" + ("，这一事项对申请人有资格要求" if eligibility else "") + "。")
    rows = [["申请人名称", applicant or "【待补充：当地法人或授权代表】"]]
    for key in applicant_fields(entry, detail):
        v = value(key)
        if not usable_value(v):
            v = ""
            f = info_fact(key)
            ask(key, FIELD_LABELS.get(key, f["label"]), f["question"], "申请书要写明申请人的这一项。")
        rows.append([FIELD_LABELS.get(key) or info_fact(key)["label"], v or BLANK])
    if entry.category in ("vehicle_approval", "telecom_approval", "av_testing"):
        vehicle = value("vehicle_model")
        if not usable_value(vehicle):
            vehicle = ""
            ask("vehicle_model", "车型或设备型号", "这次申请涉及哪些车型（或设备型号）？", "申请书要写明申请对象。")
        rows.append(["申请对象（车型或设备型号）", vehicle or BLANK])
    out.blocks.append({"type": "heading", "text": "一、申请人"})
    out.blocks.append({"type": "table", "head": ["事项", "内容"], "rows": rows})

    clause_of = {}
    for st in law_steps:
        for m in st.get("materials") or []:
            clause_of.setdefault(m, st.get("clause_id"))
    ids = [detail.get("basis", {}).get("clause_id") if detail.get("basis") else None, *(st.get("clause_id") for st in law_steps)]
    clauses = await clause_texts(session, [i for i in ids if i])
    basis_id = str((detail.get("basis") or {}).get("clause_id") or "")
    basis = clauses.get(basis_id) if basis_id else None
    by = (
        f"依据{basis['source']}，"
        if basis
        and basis.get("verified")
        and re.search(r"第[0-9一二三四五六七八九十百]+(?:[.\-][0-9]+)*(?:条|段|节|款|项|章|篇|部分)", basis["source"])
        else ""
    )
    out.blocks.append({"type": "heading", "text": "二、申请事项"})
    out.blocks.append({"type": "paragraph", "text": f"{by}申请人向贵机构申请办理{item_name}。"})

    # 法规要求申请人说明的事项（由企业填写的信息写成）
    described = _describe_rows(plan, known, answers, asked, out)
    n = 3
    step_titles = [str(st.get("title_zh") or "").strip() for st in law_steps if str(st.get("title_zh") or "").strip()]
    target_obj = value("vehicle_model") if entry.category in ("vehicle_approval", "telecom_approval", "av_testing") else ""
    out.blocks.append({"type": "heading", "text": f"{_chinese_no(n)}、申请背景与合规符合性说明"})
    bg_subject = applicant or "申请人"
    bg_obj = f"围绕所涉型号（{target_obj}）" if usable_value(target_obj) else ""
    bg_elig = f"申请人已对照法定主体资格要求完成逐项核实（{eligibility.rstrip('。；; ')}），" if eligibility else ""
    bg_steps = f"并已对照法定审查环节（{'、'.join(step_titles[:4])}）完成内部合规自查与技术卷宗核验" if step_titles else "并已完成属地技术规范与合规内控核验"
    cat_focus = {
        "vehicle_approval": "整车及关键零部件安全结构、车载电子架构与软件配置管理（含车型法规一致性控制）",
        "telecom_approval": "车载无线电通信终端射频参数、电磁兼容指标、频段发射限值及通信接口安全",
        "data_filing": "个人数据处理目的限制、最小必要范围、全生命周期加密访问控制、留存销毁及数据主体权利保障机制",
        "av_testing": "自动驾驶测试车辆安全冗余设计、车载数据记录装置留存、测试道路风险管控及专职安全员值守保障",
    }.get(entry.category, "产品技术安全、通信规范及属地数据处理内控各环节")
    out.blocks.append(
        {
            "type": "paragraph",
            "text": f"{bg_subject}{bg_obj}拟在贵机构管辖区域内依法开展与{item_name}相关的市场准入与业务运营活动。{bg_elig}{bg_steps}，确保申请对象在{cat_focus}等方面全面符合现行有效法规之强制性标准。",
        }
    )
    out.blocks.append(
        {
            "type": "paragraph",
            "text": f"{by}申请人谨此按贵机构制定的官方格式与法定程序正式呈交本次{item_name}申请书及全套随附证明文件，恳请贵机构依法予以审查核准；获准后，申请人承诺严格在核准载明的型号与业务范围内合规运营，并持续配合贵机构监督核查。",
        }
    )
    n += 1
    if described:
        out.blocks.append({"type": "heading", "text": f"{_chinese_no(n)}、申请说明"})
        out.blocks.append({"type": "table", "head": ["事项", "说明"], "rows": described})
        n += 1
    enclosed = [
        p["name"]
        for p in plan
        if p.get("kind") in ("original", "form")
        and p.get("kind") != "none"
        and not re.fullmatch(r"(申请|签署|填表|提交|落款|盖章)?日期", _clean_name(p["name"]))
    ]
    enclosed += [f"《{f}》" for f in forms if not any(p.get("kind") == "form" for p in plan)]
    said = {r[0] for r in described}  # 申请书各节不重复：已在“申请说明”写了的不再列为随附材料
    enclosed = [x for x in dict.fromkeys(_clean_name(x) for x in enclosed) if x not in said]
    if enclosed:
        out.blocks.append({"type": "heading", "text": f"{_chinese_no(n)}、随附材料"})
        out.blocks.append({"type": "table", "head": ["序号", "材料"], "rows": [[str(i), x] for i, x in enumerate(enclosed, 1)]})
        n += 1
    out.blocks.append({"type": "heading", "text": f"{_chinese_no(n)}、声明"})
    out.blocks.append({"type": "paragraph", "text": "申请人保证本申请书及随附材料内容真实、完整、有效，如有不实，愿意承担相应法律责任。"})
    out.blocks.append({"type": "table", "head": ["", ""], "rows": [["申请人（盖章）", ""], ["法定代表人或授权代表（签字）", ""], ["日期", "　　　　年　　月　　日"]]})

    # ===== ②办理指引：给企业自己看的，去哪交、怎么准备、递交清单、办理路线、所依据的条文 =====
    out.blocks.append({"type": "pagebreak"})
    out.blocks.append({"type": "title", "text": f"{item_name}办理指引"})
    from app.analysis.doc_rules import admission_gate

    sometimes = admission_gate(entry)
    if sometimes:  # 不是每个项目都要办的事项：写在办理指引的最前面
        out.blocks.append({"type": "paragraph", "text": f"视情况办理：{sometimes}。符合情形再按下面的办法办理。"})
    guide = 1

    def head(text: str) -> None:
        nonlocal guide
        out.blocks.append({"type": "heading", "text": f"{_chinese_no(guide)}、{text}"})
        guide += 1

    head("受理机构与递交要求")
    where = [["受理机构", official_authority(detail) or "条文未写明受理机构，递交前向主管部门确认"]]
    portal = detail.get("portal")
    if portal:
        where.append(["递交入口", "；".join([f"官网 {portal['site']}", *(f"{x['label']} {x['url']}" for x in portal.get("links") or [])])])
    if eligibility:
        where.append(["申请人资格", eligibility])
    lang_adj = "英文" if lang_main == "英语" else lang_main
    where.append(
        [
            "申请语言",
            (
                (
                    lang
                    if re.search(r"须|附|译本|译文|；", lang)
                    else f"{lang}；非{lang_adj}证明材料须附具{lang_adj}正式译本"
                )
                if lang
                else "条文没有写明，递交前向受理机构确认"
            ),
        ]
    )
    out.blocks.append({"type": "table", "head": ["事项", "内容"], "rows": where})

    head("材料准备方式")
    how = {"info": "写进申请书", "form": "填写官方表格", "application": "申请书", "original": "企业递交时自带"}
    mats = []
    for p in plan:
        if p.get("kind") == "none":
            continue
        prep = how.get(p["kind"], "企业递交时自带") + (f"（{p['what']}）" if p["kind"] == "original" and p.get("what") else "")
        c = clauses.get(str(clause_of.get(p["name"]) or ""), {})
        mats.append([_clean_name(p["name"]), prep, c.get("source", "—") if c.get("verified") else "—"])
    mats += [[f"官方表格《{name}》", "填写官方表格，签字盖章", "—"] for name in forms if not any(p.get("kind") == "form" for p in plan)]
    if mats:
        out.blocks.append({"type": "table", "head": ["材料名称", "准备方式", "法规依据"], "rows": mats})
    else:
        out.blocks.append({"type": "paragraph", "text": "条文没有列出具体材料；递交前可向受理机构确认要附哪些材料。"})

    head("递交清单")
    rows = checklist_rows(entry, plan, forms, "本文件第一部分（申请书）")
    out.blocks.append({"type": "table", "head": ["序号", "材料名称", "准备方式"], "rows": [[str(i), *r] for i, r in enumerate(rows, 1)]})

    head("办理路线")
    steps_ = [t.split("：", 1) if "：" in t[:24] else ["", t] for t in route_steps(official_detail(detail), forms, for_file=True)]
    out.blocks.append({"type": "table", "head": ["步骤", "办理环节", "要求说明"], "rows": [[str(k), a or b.rstrip("。"), b if a else ""] for k, (a, b) in enumerate(steps_, 1)]})

    # 所依据的条文：每条只出现一次，中文在前、原文在后；没过核验或识别不清的只列条号
    shown_ids = [i for i in dict.fromkeys(str(i) for i in ids if i) if clauses.get(i)]
    # 没关联到库里条文的环节：列它摘自条文的原话
    loose = [q for q in dict.fromkeys(" ".join((st.get("evidence_quote") or "").split()) for st in law_steps if not clauses.get(str(st.get("clause_id") or ""))) if q]
    if shown_ids or loose:
        head("参考条文")
        for q in loose:
            out.blocks.append({"type": "paragraph", "text": "原文：" + _cut(q, 700), "original": True})
        seen_unverified: set[str] = set()
        for cid in shown_ids:
            c = clauses[cid]
            if c.get("verified") and c["zh"]:
                from app.analysis.clause_gate import strip_own_label

                label = re.sub(r"^《[^》]*》", "", c["source"])
                out.blocks.append({"type": "paragraph", "text": c["source"] + "：" + _cut(strip_own_label(label, c["zh"]), 700)})
                out.blocks.append({"type": "paragraph", "text": "原文：" + _cut(c["original"], 700), "original": True})
            elif c["source"] not in seen_unverified:
                seen_unverified.add(c["source"])
                out.blocks.append({"type": "paragraph", "text": c["source"] + "：以受理机构官方发布原件为准。"})
    for blk in out.blocks:
        if blk.get("original"):
            continue
        if isinstance(blk.get("text"), str):
            blk["text"] = _tighten_zh_spaces(blk["text"])
        if isinstance(blk.get("items"), list):
            blk["items"] = [_tighten_zh_spaces(it) if isinstance(it, str) else it for it in blk["items"]]
        if blk.get("type") == "table" and isinstance(blk.get("rows"), list):
            blk["rows"] = [
                [_tighten_zh_spaces(cell) if isinstance(cell, str) else cell for cell in row]
                for row in blk["rows"]
            ]
    return out, _tighten_zh_spaces(title)


BLANK = "【待补充】"
FIELD_LABELS = {
    "company_name": "申请人名称", "company_address": "地址", "legal_rep_name": "法定代表人", "legal_rep_title": "法定代表人职务",
    "company_reg_no": "商业登记号", "company_tax_no": "税务登记号", "company_phone": "电话", "company_email": "电子邮箱",
    "contact_email": "联系人电子邮箱", "local_rep_phone": "当地代理人电话", "dpo_name": "数据保护负责人",
}
# 没有官方申请表时，按事项类型列申请人的栏目
_DEFAULT_FIELDS = {
    "data_filing": ["company_address", "legal_rep_name", "company_phone", "company_email", "dpo_name"],
}
_PROMPT = re.compile(r"请(写|填|填写|输入|说明|提供)|？|\?|演示填写|^（?须为|待补充|待企业填写|例如|如：")


def usable_value(v: str) -> bool:
    """A value that can go into the application: not a question or its hint copied into the
    answer (“演示填写：由哪家企业提出申请？请写全称（须为：拟从”), not cut off half way, not a
    placeholder (“代表人”“ZW”)."""
    v = (v or "").strip()
    if not v or _PROMPT.search(v):
        return False
    if v in ("代表人", "负责人", "法人", "法定代表人", "无", "暂无", "待定") or re.fullmatch(r"[A-Za-z]{1,3}", v):
        return False
    return v.count("（") <= v.count("）") and v.count("(") <= v.count(")")


def applicant_fields(entry: AdmissionEntry, detail: dict) -> list[str]:
    """The applicant's particulars this item asks for: the fields of its own official application
    form (in the form's order), else by the kind of item."""
    from app.forms.service import get_form

    keys: list[str] = []
    for f in detail.get("forms") or []:
        form = get_form(f.get("code", "")) or {}
        for section in form.get("sections", []):
            for item in section.get("items", []):
                k = item.get("fact")
                if k and item.get("kind") == "text" and k != "company_name" and k in FIELD_LABELS:
                    keys.append(k)
    if keys:
        return list(dict.fromkeys(keys))
    return _DEFAULT_FIELDS.get(entry.category, ["company_address", "legal_rep_name", "company_reg_no", "company_phone", "company_email"])


_PHASE_PAIRS = (("使用", "试验"),)


def law_steps_of(detail: dict) -> list[dict]:
    """The steps the law lays down for this item. Steps of the other phase are left out: an item
    for “use” (道路使用授权) does not carry the steps of the “trial” item it follows."""
    if detail.get("steps_generic"):
        return []
    steps = list(detail.get("steps") or [])
    name = str(detail.get("name_zh") or "")
    for mine, other in _PHASE_PAIRS:
        if mine in name and other not in name:
            steps = [st for st in steps if other not in str(st.get("title_zh") or "")]
    return steps


def official_detail(detail: dict) -> dict:
    """The item as a filed document names it: the receiving body without the platform's notes."""
    return {**detail, "authority_zh": official_authority(detail), "authority_unnamed": False, "authority_hint": ""}


def official_authority(detail: dict) -> str:
    """The receiving body's name for the letter's address line, without the platform's notes."""
    name = detail.get("authority_zh") if detail.get("authority_zh") and not detail.get("authority_unnamed") else (detail.get("authority_hint") or "")
    return re.sub(r"[（(]推断[^）)]*[）)]", "", name or "").strip()


def _clean_name(name: str) -> str:
    s = re.sub(r"[（(][^）)]*[؀-ۿ][^）)]*[）)]", "", name or "").strip() or (name or "")
    # 消除中文材料名后重复或嵌套的外文括注（含中间夹有“（原件）”等括注的情形）
    m_abbr = re.search(r"[（(]([A-Za-z0-9/\-]{2,16})[）)]", s)
    if m_abbr:
        abbr = m_abbr.group(1)
        tail_after = s[m_abbr.end() :]
        for m_tail in list(re.finditer(r"[（(](?:[^（）()]|\([^()]*\)|（[^（）]*）)+[）)]", tail_after)):
            inner = m_tail.group(0)[1:-1]
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(abbr)}(?![A-Za-z0-9])", inner, re.IGNORECASE):
                cut_s = m_abbr.end() + m_tail.start()
                cut_e = m_abbr.end() + m_tail.end()
                s = (s[:cut_s].rstrip() + s[cut_e:]).strip()
                break
    s = re.sub(r"([（(][^（）()]{1,40}[）)])\s*\1", r"\1", s)
    return s.strip() or (name or "")


def _describe_rows(plan: list[dict], known: dict[str, str], answers: dict[str, str], asked: set[str], rendered: Rendered) -> list[list[str]]:
    """The descriptions the law asks the applicant to give, from the information the enterprise
    gives once; what is missing becomes a question."""
    rows = []
    for p in plan:
        if p.get("kind") != "info" or not p.get("fact"):
            continue
        cleaned_label = _clean_name(p["name"])
        if p.get("fact") == "sign_date" or re.fullmatch(r"(申请|签署|填表|提交|落款|盖章)?日期", cleaned_label):
            continue
        parts = []
        keys = p["fact"].split(",")
        for key in keys:
            v = str(answers.get(DOC_FACT_PREFIX + key) or known.get(DOC_FACT_PREFIX + key) or known.get(key) or "").strip()
            f = info_fact(key)
            if usable_value(v):
                parts.append(v if len(keys) == 1 else f"{f['label']}：{v}")
            elif key not in asked:
                asked.add(key)
                rendered.missing.append(Question(key, f["label"], f["question"], f.get("why", ""), bool(f.get("short"))))
        rows.append([_clean_name(p["name"]), "；".join(parts) or BLANK])
    return rows


def authority_line(detail: dict) -> str:
    """The receiving body as the page shows it; an inferred one says so and on what basis."""
    name = detail.get("authority_zh") or ""
    note = detail.get("authority_note") or ""
    if name and not detail.get("authority_unnamed"):
        return name + (f"（推断：{note}）" if note else "")
    hint = detail.get("authority_hint") or ""
    if hint:
        return f"{hint}（推断：条文未写明受理机构，取所依据法规点名的主管机构，递交前可向其确认）"
    return "条文未写明，递交前向该国主管部门确认"


def route_steps(detail: dict, forms: list[str], for_file: bool = False) -> list[str]:
    """The route in order: what the law lays down, the official forms, where to file, what comes back."""
    steps = law_steps_of(detail)
    out = [f"{st['title_zh']}：{st['detail_zh']}".rstrip("：") for st in steps]
    if not out:
        out.append("准备材料：准备申请书和企业递交时自带的文件。")
    if forms:
        out.append("填写官方表格：" + "、".join(f"《{f}》" for f in forms) + "，签字盖章。")
    name = detail.get("authority_zh") if detail.get("authority_zh") and not detail.get("authority_unnamed") else (detail.get("authority_hint") or "受理机构")
    portal = detail.get("portal")
    where = ""
    if portal:
        where = "，递交入口为" + "、".join([f"官网 {portal['site']}", *(f"{x['label']} {x['url']}" for x in portal.get("links") or [])])  # 不用冒号：步骤表按冒号分“做什么”和“说明”
    out.append(f"向{name}递交{where}。")
    validity = known_value(detail.get("profile") or {}, "validity")
    if for_file:  # 下载的文件只写办理本身；在平台登记批文是页面上的提示
        waiting = any(re.search(r"等待.{0,6}(审查|结果|受理)|审查结果", x[:14]) for x in out[:-1])  # 法规环节里已有“等待审查结果”的，不再重复一步
        if not waiting or validity:
            out.append("等待受理机构出具结果" + (f"；批文有效期：{validity}" if validity else "") + "。")
    else:
        out.append("等待受理；取得批文后在平台登记编号和有效期" + (f"（有效期：{validity}）" if validity else "") + "，到期前会提醒。")
    return [_tighten_zh_spaces(x) for x in out]


def _chinese_no(n: int) -> str:
    return "一二三四五六七八九十"[n - 1] if 1 <= n <= 10 else str(n)


def next_heading(rendered_blocks: list[dict]) -> str:
    """The numeral of the next top-level heading (“六、”), counting those already there."""
    # 一份下载件里有两部分（申请书、办理指引）：只数最后一部分（最后一个标题之后）的章节
    last = max((i for i, b in enumerate(rendered_blocks) if b.get("type") == "title"), default=-1)
    n = sum(1 for b in rendered_blocks[last + 1:] if b.get("type") == "heading" and re.match(r"^[一二三四五六七八九十]+、", b.get("text", "")))
    return _chinese_no(n + 1) + "、"


def info_fact(key: str) -> dict:
    """The question for an information item an admission material needs: the one the compliance
    documents already ask, else an admission-only item, else the official forms' label."""
    from app.analysis.document_templates_forms import FORM_TEMPLATES

    for _, _, definition in FORM_TEMPLATES:
        for f in definition.get("facts", []):
            if f["key"] == key:
                return f
    if key in NEW_FACTS:
        return {"key": key, **NEW_FACTS[key]}
    label = fact_registry().get(key, key)
    return {"key": key, "label": label, "question": f"请填写{label}", "why": "申请材料需要写明这一项。", "short": True}


def _describe(rendered: Rendered, plan: list[dict], known: dict[str, str], answers: dict[str, str], asked: set[str] | None = None) -> None:
    """The descriptions the law lists as materials, written from information the enterprise gives
    once: what is already known (assessment, official forms, this project) is used, what is not
    becomes one more question here."""
    rows = []
    asked = asked if asked is not None else {q.key for q in rendered.missing}
    for p in plan:
        if p.get("kind") != "info" or not p.get("fact"):
            continue
        parts = []
        for key in p["fact"].split(","):
            value = str(answers.get(DOC_FACT_PREFIX + key) or known.get(DOC_FACT_PREFIX + key) or known.get(key) or "").strip()
            f = info_fact(key)
            if value:
                parts.append(value if len(p["fact"].split(",")) == 1 else f"{f['label']}：{value}")
            elif key not in asked:
                asked.add(key)
                rendered.missing.append(Question(key, f["label"], f["question"], f.get("why", ""), bool(f.get("short"))))
        rows.append([p["name"], "；".join(parts) or "待补充"])
    rendered.blocks.append({"type": "heading", "text": next_heading(rendered.blocks) + "法规要求说明的事项"})
    if rows:
        rendered.blocks.append({"type": "table", "head": ["事项", "说明"], "rows": rows})
    else:
        rendered.blocks.append({"type": "paragraph", "text": "条文没有要求另作说明。"})


def bring_list(plan: list[dict], forms: list[str]) -> list[dict]:
    """What the enterprise still brings to the authority itself: the originals the law lists (and
    the authority's own forms when none of them is filled on the platform), with what kind is
    required when the law says it."""
    out, seen = [], set()
    for p in plan:
        if p.get("kind") == "original" or (p.get("kind") == "form" and not forms):
            key = _bare(p["name"])
            if key in seen:  # 同一份材料，条文前后各提了一次（“银行保函原件”“银行保函”）
                continue
            seen.add(key)
            out.append({"name": _clean_name(p["name"]), "what": p.get("what") or ""})
    return out


def _bare(name: str) -> str:
    return re.sub(r"（[^）]*）|\([^)]*\)|原件|正本|副本|复印件|[\s，、。]", "", name)


def checklist_rows(entry: AdmissionEntry, plan: list[dict], forms: list[str], self_name: str = "本文件") -> list[list[str]]:
    """The filing checklist, in the order the law lists the materials: this application, the
    official forms, then the originals the enterprise brings — [material, how it is prepared].
    The project page and the downloaded material show the same list."""
    rows = [[doc_name(entry), self_name]]
    rows += [[f"官方表格《{name}》", "填写官方表格，签字盖章" if self_name.startswith("本文件") else "平台代填，打印后签字盖章"] for name in forms]
    rows += [[b["name"], "企业递交时自带" + (f"：{b['what']}" if b["what"] else "")] for b in bring_list(plan, forms)]
    return rows


def _checklist(rendered: Rendered, entry: AdmissionEntry, plan: list[dict], forms: list[str]) -> None:
    rows = checklist_rows(entry, plan, forms)
    rendered.blocks.append({"type": "heading", "text": next_heading(rendered.blocks) + "递交清单"})
    rendered.blocks.append({"type": "table", "head": ["序号", "材料", "怎么准备"], "rows": [[str(i), *r] for i, r in enumerate(rows, 1)]})


def clean_answers(raw: dict[str, str], allowed: set[str]) -> dict[str, str]:
    """Only answers to questions the template really asks, trimmed and bounded."""
    out = {}
    for key, value in raw.items():
        if key in allowed:
            text = str(value or "").strip()[:MAX_ANSWER_CHARS]
            if text:
                out[DOC_FACT_PREFIX + key] = text
    return out
