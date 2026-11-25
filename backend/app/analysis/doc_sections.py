# ruff: noqa: E501  文件正文是长句
"""The parts of a compliance document the platform writes itself (not the enterprise's answers,
not a table of clauses), the same way for every country and direction:

* ``route``: the transfer route under the sending country's law (``doc_rules.route``);
* the remediation report's issue list, built by rule — the transfer route's obligations, the
  licences the target country requires for this business, the key facts still missing, and the
  problems the enterprise listed — and its route (what to do, with whom, what comes out, how long);
* the risk report's recommendations, from the same issues;
* the market-admission report (05), item by item, written like the admission application material
  (``app.admission.engine``): materials with how each is prepared and its clause, the filing
  requirements, the route, and every clause once, Chinese first.

Each is a list of blocks a template puts in place with ``{"type": "slot", "name": ...}``.
"""

from __future__ import annotations

import copy
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import clause_gate, doc_rules
from app.analysis.document_basis import CN, NAMES
from app.analysis.document_templates import DOC_FACT_PREFIX, DRAFT_BLANK

_RECEIVING_LAW_CACHE: dict[str, tuple[list[list[str]], list[dict]]] = {}

# 文件里要写明、企业还没写的关键事实：问题与建议（与项目里的问法一致）
_KEY_FACTS = [
    ("recipient", "境外接收方的名称、地址和负责人还没写明", "确定接收方并写明其名称、地址、负责人，与其签订数据传输合同"),
    ("consent_and_notice", "向车主、乘客告知并取得同意的安排还没写明", "制定隐私告知，按数据发出国的数据保护法取得同意（敏感信息单独同意）并留存记录"),
    ("retention_and_blackbox", "数据在接收方的保存期限、存放位置和到期处理还没写明", "确定保存期限、存放位置和到期删除方式，写进与接收方的合同"),
    ("risk_measures", "已采取的安全保护措施还没写明", "落实传输加密、访问控制、日志审计和脱敏措施，并留存记录"),
    ("data_types", "传输的数据类型还没写明", "梳理出境数据清单（数据项、用途、是否含个人信息或敏感个人信息）"),
]
_CN_ROUTE_DUTY = {
    "security_assessment": ("须申报数据出境安全评估", "完成数据出境风险自评估后，通过所在地省级网信部门向国家网信办申报", "所在地省级网信部门（报国家网信办）", "评估结果（有效期 3 年）",
                            "国家网信部门自发出书面受理通知书之日起 45 个工作日内完成评估，情况复杂的可延长", "《促进和规范数据跨境流动规定》第七条；《数据出境安全评估办法》第十二条"),
    "standard_contract": ("须订立个人信息出境标准合同并备案", "按国家网信办范本与境外接收方订立标准合同，完成个人信息保护影响评估，向所在地省级网信部门备案", "所在地省级网信部门", "备案材料与备案结果",
                          "合同生效之日起 10 个工作日内备案", "《促进和规范数据跨境流动规定》第八条；《个人信息出境标准合同办法》第七条"),
}


def answered(task) -> dict[str, str]:
    """The answers given anywhere in the project (each question is answered once per project)."""
    return {
        k.removeprefix(DOC_FACT_PREFIX): str(v).strip()
        for k, v in (task.facts_json or {}).items()
        if isinstance(v, str) and v.strip() and not v.strip().startswith("暂不确定")
    }


async def admission_entries(session: AsyncSession, task, country: str | None) -> list:
    """The admission items the target country requires for this business (the same rule as the
    admission page and ``_fill_admission``)."""
    if not country:
        return []
    from app.admission import plan
    from app.admission.models import AdmissionEntry

    rows = (
        await session.scalars(
            select(AdmissionEntry)
            .where(AdmissionEntry.country_code == country, AdmissionEntry.review_status != "out_of_scope", AdmissionEntry.merged_into.is_(None))
            .order_by(AdmissionEntry.category, AdmissionEntry.name_zh)
        )
    ).all()
    from app.admission.router import entry_listed
    from app.analysis.documents_router import task_functions

    rows = [r for r in rows if await entry_listed(session, r)]  # 资料不齐的不对用户展示（与准入页同一口径）
    fns = task_functions(task)
    needed = [
        r
        for r in rows
        if (
            any(plan.applies(getattr(r, "category", ""), fn, getattr(r, "functions", None), r.name_zh) == "needed" for fn in fns)
            if fns
            else plan.applies(getattr(r, "category", ""), "", getattr(r, "functions", None), r.name_zh) == "needed"
        )
    ]
    return needed or list(rows)


def _applicant(entry, company: str = "", receiving: str = "") -> str:
    """Who files the item, as a kind of entity (its conditions are a column of their own); a data
    item in the receiving country is filed by the receiver there."""
    if receiving and getattr(entry, "category", "") == "data_filing":
        return f"{receiving}接收方（数据控制者）"
    return doc_rules.applicant_type(entry)


def _conditions(entry) -> str:
    from app.admission.engine import known_value

    return known_value((entry.profile or {}), "eligibility") or "条文未写明"


async def issues(session: AsyncSession, task, values: dict[str, str], route: dict) -> list[dict]:
    """The issue list by rule: {problem, basis, advice, to, output, time}."""
    facts = {**answered(task), **{k: v for k, v in values.items() if v}}
    source, recipient = doc_rules.ends(values, task.country_code)
    out: list[dict] = []
    r = route.get("route")
    if source == CN and r in _CN_ROUTE_DUTY:
        problem, advice, to, output, time, basis = _CN_ROUTE_DUTY[r]
        out.append({"problem": problem, "basis": basis, "advice": advice, "to": to, "output": output, "time": time})
    elif source == CN and r == "unknown":
        out.append({"problem": "出境路径还不能判断", "basis": "《促进和规范数据跨境流动规定》第七条、第八条", "advice": route["text"].split("：", 1)[-1], "to": "—", "output": "确定出境路径", "time": "—"})
    elif r == "exception":
        out.append({"problem": f"向{NAMES.get(recipient or '', '接收国')}传输须满足{NAMES.get(source or '', '')}法律规定的条件", "basis": route.get("cite") or "—", "advice": _how(route["text"]), "to": await main_regulator(session, source) or f"{NAMES.get(source or '', '')}数据保护主管机构", "output": "同意记录或传输授权", "time": "以主管机构通知为准"})
    target = doc_rules.overseas(source, recipient)
    entries = [e for e in doc_rules.inbound_only(await admission_entries(session, task, target), source, recipient, target, facts) if not doc_rules.admission_gate(e)][:6]
    if any(x["problem"].startswith("向") and "传输须满足" in x["problem"] for x in out):
        transfer = [e for e in entries if doc_rules._ONWARD.search(e.name_zh)]
        if transfer:  # 同一件事：出境路径那一步就是办这项授权，向它的受理机构递交
            for x in out:
                if "传输须满足" in x["problem"]:
                    mark = _permit_option(x["advice"])
                    x["advice"] += f"；{'选' + mark + '时' if mark else ''}办理“{transfer[0].name_zh}”"
                    x["to"] = transfer[0].authority_zh or x["to"]
            entries = [e for e in entries if e not in transfer]
    if entries:
        from app.knowledge.whitepaper_graph import UNNAMED_ROLE, authority_resolver

        resolve = await authority_resolver(session, target)
    for e in entries:
        to = resolve(e.authority_zh or "") or ""
        if UNNAMED_ROLE.search(re.sub(r"[（(][^）)]*[）)]", "", to)):
            to = ""
        out.append({
            "problem": f"须在{NAMES.get(target or '', '')}办理“{e.name_zh}”", "basis": "见准入材料清单", "advice": f"按准入材料清单准备“{e.name_zh}”的材料并递交（申请主体：{_applicant(e, facts.get('company_name', ''), receiving=NAMES.get(target or '', '') if target == recipient and source != recipient else '')}）",
            "to": to if to and not re.search(r"[؀-ۿ]", to) else (addressee_entry(e) or "受理机构（见准入材料清单）"), "output": "批准文件", "time": "以受理机构通知为准",
        })
    for key, problem, advice in _KEY_FACTS:
        if not facts.get(key):
            out.append({"problem": problem, "basis": "—", "advice": advice.replace("数据发出国", NAMES.get(source or "", "数据发出国")), "to": "企业内部", "output": "补齐后的文件", "time": DRAFT_BLANK})
    measure_lines = [x.strip(" ；;。") for x in (facts.get("measures") or "").replace("；\n", "\n").split("\n") if x.strip()]
    for idx_line, line in enumerate([x.strip(" ；;。") for x in (facts.get("issues") or "").replace("；", "\n").split("\n")]):
        if line and line not in ("无", "没有", "暂无"):
            matched_m = next((m for m in measure_lines if (rm := re.match(r"^(?:风险\s*\d+)", line)) and rm.group(0) in m), "")
            adv = matched_m or (measure_lines[idx_line] if idx_line < len(measure_lines) else (facts.get("measures") or DRAFT_BLANK))
            if adv.count("（") > adv.count("）"):
                adv = adv + "）" * (adv.count("（") - adv.count("）"))
            due_val = _extract_due_from_advice(adv) or ("出境申报前完成" if facts.get("measures") else DRAFT_BLANK)
            out.append({"problem": line, "basis": "—", "advice": adv, "to": "企业内部", "output": "整改记录", "time": due_val})
    return out


def _extract_due_from_advice(adv: str) -> str:
    """Extract due-date / completion status after ``完成时限：`` while keeping inner parentheses intact."""
    m = re.search(r"完成时限[：:]\s*", adv or "")
    if not m:
        return ""
    buf: list[str] = []
    depth = 0
    for ch in adv[m.end() :]:
        if ch in ("（", "("):
            depth += 1
            buf.append(ch)
        elif ch in ("）", ")"):
            if depth > 0:
                depth -= 1
                buf.append(ch)
            else:
                break
        elif depth == 0 and ch in (";", "；", "\n", "\r"):
            break
        else:
            buf.append(ch)
    res = "".join(buf).strip()
    if depth > 0:
        res += "）" * depth
    return res


_NON_DPA_NODE_RE = re.compile(
    r"信息系统|法律数据库|法律法规数据库|规范性法律文件|统一法律|中央法律|法规门户|门户网站|公报|统计局|统计所|"
    r"灾难恢复中心|金融中心|法律和司法委员会|欧盟委员会|议会|常设委员会|惩戒委员会|咨询委员会|"
    r"^(?:任何|款所述|本款|上述|前款|保护委员会$|数据保护监管机构$|认证委员会$)"
)
_DPA_PRIORITY_RE = re.compile(
    r"个人数据|个人信息|数据保护|数据隐私|隐私保护|国家隐私|信息与自由|信息专员|信息监管局|"
    r"PDPC|INPDP|CNDP|CNIL|BfDI|ICO|EDPB|Garante|AEPD|UODO|IMY|APD|PPC|PIPC|ANPD|SDAIA|KVKK|NDPC|OPC|PPA|AAIP"
)


async def main_regulator(session: AsyncSession, country: str | None) -> str:
    """The supervisory body the country's laws name most often (its one proper name)."""
    if not country:
        return ""
    from app.knowledge import whitepaper_graph as W

    nodes = await W._regulators(session, country)
    nodes = [
        n
        for n in nodes
        if re.search(r"数据|信息|隐私", n.name_zh or "")
        and not _NON_DPA_NODE_RE.search((n.name_zh or "").strip())
    ]
    nodes.sort(
        key=lambda n: (
            0 if _DPA_PRIORITY_RE.search(n.name_zh or "") else 1,
            -W._MENTIONS.get(n.id, 0),
        )
    )
    return nodes[0].name_zh if nodes else ""


def _permit_option(text: str) -> str:
    """The marker (①②…) of the option in a route text that is applying for an authorisation."""
    for m in re.finditer(r"([①②③④⑤⑥])([^；。]*)", text or ""):
        if re.search(r"授权|许可|批准", m.group(2)):
            return m.group(1)
    return ""


def _only_if_permit(entry, mark: str) -> str:
    """A data-transfer authorisation is only done when the route chosen is applying for it."""
    return f"；出境路径选{mark}（申请授权）时办理" if mark and doc_rules._ONWARD.search(getattr(entry, "name_zh", "") or "") else ""


def _how(text: str) -> str:
    """The ways a route allows, as the advice column reads them: “任选其一：①…；②…”."""
    body = text.split("：", 1)[-1].rstrip("。")
    mark = "任选其一：" if "任选其一" in text else ("须同时满足：" if "同时满足" in text else "")
    return mark + body if mark and not body.startswith(mark[:4]) else body


def issue_blocks(items: list[dict]) -> list[dict]:
    if not items:
        return [{"type": "paragraph", "text": "经对照发出国出境监管规则、海外属地准入条件及企业内控自查记录，当前已填报事实下无待整改事项。"}]
    summary = f"综合法定出境路径前置义务、目标国属地行政许可及企业内控核查结果，共识别出 {len(items)} 项需逐项落实的合规事项，逐项列明规范依据与处置方案如下："
    return [
        {"type": "paragraph", "text": summary},
        {"type": "table", "head": ["序号", "问题", "依据", "整改建议"], "rows": [[str(i), x["problem"], x["basis"], x["advice"]] for i, x in enumerate(items, 1)]},
    ]


def route_blocks(items: list[dict], verified: bool = False) -> list[dict]:
    """The remediation route: one step per issue, in order — what to do, with whom, what comes out, how long."""
    seen: set[str] = set()
    items = [x for x in items if not (x["advice"] in seen or seen.add(x["advice"]))]
    if not items:
        return [{"type": "paragraph", "text": "当前业务状态无需额外执行行政报批或内控整改步骤，维持日常合规审计即可。"}]
    rows = [[str(i), x["advice"], x["to"], x["output"], x["time"]] for i, x in enumerate(items, 1)]
    rows.append([str(len(rows) + 1), "逐项核对整改结果，更新本报告并留存记录", "企业内部", "整改完成记录", "出境申报前持续核验" if verified else DRAFT_BLANK])
    return [
        {"type": "paragraph", "text": f"围绕上述合规事项，统筹设置 {len(rows)} 个顺序衔接的执行步骤，明确每一步的受理机关或内部责任主体、交付物与时限要求："},
        {"type": "table", "head": ["步骤", "整改动作", "责任主体或受理机构", "交付成果", "完成时限"], "rows": rows},
    ]


def advice_blocks(items: list[dict]) -> list[dict]:
    if not items:
        return [{"type": "paragraph", "text": "经核验当前填报事实与安全保障措施，未发现待补齐缺口；后续如遇业务范围调整、出境数据规模跃升或属地法规修订，应及时开展动态复评。"}]
    return [{"type": "list", "items": [f"{x['advice']}（对应事项：{x['problem']}）" for x in items]}]


_ACCESS = re.compile(
    r"(侦查|调查机关|调查人员|司法警察|检察|法院|执法|公安)[^。；]{0,40}(调取|调阅|扣押|获取|访问|查阅|命令[^。；]{0,10}提供)[^。；]{0,20}(数据|信息)"
    r"|(调取|调阅|扣押)[^。；]{0,20}(数据|信息|资料)"
    r"|订明执法机构[^。；]{0,60}(书面授权|披露)[^。；]{0,40}个人数据"
    r"|(委员会|检查员)[^。；]{0,35}书面通知[^。；]{0,35}要求[^。；]{0,30}提供[^。；]{0,25}(文件|信息)"
)
_REMEDY_EXT = re.compile(
    r"(有权|可以|可)[^。；]{0,35}(向[^。；]{0,18}(中心|委员会|上诉委员会|机构|部门|法院)[^。；]{0,12}(投诉|申诉|上诉|提起诉讼|申请审查)|提起诉讼|起诉|请求救济|请求赔偿|要求赔偿)"
    r"|应投诉人的申请[^。；]{0,30}委员会[^。；]{0,15}可审查"
    r"|投诉应向[^。；]{0,15}(中心|委员会|机构)提出"
    r"|在数据控制者拒绝根据利害关系人的请求进行更正时[^。；]{0,30}安排进行有正当理由的更正"
)
_REMEDY = re.compile(r"(有权|可以|可)[^。；]{0,35}(投诉|申诉|举报|提起诉讼|起诉|请求救济|请求赔偿|要求赔偿)|向[^。；]{0,18}(中心|委员会|上诉委员会|机构|部门|法院)[^。；]{0,12}(投诉|申诉|上诉|提起)")
_SUPERVISOR = re.compile(r"(负责本法的实施|根据本条进行调查|发出[^。；]{0,20}指示|施加罚款|支付财务处罚)")


_EU_SUPERVISORS: dict[str, tuple[str, str]] = {
    "FR": (
        "欧盟《通用数据保护条例》（GDPR，Regulation (EU) 2016/679）及法国《信息与自由法》（Loi n° 78-17 Informatique et Libertés）",
        "法国国家信息与自由委员会（CNIL）",
    ),
    "DE": (
        "欧盟《通用数据保护条例》（GDPR，Regulation (EU) 2016/679）及德国《联邦数据保护法》（BDSG）",
        "德国联邦数据保护与信息自由专员（BfDI）及州级数据保护监管机关",
    ),
    "NL": ("欧盟《通用数据保护条例》（GDPR，Regulation (EU) 2016/679）及荷兰《GDPR 实施法》（UAVG）", "荷兰数据保护局（AP）"),
    "IT": ("欧盟《通用数据保护条例》（GDPR，Regulation (EU) 2016/679）及意大利《个人数据保护法典》", "意大利个人数据保护局（Garante）"),
    "ES": ("欧盟《通用数据保护条例》（GDPR，Regulation (EU) 2016/679）及西班牙《个人数据保护与数字权利保障组织法》", "西班牙数据保护局（AEPD）"),
    "SE": ("欧盟《通用数据保护条例》（GDPR，Regulation (EU) 2016/679）", "瑞典隐私保护局（IMY）"),
    "BE": ("欧盟《通用数据保护条例》（GDPR，Regulation (EU) 2016/679）", "比利时数据保护局（APD/GBA）"),
    "PL": ("欧盟《通用数据保护条例》（GDPR，Regulation (EU) 2016/679）及波兰《个人数据保护法》", "波兰个人数据保护局局长（UODO）"),
    "AT": ("欧盟《通用数据保护条例》（GDPR，Regulation (EU) 2016/679）", "奥地利数据保护局（DSB）"),
    "IE": ("欧盟《通用数据保护条例》（GDPR，Regulation (EU) 2016/679）", "爱尔兰数据保护委员会（DPC）"),
    "EU": ("欧盟《通用数据保护条例》（GDPR，Regulation (EU) 2016/679）", "欧洲数据保护委员会（EDPB）及成员国独立监管机关"),
}
_EU_EEA_CODES = set(_EU_SUPERVISORS) | {"PL", "PT", "CZ", "HU", "NO"}


_STATUTORY_SUPERVISORS: dict[str, str] = {
    "AR": "阿根廷公共信息获取局（AAIP）",
    "AU": "澳大利亚信息专员公署（OAIC）",
    "BD": "孟加拉国国家网络安全局（NCSA）及电信监管委员会（BTRC）",
    "CA": "加拿大联邦隐私专员公署（OPC）及省级信息与隐私监管机关",
    "CO": "哥伦比亚工商监管局（SIC）个人数据保护署",
    "ID": "印度尼西亚通信与数字部个人数据保护监管机关",
    "IL": "以色列隐私保护局（PPA）",
    "KG": "吉尔吉斯斯坦内阁下属个人数据保护署及数字发展部",
    "KR": "韩国个人信息保护委员会（PIPC）",
    "KW": "科威特通信与信息技术监管局（CITRA）",
    "KZ": "哈萨克斯坦数字发展、创新和航空航天工业部信息安全委员会",
    "MX": "墨西哥联邦个人数据保护主管机关（反腐败与良政秘书处 / 原 INAI）",
    "OM": "阿曼交通、通信和信息技术部（MTCIT）",
    "PE": "秘鲁国家个人数据保护局（ANPD）",
    "PK": "巴基斯坦联邦政府指定电子与数据执法机关及电信管理局（PTA）",
    "QA": "卡塔尔国家网络安全局（NCSA）国家数据隐私合规办公室",
    "SA": "沙特数据与人工智能局（SDAIA）",
    "VN": "越南公安部网络安全与高科技犯罪防范局（A05）",
}


async def receiving_law(session: AsyncSession, country: str | None, clauses: list | None = None) -> list[list[str]]:
    """The four things a transfer impact assessment states about the receiving country, from the
    library: its data protection law, its supervisory body, the law on public authorities' access
    to data, and the remedies of the people concerned — each with the clause it rests on.
    ``clauses`` (a list) receives the clauses the table rests on, as citations."""
    if not country:
        return []
    from sqlalchemy import text as sql

    from app.knowledge.cache_sync import keeper_active, rebuilding, use_cached
    from app.knowledge.reading import article_label

    s_cache = session.info.setdefault("_receiving_law", {})
    if country in s_cache and not rebuilding.get():
        rows_out, cited_out = s_cache[country]
        if clauses is not None:
            clauses.extend(copy.deepcopy(cited_out))
        return copy.deepcopy(rows_out)
    if use_cached(country in _RECEIVING_LAW_CACHE):
        rows_out, cited_out = _RECEIVING_LAW_CACHE[country]
        if clauses is not None:
            clauses.extend(copy.deepcopy(cited_out))
        return copy.deepcopy(rows_out)

    law = await doc_rules.main_data_law(session, country)
    body = await main_regulator(session, country)
    rows = (await session.execute(sql(
        f"""select coalesce(d.title_zh, d.title), k.clause_number, coalesce({doc_rules._SQL_SINGLE_CURRENT_TR}, case when k.language like 'zh%' then k.original_text end), k.original_text, k.id::text, d.id::text, d.title
           from clauses k join document_versions v on v.id=k.version_id and v.state='parsed' join documents d on d.id=v.document_id
           where d.country_code=:c
             and d.relevance in ('relevant', 'related')
             {doc_rules._SQL_ELIGIBLE_DOC_VER_CLAUSE}
           order by case when coalesce(d.title_zh, d.title) ~ '个人数据|个人信息|数据保护|电子交易与个人数据' then 0 else 1 end,
                    case d.document_type when 'LAW' then 0 when 'REGULATION' then 1 else 2 end,
                    case coalesce(d.instrument_kind, '') when 'BASE' then 0 else 1 end,
                    d.title_zh, k.ordinal"""), {"c": country})).all()
    table_cited: list[dict] = []

    def first(pat) -> str:
        for title, number, zh, original, cid, did, orig_doc_title in rows:
            if re.search(r"视频监控|门禁|健康|医疗|研究|中央银行|银行体系|证券|保险|反洗钱|金融科技", title or ""):
                continue
            if re.search(r"惩戒委员会|承租人|出租人|发动机功率|空载重量|无权要求赔偿", zh or ""):
                continue
            if not number or clause_gate.why_unusable(zh, title, original) or doc_rules._OUTBOUND.search(zh or "") or doc_rules.not_a_basis({"quote_zh": zh}):
                continue
            m = pat.search(zh or "")
            if m:
                start = max(zh.rfind(x, 0, m.start()) for x in ("。", "；", chr(10))) + 1
                end = min([i for i in (zh.find(x, m.end()) for x in ("。", "；")) if i >= 0] or [len(zh)])
                shown = clause_gate.strip_own_label(article_label(number), zh[start:end + 1].strip())
                ln = clause_gate.law_name(title, did, country_code=country)
                if not any(x["clause_id"] == cid for x in table_cited):
                    table_cited.append({"clause_id": cid, "document_id": did, "document_title": ln, "original_title": str(orig_doc_title or "").strip() if orig_doc_title and orig_doc_title != title else "", "clause_number": article_label(number),
                                        "quote_original": original or "", "quote_zh": zh, "dimension_code": "D3", "side": "recipient", "core": True})
                return f"《{ln}》{article_label(number)}：{shown}"
        return ""

    first(_SUPERVISOR)  # 将监管机构的法定职权与执法调查条文一并纳入可引用池，供 TIA 第三节核实引用
    access_txt = first(_ACCESS)
    remedy_txt = first(_REMEDY_EXT) or first(_REMEDY)

    if not body:  # 图谱里没有监管机构节点：取条文里最常点名的专门数据/隐私/网安监管机构
        counts: dict[str, int] = {}
        for _, _, zh, _, _, _, _ in rows:
            for m in re.findall(
                r"(?:国家|联邦)?(?:个人数据|个人信息|数据保护|数据隐私|信息保护|隐私保护|国家隐私|信息监管|网络安全|通信与信息技术)(?:[一-鿿]{0,8})(?:委员会|中心|管理局|监管局|保护局局长|保护局|办公室|专员公署|专员|署|局)",
                zh or "",
            ):
                cleaned_m = re.sub(r"^(?:是指|系指|指|由|向|报|经|归|及|与|或|该|本|任何|款所述|上述)+", "", m).strip()
                if len(cleaned_m) >= 5 and not _NON_DPA_NODE_RE.search(cleaned_m):
                    counts[cleaned_m] = counts.get(cleaned_m, 0) + 1
        body = max(counts, key=counts.get) if counts else ""

    if country in _STATUTORY_SUPERVISORS and (not body or not _DPA_PRIORITY_RE.search(body)):
        body = _STATUTORY_SUPERVISORS[country]

    safe_law = (law or "").replace("《", "〈").replace("》", "〉")
    if country in _EU_EEA_CODES:
        eu_law, eu_body = _EU_SUPERVISORS.get(
            country,
            (
                "欧盟《通用数据保护条例》（GDPR，Regulation (EU) 2016/679）及成员国实施立法",
                f"{NAMES.get(country, country)}国家数据保护独立监管机关（受欧洲数据保护委员会 EDPB 协同监督）",
            ),
        )
        if safe_law and "GDPR" not in safe_law and "通用数据保护条例" not in safe_law:
            law_disp = f"{eu_law}（本国在库立法：《{safe_law}》）"
        else:
            law_disp = f"《{safe_law}》" if safe_law else eu_law
        if country in _EU_SUPERVISORS or not body or not _DPA_PRIORITY_RE.search(body):
            body = eu_body
        if not access_txt:
            access_txt = (
                "《欧盟通用数据保护条例》（GDPR）第 6 条第 1 款 (c)/(e) 项、第 23 条及第 48 条："
                "公权力机关调取个人数据须具备欧盟或成员国成文法明确授权，严格受民主社会必要性与比例原则约束；"
                "非经司法协助条约等国际协定，第三国法院裁决或行政调取指令在欧盟境内不具有直接强制执行力。"
            )
            table_cited.append(
                {
                    "clause_id": "",
                    "citation_tag": f"GDPR-Art23-48-{country}",
                    "document_id": "",
                    "document_title": "欧盟通用数据保护条例（GDPR）",
                    "original_title": "Regulation (EU) 2016/679 (General Data Protection Regulation)",
                    "clause_number": "第23条、第48条",
                    "quote_original": "Article 23 & Article 48 GDPR: Restrictions on obligations and rights must be laid down by Union or Member State law and constitute a necessary and proportionate measure in a democratic society; transfers or disclosures not authorised by Union law require an international agreement such as a mutual legal assistance treaty.",
                    "quote_zh": "第23条、第48条：欧盟或成员国法律对权利和义务的限制须尊重基本权利与自由的本质，且在民主社会中构成必要且相称的措施；第三国法院判决或行政机关要求传输或披露个人数据的决定，仅在基于请求国与欧盟或其成员国之间现行有效的司法协助条约等国际协定时方予承认或执行。",
                    "dimension_code": "D3",
                    "side": "recipient",
                    "core": True,
                }
            )
        if not remedy_txt:
            remedy_txt = (
                f"《欧盟通用数据保护条例》（GDPR）第 77 条至第 79 条及第 82 条："
                f"数据主体有权向{body}提起行政投诉，有权针对监管决定或数据控制者、处理者的侵权处理行为向成员国管辖法院提起有效司法救济，并依法请求物质或精神损害赔偿。"
            )
            table_cited.append(
                {
                    "clause_id": "",
                    "citation_tag": f"GDPR-Art77-79-82-{country}",
                    "document_id": "",
                    "document_title": "欧盟通用数据保护条例（GDPR）",
                    "original_title": "Regulation (EU) 2016/679 (General Data Protection Regulation)",
                    "clause_number": "第77条至第79条、第82条",
                    "quote_original": "Articles 77–79 & 82 GDPR: Every data subject shall have the right to lodge a complaint with a supervisory authority, the right to an effective judicial remedy against a supervisory authority or against a controller or processor, and the right to receive compensation from the controller or processor for material or non-material damage suffered.",
                    "quote_zh": "第77条至第79条、第82条：每位数据主体在认为与其相关的个人数据处理违反本条例时，均享有向独立监管机关提出投诉的权利、针对监管机关有法律约束力的决定或针对控制者及处理者向成员国法院寻求有效司法救济的权利，并有权就遭受的物质或非物质损害向控制者或处理者请求赔偿。",
                    "dimension_code": "D3",
                    "side": "recipient",
                    "core": True,
                }
            )
    else:
        law_disp = f"《{safe_law}》" if safe_law else DRAFT_BLANK

    result_rows = [
        ["数据保护法律", law_disp],
        ["监管机构", body or DRAFT_BLANK],
        ["公权力调取数据的依据", access_txt or DRAFT_BLANK],
        ["数据主体的救济途径", remedy_txt or DRAFT_BLANK],
    ]
    if clauses is not None:
        clauses.extend(copy.deepcopy(table_cited))
    s_cache[country] = (result_rows, table_cited)
    if keeper_active or rebuilding.get():
        _RECEIVING_LAW_CACHE[country] = (result_rows, table_cited)
    return copy.deepcopy(result_rows)


def addressee_entry(entry) -> str:
    """The body named on the item's official form, when the item has one."""
    from app.admission.router import entry_forms

    for f in entry_forms(entry):
        if f.get("authority_zh"):
            return re.sub(r"[（(]推断[^）)]*[）)]", "", f["authority_zh"]).strip()
    return ""


def addressee(detail: dict) -> str:
    """Whom the application goes to: the body of the item's official form when it has one (the form
    and the letter must name the same body), else the body the law names."""
    from app.admission import engine

    for f in detail.get("forms") or []:
        if f.get("authority_zh"):
            return re.sub(r"[（(]推断[^）)]*[）)]", "", f["authority_zh"]).strip()
    return engine.official_authority(detail)


async def admission_blocks(
    session: AsyncSession, task, values: dict[str, str], *, definition: dict | None = None
) -> dict[str, list[dict]]:
    """05: the target country's admission items for this business, written like the admission
    application material — one shared way of saying it (app.admission.engine)."""
    from app.admission import engine
    from app.admission.material_kinds import plan_of
    from app.admission.router import _entry_detail

    source, recipient = doc_rules.ends(values, task.country_code)
    target = doc_rules.overseas(source, recipient)
    every = await admission_entries(session, task, target)
    entries = doc_rules.inbound_only(every, source, recipient, target, values)
    onward = [(e, f"数据传入{NAMES.get(target or '', '')}时不用办；接收方再向第三国转移时才办") for e in every if e not in entries]
    maybe = [(e, doc_rules.admission_gate(e)) for e in entries if doc_rules.admission_gate(e)] + onward
    entries = [e for e in entries if not doc_rules.admission_gate(e)]
    if not entries:
        none = [{"type": "paragraph", "text": f"{NAMES.get(target or '', '目标国家')}对这项业务没有要办理的准入事项。"}]
        return {"items": none, "materials": [], "filing": [], "route": [], "clauses": []}
    how = {"info": "写进申请书", "form": "填写官方表格", "application": "准入申请书（随附提交）", "original": "企业递交时自带"}
    merged_vals = {**answered(task), **{k: v for k, v in values.items() if v}}
    permit_mark = _permit_option((await doc_rules.route(session, merged_vals, task.country_code)).get("text", ""))
    items_rows, materials, filing, route, clause_ids = [], [], [], [], []
    for i, e in enumerate(entries, 1):
        detail = await _entry_detail(session, e)
        forms = [f["name_zh"].split("：", 1)[-1] for f in detail.get("forms") or []]
        steps = engine.law_steps_of(detail)
        plan = plan_of(e, [m for st in steps for m in st.get("materials") or []])
        clause_of = {}
        for st in steps:
            for m in st.get("materials") or []:
                clause_of.setdefault(m, st.get("clause_id"))
        ids = [(detail.get("basis") or {}).get("clause_id"), *(st.get("clause_id") for st in steps)]
        clause_ids += [x for x in ids if x]
        texts = await engine.clause_texts(session, [x for x in ids if x])
        items_rows.append([str(i), e.name_zh, addressee(detail) or "条文未写明受理机构", _applicant(e, receiving=NAMES.get(target or "", "") if target == recipient and source != recipient else ""), _conditions(e) + _only_if_permit(e, permit_mark), "、".join(f"《{f}》" for f in forms) or "—"])
        rows = []
        for p in plan:
            if p.get("kind") == "none":
                continue
            prep = how.get(p["kind"], "企业递交时自带") + (f"（{p['what']}）" if p["kind"] == "original" and p.get("what") else "")
            shown = engine._clean_name(
                re.sub(r"[（(][^）)]*[؀-ۿ][^）)]*[）)]", "", p["name"]).strip() or p["name"]
            )  # 中文名后括注的阿拉伯原文及重复外文缩写不放进中文文件
            src = texts.get(str(clause_of.get(p["name"]) or ""), {}).get("source", "—")
            rows.append([shown, prep, "—" if clause_gate.is_form(src) else src])  # 官方表格不当法规依据
        rows += [[f"官方表格《{f}》", "填写官方表格，签字盖章", "—"] for f in forms if not any(p.get("kind") == "form" for p in plan)]
        materials.append({"type": "paragraph", "text": f"（{i}）{e.name_zh}"})
        materials.append({"type": "table", "head": ["材料名称", "准备方式", "法规依据"], "rows": rows} if rows else {"type": "paragraph", "text": "条文没有列出具体材料；按办理路线准备，递交前可向受理机构确认。"})
        lang = engine.known_value(detail.get("profile") or {}, "language").removeprefix("按官方表格：")
        first = re.split(r"[/、；;，,（(]", lang)[0].strip() if lang else ""
        first_adj = "英文" if first == "英语" else first
        if lang and re.search(r"译文|译本|翻译|公证", lang):
            if re.search(r"[；;]", lang):
                l_head, l_tail = re.split(r"[；;]", lang, maxsplit=1)
                lang_clause = f"以{l_head.strip()}提交（{l_tail.strip()}）"
            else:
                lang_clause = f"以{lang}提交"
        elif lang:
            lang_clause = f"以{lang}提交，非{first_adj}证明材料须附具{first_adj}正式译本"
        else:
            lang_clause = "申请语言条文未写明，递交前向受理机构确认"
        portal = detail.get("portal")
        where = ("；递交入口：" + "、".join([f"官网 {portal['site']}", *(f"{x['label']} {x['url']}" for x in portal.get("links") or [])])) if portal else ""
        filing.append(f"{e.name_zh}：向{addressee(detail) or '主管部门（条文未写明受理机构）'}递交，" + lang_clause + where + "。")
        route.append({"type": "paragraph", "text": f"（{i}）{e.name_zh}"})
        steps_ = [t.split("：", 1) if "：" in t[:24] else ["", t] for t in engine.route_steps(engine.official_detail(detail), forms, for_file=True)]
        route.append({"type": "table", "head": ["步骤", "办理环节", "要求说明"], "rows": [[str(k), a or b.rstrip("。"), b if a else ""] for k, (a, b) in enumerate(steps_, 1)]})
    texts = await engine.clause_texts(session, list(dict.fromkeys(clause_ids)))
    clauses = []
    for cid in dict.fromkeys(clause_ids):
        c = texts.get(str(cid))
        if c and c.get("verified") and not clause_gate.is_form(re.sub(r"第[^》]*$", "", c["source"])):  # 官方表格不当法规依据
            label = re.sub(r"^《[^》]*》", "", c["source"])
            clauses.append({"type": "paragraph", "text": c["source"] + "：" + engine._cut(clause_gate.strip_own_label(label, c["zh"]), 700), "verbatim_quote": True})
            clauses.append({"type": "paragraph", "text": "原文：" + engine._cut(c["original"], 700), "original": True})
    from app.analysis import verdict_rules

    target_zh = NAMES.get(target or "", "目标国家")
    merged_adm_vals = {**answered(task), **{k: v for k, v in values.items() if v}}
    if not merged_adm_vals.get("recipient_country") and target:
        merged_adm_vals["recipient_country"] = target_zh
    adm_verdict = verdict_rules.compute_doc_verdict(
        "admission",
        merged_adm_vals,
        task_country_code=getattr(task, "country_code", None),
        admission_count=len(entries),
        definition=definition,
    )
    verdict_blocks = (
        [
            {
                "type": "paragraph",
                "text": f"属地准入综合判定结论（{adm_verdict['label']}）：{adm_verdict['text']}",
            }
        ]
        if adm_verdict
        else []
    )
    lead_summary = (
        f"经检索核验{target_zh}现行有效的车辆、通信及数据保护法规库，结合本项目申报的业务功能与跨境传输方向，"
        f"共筛选出 {len(entries)} 项直接适用的法定准入事项"
        + (f"，另有 {len(maybe)} 项按特定业务条件触发的备查事项。" if maybe else "。")
    )
    return {
        "items": [
            {"type": "paragraph", "text": lead_summary},
            {"type": "table", "head": ["序号", "准入事项", "受理机构", "申请主体", "申请条件", "官方表格"], "rows": items_rows},
        ]
        + ([{"type": "paragraph", "text": "按条件适用事项（满足特定业务条件时办理）："},
            {"type": "table", "head": ["准入事项", "适用条件说明"], "rows": [[e.name_zh, why] for e, why in maybe]}] if maybe else []),
        "materials": materials,
        "filing": [{"type": "list", "items": filing}],
        "route": route,
        "verdict": verdict_blocks,
        "clauses": clauses,
    }


_DELIM_PAIRS = (("《", "》"), ("（", "）"), ("(", ")"), ("“", "”"), ("【", "】"))
_OPEN_TO_CLOSE = dict(_DELIM_PAIRS)
_CLOSE_TO_OPEN = {cl: op for op, cl in _DELIM_PAIRS}


def _balance_open_delimiters(s: str) -> str:
    """Roll back trailing chopped parentheticals (e.g. '（Regulation (EU) ' or '(QH-A') and close
    any remaining outer paired delimiters such as '《...》'."""
    s = s.rstrip(" ，,、：:")
    # 若末尾存在未闭合的圆括号注释（截断在括号半途），优先回退到该未闭合左括号之前，避免残留半截外文词
    for _ in range(3):
        stack: list[tuple[str, int]] = []
        for idx, ch in enumerate(s):
            if ch in _OPEN_TO_CLOSE:
                stack.append((ch, idx))
            elif ch in _CLOSE_TO_OPEN:
                want = _CLOSE_TO_OPEN[ch]
                for k in range(len(stack) - 1, -1, -1):
                    if stack[k][0] == want:
                        del stack[k:]
                        break
        if not stack:
            return s
        last_op, last_pos = stack[-1]
        if last_op in ("（", "(") and last_pos >= 3:
            s = s[:last_pos].rstrip(" ，,、：:")
            continue
        break
    for op, cl in _DELIM_PAIRS:
        diff = s.count(op) - s.count(cl)
        if diff > 0:
            s = s + (cl * diff)
    return s


def _cut_respecting_words_and_delims(txt: str, limit: int, marks: tuple[str, ...]) -> str:
    if len(txt) <= limit:
        return txt
    # 1) 优先在嵌套深度为 0 的标点处切分，绝不在《...》或（...）内部拦腰截断
    depth = 0
    best_zero = -1
    for i, ch in enumerate(txt[:limit]):
        if ch in _OPEN_TO_CLOSE:
            depth += 1
        elif ch in _CLOSE_TO_OPEN and depth > 0:
            depth -= 1
        elif depth == 0 and ch in marks:
            best_zero = i
    if best_zero >= max(10, limit // 3):
        return txt[:best_zero]
    # 2) 退化到定长切分时保护英文/法文/型号词边界，不切出半截 'Libert' 或 '(QH-A'
    cut = limit
    if cut < len(txt) and txt[cut - 1].isascii() and txt[cut].isascii():
        if re.match(r"[A-Za-z0-9_-]", txt[cut - 1]) and re.match(r"[A-Za-z0-9_-]", txt[cut]):
            back = cut
            while back > max(8, limit - 28) and re.match(r"[A-Za-z0-9_.-]", txt[back - 1]):
                back -= 1
            if back > max(8, limit - 28):
                cut = back
    return txt[:cut].rstrip()


def _clean_fact_snippet(val: str, fallback: str, limit: int = 240) -> str:
    txt = re.sub(r"\s+", " ", (val or "").strip()).rstrip("。；;，,！! ")
    if not txt or txt.startswith("暂不确定") or txt == DRAFT_BLANK:
        return fallback
    effective_limit = max(limit, 220)
    if len(txt) <= effective_limit:
        return _balance_open_delimiters(txt)
    res = _cut_respecting_words_and_delims(txt, effective_limit, ("。", "；", ";", "，", ","))
    return _balance_open_delimiters(res.rstrip("。；;，,！! ").strip())


def _first_clause_phrase(val: str, fallback: str, limit: int = 56) -> str:
    """Extract a short, clean first clause without periods, colons, or dangling parentheses/guillemets."""
    txt = re.sub(r"\s+", " ", (val or "").strip())
    if not txt or txt.startswith("暂不确定") or txt == DRAFT_BLANK:
        return fallback
    first = re.split(r"[。；;\n]", txt)[0].strip()
    first = re.sub(r"^(?:关于|围绕|针对)[^：:]{1,18}[：:]\s*", "", first)
    first = re.sub(r"^(?:本单位|本公司|本项目|企业)(?:拟|旨在|用于|主要)?", "", first).strip()
    if len(first) > limit:
        # 仅剥离成对闭合的括号补充说明（保留《书名号》主体与后续动宾结构，绝不在《...》中间切开）
        stripped = re.sub(r"（[^（）]{1,48}）|\([^()]{1,48}\)", "", first).strip()
        if len(stripped) >= 4:
            first = stripped
    if len(first) > limit:
        first = _cut_respecting_words_and_delims(first, limit, ("，", "、"))
    first = _balance_open_delimiters(first.rstrip("。；;，,：: ").strip())
    return first or fallback


_NEGATED_CLAUSE_RE = re.compile(
    r"(?:(?:严禁|禁止|不得|绝不|从不|默认不|默认关闭|关闭|不予|未曾|不会|无需|不进行|不向境外|不|未|无|没有)"
    r"(?:采集|收集|获取|抓取|录制|开启|启用|回传|上传|传输|出境|包含|涉及|纳入|处理|提取|同步|开展|上报|存在|生成)"
    r"|不含|不包含|不涉及|不纳入|排除|(?:无|没有)(?=[^；。，\n]{0,16}(?:视频|图像|影像|摄像头|座舱|麦克风|人脸|声纹|生物|轨迹|定位|GNSS|GPS|点云|路人|行人|第三方|乘员|乘客|未成年|儿童|驾驶人))|不在[^；。，\n]{0,16}范围内)[^；。，\n]*|"
    r"[^；。，\n（）()]{1,24}?(?:均?不(?:采集|收集|获取|录制|开启|启用|回传|上传|传输|出境|处理)|未(?:开启|启用|采集|收集))[^；。，\n]*|"
    r"（(?:不含|不包含|不涉及|严禁|不采集|无|没有|排除)[^（）]*）|\((?:不含|不包含|不涉及|严禁|不采集|无|没有|排除)[^()]*\)"
)
_PROHIBITED_UNMASKED_RE = re.compile(
    r"(?:严禁|禁止|不得|杜绝|防止|阻断|拦截)[^；。，\n]{0,16}?(?:未|未经|不做|未完成|不进行)\s*(?:任何|有效|不可逆)?\s*(?:脱敏|去标识化?|匿名化?|模糊|遮蔽|擦除|哈希|降精|假名化)[^；。，\n]*|"
    r"(?:未|未经|不做|未完成|不进行)\s*(?:任何|有效|不可逆)?\s*(?:脱敏|去标识化?|匿名化?|模糊|遮蔽|擦除|哈希|降精|假名化)[^；。\n]{0,24}?(?:严禁|禁止|不得|不予|杜绝|防止|阻断|拦截)[^；。\n]*"
)
_UNMASKED_RE = re.compile(
    r"(?:未|未经|不做|未完成|不进行|缺乏|无)\s*(?:任何|有效|不可逆)?\s*(?:脱敏|去标识化?|匿名化?|模糊|遮蔽|擦除|哈希|降精|假名化)"
)


def _strip_negated_clauses(text: str) -> str:
    """Strip negated exclusion statements ('严禁采集车外视频' / '不含座舱音视频' / '默认关闭GNSS上报')
    before positive keyword detection so excluded sensors are never misclassified as collected."""
    return _NEGATED_CLAUSE_RE.sub(" ", text or "")


def necessity_alternatives_blocks(task, code: str, values: dict[str, str]) -> list[dict]:
    """Reference DPIA Chapter 3.2 & 3.3 / LIA Step 2 / Risk Report Ch. 3: four-option counterfactual necessity comparison differentiated by legal instrument."""
    facts = {**answered(task), **{k: v for k, v in values.items() if v}}
    source_code, recipient_code = doc_rules.ends(facts, getattr(task, "country_code", None))
    src_zh = NAMES.get(source_code or "", facts.get("source_country") or "发出国")
    rcp_zh = NAMES.get(recipient_code or "", facts.get("recipient_country") or "接收国")
    vehicle = _first_clause_phrase(facts.get("vehicle_model") or getattr(task, "vehicle_model", "") or "", "受评车型车队", 28)
    scenario = _first_clause_phrase(
        facts.get("scenario") or getattr(task, "scenario_label", "") or getattr(task, "scenario_type", "") or "",
        "车载网联服务与远程协同运维",
        36,
    )
    purpose = _first_clause_phrase(facts.get("purpose") or "", "保障在用车辆运行安全与跨区域工程协同", 56)
    ret_short = _first_clause_phrase(facts.get("retention_and_blackbox") or "", "最短必要业务留存周期", 42)

    if code == "lia":
        rows = [
            [
                "替代路径 A：完全依赖车端本地闭环或人工模拟测试（零个人数据处理）",
                f"仅凭 {vehicle} 车载本地算力或实验室合成样本运行，不接触任何真实工况关联记录。",
                f"合成样本与单车孤立算力无法覆盖真实场景长尾故障与边界工况，无法实质达成“{purpose}”之合法利益目标。",
                "不具备目的可实现性（排除）",
            ],
            [
                "替代路径 B：持续静默采集全量原始身份与高精度感知轨迹",
                "不设触发条件与源端遮蔽，后台常态化上传明文车架号、连续精确位置或原始音视频流。",
                f"显著超出数据主体在“{scenario}”下的合理预期，存在同等目标下侵入性更轻的技术路径，无法通过最小侵害测试。",
                "违反最小侵害要求（排除）",
            ],
            [
                "替代路径 C：事件触发＋源端去标识化白名单处理（本评估选定路径）",
                f"在 {src_zh} 端先行剥离直接身份标识并收敛精度，仅在预设事件触发时按白名单处理最小必要字段（留存受控：{ret_short}）。",
                "在确保正当安全与履约利益落地的同时，将对相关自然人私密空间与行踪自决权的影响压降至客观最低限度。",
                "通过必要性排他测试（采用）",
            ],
            [
                "替代路径 D：逐笔实时弹窗索取临时授权替代合法利益/常态机制",
                "每次产生后台安全诊断或路侧瞬时感知均中断操作请求人工逐次授权。",
                "在行车高速动态环境或开放道路场景下不仅客观不可行，且频繁弹窗干扰驾驶注意力反而危及道路公共安全。",
                "实操不可行且损及行车安全（排除）",
            ],
        ]
        cond_para = (
            f"相称性生效前提核定（合法利益第二步测试）：上述选定路径的最小侵害性以四项控制刚性存续为条件——"
            f"严禁超白名单字段夹带、{src_zh}源端去标识化不可逆转、向数据主体开放便捷异议或退出通道、以及期满连同备份彻底清除。"
        )
    elif code == "risk_report":
        rows = [
            [
                "比选方案一：纯境内单节点闭环处置（不出境）",
                f"仅在{src_zh}境内节点留存分析 {vehicle} 运行数据，不向{rcp_zh}接收端传输任何字段。",
                f"核心跨区域技术支持团队、算法基线或属地维保履约体系位于{rcp_zh}，零跨境协同将直接导致{purpose}无法闭环兑现。",
                "业务与安全目标无法达成（排除）",
            ],
            [
                "比选方案二：原始明文全量镜像跨境同步",
                f"将境内数据库原始车架号、车主身份明细及未降精感知报文全量镜像同步至{rcp_zh}云端。",
                "造成境外攻击面与潜在泄露影响人数成倍放大，违反数据出境“最小必要范围、最短留存周期、最少访问席位”法定红线。",
                "跨境安全风险不可接受（排除）",
            ],
            [
                "比选方案三：境内安全网关前置脱敏＋最小字段受控出境（现行方案）",
                f"由{src_zh}前置安全网关执行字段白名单强过滤与假名化令牌替换，仅按需向{rcp_zh}受控专区传输最小业务参数。",
                f"兼顾跨国工程履约刚需与主权密钥留存（{ret_short}），有效收敛境外数据沉淀规模与外部越权调取暴露面。",
                "本项目采用（出境风险可控且最小必要）",
            ],
            [
                "比选方案四：各海外区域全栈重建独立工程与诊断底座",
                f"在{rcp_zh}本地完整复制全套核心算法训练集群、历史缺陷库及全天候二线专家团队。",
                "受制于跨国车企统一安全基线治理架构与高昂重复建设周期，现阶段不具备工程与经济可行性，列为远期属地化演进方向。",
                "中长期演进参照（现阶段辅以网关管控）",
            ],
        ]
        cond_para = (
            f"相称性生效前提核定（数据出境最小必要核查）：本方案持续满足出境必要性须严守四道防线——"
            f"出境网关白名单硬拦截、还原主密钥独占托管于{src_zh}境内、严禁向未经约定第三方再转移、以及到期生产与备份副本同步擦除。"
        )
    else:
        rows = [
            [
                "方案一：纯车端本地离线处理（数据不出车、不上云）",
                f"仅依赖 {vehicle} 车载控制器与边缘算力独立离线运算，不向{rcp_zh}或云端同步任何业务与工程参数。",
                f"虽可将跨境暴露风险降至零，但无法支撑{scenario}所需的跨批次异常工况复现与协同分析，难以实现{purpose}之核心目标。",
                "予以排除（无法达成既定业务与安全目标）",
            ],
            [
                "方案二：原始全量未脱敏数据直接跨境传输",
                "不做源端过滤与去标识化预处理，持续或批量回传车内外原始感知画面、完整精确轨迹及明文身份标识。",
                "采集颗粒度与频次显著超出特定目的所需最小边界，对驾乘人员及道路参与者基本权利与自由侵入性过高，违背默认隐私保护原则。",
                "坚决排除（对个人基本权利侵入性过高）",
            ],
            [
                "方案三：源端预处理脱敏＋按需触发最小白名单传输（现行方案）",
                f"在车端及{src_zh}前置节点先行完成不可逆脱敏与字段过滤，仅在预设事件触发时向{rcp_zh}按白名单传输最小必要参数。",
                f"在完整达成车辆安全验证与服务履约目标的同时，从源头剥离直接识别特征并锁定留存窗口（{ret_short}），兼顾安全效益与自然人权利保护。",
                "本项目采用（同等功能目标下侵入性最小）",
            ],
            [
                "方案四：目标市场完全属地化独立部署（远期演进参照）",
                f"在{rcp_zh}独立重建全套核心工程诊断平台、历史基线库及全天候专职支持体系，实现零跨法域数据交互。",
                "受跨国研发体系集中维护核心安全基线及属地专职工程资源建设周期客观制约，短期内不具备全面替代可行性。",
                "中长期演进参照（现阶段辅以补充措施过渡）",
            ],
        ]
        cond_para = (
            "相称性生效前提核定：上述现行方案的相称性以四项刚性控制持续有效为前提——默认关闭非必要采集、源端去标识化规则不可绕过、分层告知与撤回或异议通道畅通、以及留存期满连同全部备份副本彻底安全销毁。"
        )
    return [
        {"type": "paragraph", "text": "必要性与最小侵害替代方案排他比选表："},
        {
            "type": "table",
            "head": ["备选方案", "技术与业务实现方式", "目标达成度与基本权利侵入性权衡论证", "比选结论"],
            "rows": rows,
            "widths": [20, 25, 39, 16],
        },
        {"type": "paragraph", "text": cond_para},
    ]


def safeguard_adequacy_blocks(task, code: str, values: dict[str, str], route: dict) -> list[dict]:
    """Adequacy and effectiveness justification table differentiated by legal instrument (DPIA Ch. 2.3 / TIA Ch. 4 / Risk Report Ch. 7)."""
    facts = {**answered(task), **{k: v for k, v in values.items() if v}}
    source_code, recipient_code = doc_rules.ends(facts, getattr(task, "country_code", None))
    src_zh = NAMES.get(source_code or "", facts.get("source_country") or "发出国")
    rcp_zh = NAMES.get(recipient_code or "", facts.get("recipient_country") or "接收国")
    basis_raw = facts.get("lawful_basis") or facts.get("consent_and_notice") or facts.get("cross_border_mechanism") or ""
    data_raw = facts.get("exterior_video_and_gnss") or facts.get("data_types") or ""
    ret_raw = facts.get("retention_and_blackbox") or ""
    sec_raw = facts.get("supplementary_measures") if code == "tia" else (facts.get("risk_measures") or facts.get("security_tech") or facts.get("supplementary_measures") or "")
    if not sec_raw:
        sec_raw = facts.get("risk_measures") or facts.get("security_tech") or ""
    contract_raw = facts.get("legal_doc_terms") or facts.get("cross_border_mechanism") or route.get("text") or ""

    def _status(raw: str) -> str:
        s = (raw or "").strip()
        return "待补充确认" if (not s or s == DRAFT_BLANK or s.startswith("暂不确定")) else "已核验纳入管控"

    if code == "tia":
        rows = [
            [
                "发出端假名化与主权密钥物理隔离（技术补充）",
                _clean_fact_snippet(data_raw, f"在{src_zh}前置节点剥离直接身份标识，还原密钥独占托管于{src_zh}硬件安全模块", 110),
                f"对标 EDPB 第 01/2020 号建议书典型有效假名化场景：即便{rcp_zh}接收端存储介质受外部接触，在缺乏{src_zh}独立托管密钥与映射表的情况下亦无法逆向识别特定自然人。",
                _status(data_raw),
            ],
            [
                "跨境通道强密码学保护与防截获校验（技术补充）",
                _clean_fact_snippet(sec_raw, "采用跨境专用加密链路、双向证书鉴权（mTLS 1.3）与静态高强度加密存储", 110),
                "有效抵御跨国公网与海底光缆过境传输阶段的被动监听、中间人劫持与报文重放篡改，确保在途与静态密文机密性。",
                _status(sec_raw),
            ],
            [
                "境外最小访问席位与生命周期硬销毁（组织补充）",
                _clean_fact_snippet(ret_raw, f"{rcp_zh}侧实行实名白名单只读访问、禁批量导出，到期主库与快照同步清零", 110),
                f"大幅压缩数据在{rcp_zh}境内的时间窗口与可触达人员边界，从组织流程上降低内部越权调阅及历史存量数据被宽泛调取的概率。",
                _status(ret_raw),
            ],
            [
                "公权力调取合法性审查、通知与抗辩（合同补充）",
                _clean_fact_snippet(contract_raw, f"合同明确约定遇{rcp_zh}机关调取须即时通报{src_zh}控制者、核查法定权限并依法抗辩", 110),
                "将外部调取应对固化为具强制约束力的违约追责条款，杜绝非经司法令状或超比例原则的静默批量数据移交。",
                _status(contract_raw),
            ],
            [
                "法域环境监测与异常单路径熔断机制（治理补充）",
                _clean_fact_snippet(facts.get("reevaluation") or basis_raw, f"持续跟踪{rcp_zh}数据立法与执法变化，异常时立即暂停跨境接口", 110),
                f"一旦{rcp_zh}法制变更导致补充措施无法维持实质同等保护水平，可毫秒级切断跨境同步并退回{src_zh}本地留存。",
                _status(facts.get("reevaluation") or basis_raw),
            ],
        ]
    elif code == "risk_report":
        mgmt_raw = facts.get("security_mgmt") or facts.get("effectiveness_proof") or basis_raw
        rcp_m_raw = facts.get("recipient_measures") or ret_raw
        rows = [
            [
                "境内分类分级内控与合规测评背书",
                _clean_fact_snippet(mgmt_raw, "建立专职数据安全机构、分类分级与应急预案，落实等保测评或国际安全认证", 110),
                "通过制度化出境审批门禁与第三方权威安全测评验证，确保数据处理者自身具备持续识别与管控跨境安全隐患的内生治理能力。",
                _status(mgmt_raw),
            ],
            [
                "车端默认克制采集与出境网关硬裁剪",
                _clean_fact_snippet(data_raw, "落实车内处理优先与默认不采集规则，境内出境安全网关执行字段白名单强过滤", 110),
                "将非必要原始识别符与高敏原始流阻截在出境边界之内，确保实际出境报文与申报字段清单保持逐字段精确一致。",
                _status(data_raw),
            ],
            [
                "全链路传输加密与密钥境内独占管控",
                _clean_fact_snippet(sec_raw, f"部署双向认证加密传输通道，假名化主密钥由{src_zh}境内安全硬件独占持有", 110),
                "阻断跨境链路窃听篡改风险，并凭借境内密钥主权切断境外单点失守向境内车主实名身份回溯扩散的传导链。",
                _status(sec_raw),
            ],
            [
                f"境外接收方（{rcp_zh}）隔离运维与按期擦除",
                _clean_fact_snippet(rcp_m_raw, f"{rcp_zh}节点实施网络逻辑隔离、堡垒机最小授权审计，并按期自动销毁主备副本", 110),
                "验证境外接收方安全防护基线与境内保护标准相匹配，杜绝境外环境混存污染、越权批量导出及超期滞留隐患。",
                _status(rcp_m_raw),
            ],
            [
                "跨境法律文件刚性追责与权利救济协同",
                _clean_fact_snippet(contract_raw, "签署法定标准合同或数据出境保护协议，锁定唯一用途、禁再转移及应急通报机制", 110),
                "压实境外接收方违约连带责任与审计配合义务，确保数据主体跨境查阅、更正、删除及索赔请求具备可执行法律抓手。",
                _status(contract_raw),
            ],
        ]
    else:
        rows = [
            [
                "合法性基础与透明告知行权",
                _clean_fact_snippet(basis_raw, "按业务场景区分合同履行必需、单独同意或法定机制，配套车机端与移动端分层隐私告知", 110),
                "将每类车载数据处理严格锚定于明确法定事由，杜绝一揽子概括授权；通过显著告知与随时可触达的查阅、更正、删除及撤回同意入口，保障数据主体知情权与自决权实质落地。",
                _status(basis_raw),
            ],
            [
                "数据最小化与源端默认隐私保护",
                _clean_fact_snippet(data_raw, "执行出境字段白名单审查，车端源头去标识化与降低定位及感知数据敏感精度", 110),
                f"在数据离开车端或{src_zh}前置节点前先行剥离非必要直接识别符，按事件触发代替后台全量静默采集，从源头切断超范围收集与个体精准画像风险。",
                _status(data_raw),
            ],
            [
                "存储期限限制与到期彻底销毁",
                _clean_fact_snippet(ret_raw, "严格按业务目的设定最短必要留存周期，期满自动触发生产库与备份副本同步清除", 110),
                f"留存窗口与故障排查、法规留痕或履约周期严格挂钩，到期后对{rcp_zh}主存储及全部灾备介质执行不可恢复的安全彻底删除，消除境外长期沉淀暴露风险。",
                _status(ret_raw),
            ],
            [
                "全链路加密与最小权限隔离审计",
                _clean_fact_snippet(sec_raw, "部署双向鉴权加密传输通道、逻辑隔离存储、独立密钥管控、岗位最小授权及不可篡改审计日志", 110),
                "通信加密与完整性校验阻断跨网截获篡改；环境隔离、细粒度权限审批与全量操作留痕形成纵深防御，确保单一节点异常不演化为批量明文泄露。",
                _status(sec_raw),
            ],
            [
                "跨境契约约束与公权力调取阻断",
                _clean_fact_snippet(contract_raw, "签署具备强制约束力的跨境传输法律文件，锁定唯一处理目的、禁止擅自再转移并约定外部调取审查通报义务", 110),
                "通过连带违约责任将保护标准延伸至境外接收方；遇当地公权力机关调取请求时，强制执行职权与程序合法性审查、对宽泛要求依法抗辩并第一时间通报报批，结合源端脱敏阻却越权明文交付。",
                _status(contract_raw),
            ],
        ]
    return [
        {"type": "paragraph", "text": "合规原则与安全保护措施适当性及有效性核验表："},
        {
            "type": "table",
            "head": ["合规控制维度", "本项目已部署或拟部署措施", "适当性与有效性论证", "核验状态"],
            "rows": rows,
            "widths": [18, 28, 40, 14],
        },
    ]


def verdict_decision_blocks(
    task,
    code: str,
    values: dict[str, str],
    route: dict,
    missing_count: int = 0,
    definition: dict | None = None,
) -> list[dict]:
    """Authoritative four-tier conclusive decision table and circuit-breaker conditions tailored by document type (DPIA / LIA / TIA / Risk Report)."""
    from app.analysis import risk_items, verdict_rules

    facts = {**answered(task), **{k: v for k, v in values.items() if v}}
    source_code, recipient_code = doc_rules.ends(facts, getattr(task, "country_code", None))
    src_zh = NAMES.get(source_code or "", facts.get("source_country") or "数据发出国")
    rcp_zh = NAMES.get(recipient_code or "", facts.get("recipient_country") or "数据接收国")
    raw_risk = facts.get("risk_rating") or ""
    r_sum = risk_items.summary(raw_risk) if raw_risk else None
    high_left = (r_sum["current_counts"]["高风险"] if r_sum else 0)
    med_left = (r_sum["current_counts"]["中风险"] if r_sum else 0)
    low_left = (r_sum["current_counts"]["低风险"] if r_sum else 0)

    r_kind = route.get("route") or ""
    r_text = (route.get("text") or "").strip()
    needs_filing = r_kind in ("security_assessment", "standard_contract", "exception") or bool(
        re.search(r"安全评估|标准合同|备案|事先授权|许可|批准|申报", r_text)
    )
    blocked = missing_count > 0 or high_left > 0

    reject_mark = "■ 暂缓放行（待消除阻断项）" if blocked else "□ 不适用"
    filing_mark = "■ 适用必办" if needs_filing else "□ 按需核验"
    cond_mark = "□ 待前置阻断项消除后转入" if blocked else "■ 正式核准（有条件通过）"
    uncond_mark = "□ 不适用"

    if code == "lia":
        reject_reason = (
            f"当前尚有 {missing_count} 项权衡要素待核实或存在未化解高侵害风险；未完成三步平衡补证前不得援引合法利益开展处理。"
            if blocked
            else "企业所主张的运行安全与履约协同利益真实合法，且未触及法律绝对禁止性红线，无需否决该处理活动。"
        )
        filing_reason = (
            f"属地法域衔接核定：{r_text}"
            if r_text
            else f"须对照{src_zh}现行数据保护法规确认合法利益适用边界，涉及跨境传输时同步完结法定前置保障程序。"
        )
        cond_list = (
            "维持三步测试权利平衡须刚性履行四项条件："
            f"（1）严格限定于既定安全运维与质量履约目的，严禁将{src_zh}车载关联数据转用于商业营销或驾驶习惯画像；"
            "（2）源端去标识化与最小字段白名单保持不可绕过状态；"
            "（3）在车机与移动端显著披露处理逻辑，确保数据主体随时可免费行使反对权或关闭非核心采集且不影响基础行车安全；"
            "（4）严格执行承诺的最短留存周期与到期含备份自动擦除。"
        )
        uncond_reason = "合法利益的成立高度依赖缓解措施与退出机制对个人权益减损的动态对冲，不得脱离上述平衡条件无限制适用。"
        rationale_para = (
            "正式裁断理由与动态复审触发机制：经目的正当性、最小侵害手段排他性及五维权利权衡测试，"
            + (
                "待补齐缺失要素并消除高风险项后方可确认平衡成立。"
                if blocked
                else "在落实源端脱敏、透明告知及便捷异议通道的前提下，企业正当安全与履约利益未凌驾于自然人基本权利之上，三步测试核定为“有条件通过”。"
            )
            + f"后续若处理目的外延、数据敏感度上升或收到{src_zh}数据主体集中合理异议，须立即暂停相关处理并重新开展合法利益评估。"
        )
    elif code == "tia":
        reject_reason = (
            f"当前尚有 {missing_count} 项跨境链路要素待核实或存在 {high_left} 项未受控法域冲突风险，暂缓启动向{rcp_zh}的数据传输。"
            if blocked
            else f"经核查{rcp_zh}数据保护法制与本项目主权密钥隔离架构，未发现导致跨境传输工具彻底失效的系统性法律障碍。"
        )
        filing_reason = (
            f"跨境传输法定前置工具核定：{r_text}"
            if r_text
            else f"正式向{rcp_zh}启动传输前，须依{src_zh}法规完结事先授权审批、安全评估或标准合同备案程序。"
        )
        cond_list = (
            f"维持向{rcp_zh}传输的实质同等保护水平须刚性满足五项条件："
            f"（1）先期完结{src_zh}法定出境行政审批、备案或签约义务，严禁未批先传；"
            f"（2）假名化映射表与根密钥独占留存于{src_zh}境内安全模块，向{rcp_zh}仅开放脱敏业务参数；"
            "（3）全程启用双向证书加密专线与静态加密，封禁境外批量明文导出权限；"
            f"（4）严格执行公权力调取即时通报、管辖合法性审查与最小化抗辩合同条款，严禁向第三国再转移；"
            f"（5）单路径跨境熔断——一旦监测到{rcp_zh}调取实践恶化或链路异常，立即单独切断该跨境通道并回退{src_zh}本地存储。"
        )
        uncond_reason = f"跨法域传输受{rcp_zh}外部法律环境动态演变影响，必须受技术密码学隔离、合同抗辩条款及单路径熔断机制持续绑定。"
        rationale_para = (
            f"正式裁断理由与动态复审触发机制：对照 EDPB 第 01/2020 号建议书六步法核查自{src_zh}向{rcp_zh}的传输链路与补充措施实效，"
            + (
                "待补齐缺失项并压降高风险后方可放行跨境同步。"
                if blocked
                else f"在叠加{src_zh}源端假名化密钥留存、端到端强加密及外部调取抗辩机制后，出境数据在{rcp_zh}可持续获得与{src_zh}法定标准实质相当的保护，核定为“有条件通过”。"
            )
            + f"运行期间按年度例行复核，若{rcp_zh}数据调取立法修订、接收方控制权变更或拟扩充出境字段，须立即冻结增量传输并重启传输影响评估。"
        )
    elif code == "risk_report":
        reject_reason = (
            f"当前尚有 {missing_count} 项申报自评要素待补齐或存在 {high_left} 项未整改高风险隐患，完成闭环清零前严禁出境放行。"
            if blocked
            else "本次拟出境业务合法正当、不触碰国家安全与测绘禁区红线，且初评识别隐患均已落实工程化阻断，无需终止出境申报。"
        )
        filing_reason = (
            f"法定出境监管路径核定：{r_text}"
            if r_text
            else f"须严格依据{src_zh}数据跨境流动监管规定办结安全评估申报、标准合同备案或法定豁免留档手续。"
        )
        cond_list = (
            "正式投产运行须持续满足五项刚性安全门禁："
            f"（1）依法取得{src_zh}主管部门安全评估通过批复或完成标准合同备案（免予申报场景须完备内控PIA与协议归档）；"
            "（2）境内出境安全网关锁定字段白名单与规模计量预警阈值，超限或夹带敏感明文自动阻断；"
            f"（3）跨境加密专线、{src_zh}境内密钥独占托管及{rcp_zh}接收端只读隔离专区持续通过渗透与审计核验；"
            "（4）双方法律文件严格锁定唯一业务用途、到期连同备份安全销毁及违规连带赔偿责任；"
            "（5）建立单路径暂停熔断机制——任一子场景未获批复或触发阈值预警时立即独立熔断该出境接口，不影响境内基础车辆服务。"
        )
        uncond_reason = "汽车数据跨境流动直接关涉个人权益与公共安全，必须受规模阈值监控、法定有效期复审及到期销毁机制刚性约束。"
        risk_clause = (
            f"本次自评估共识别 {r_sum['total']} 项风险，经技术与管理整改复测后当前高风险 {high_left} 项、中风险 {med_left} 项、低风险 {low_left} 项"
            if (r_sum and r_sum["total"] > 0)
            else "全链路脱敏、加密与境外隔离措施已完成上线前安全核验"
        )
        rationale_para = (
            f"正式裁断理由与动态复审触发机制：综合出境必要性排他比对、境内与境外双端安全保障能力核查及风险压降实效（{risk_clause}），"
            + (
                "须待缺失事项补齐与高风险整改复测通过后方可进入法定申报或放行程序。"
                if blocked
                else f"自{src_zh}向{rcp_zh}传输的字段范围最小必要、整体出境安全风险可控，核定为“有条件通过”。"
            )
            + "在评估有效期内按年度开展数据出境安全合规审计；一旦累计出境人数逼近法定升级阈值、业务功能扩围或接收方安全环境发生重大变化，须立即重新开展风险自评估并履行更新申报。"
        )
    else:
        reject_reason = (
            f"当前尚有 {missing_count} 项核心业务事实待补齐或存在 {high_left} 项未压降高风险事项；在补齐缺口并将高风险压降至中、低区间前，不得直接启动跨境传输。"
            if blocked
            else "经核查，本项目不存在违背法定禁止性规定或穷尽防控措施后仍无法压降的极高固有风险，无需驳回终止业务方案。"
        )
        filing_reason = (
            f"法定前置路径核定：{r_text}"
            if r_text
            else f"须在启动正式跨境传输前对照{src_zh}及{rcp_zh}最新监管要求，确认并完结适用的申报、备案或事先授权手续。"
        )
        cond_list = (
            "须同时刚性满足以下五项放行前提："
            "（1）依法办结前述法定出境申报审批、标准合同备案或属地主管机关事先许可程序；"
            "（2）车端及前置节点严格锁定最小必要字段白名单，默认关闭无关采集并确保去标识化规则持续生效；"
            "（3）跨境双向鉴权加密通道、境外逻辑隔离存储、独立密钥管理及全量访问审计日志通过上线前核验；"
            "（4）与境外接收方正式签署跨境数据传输法律文件，落实唯一目的限制、严禁擅自再转移、公权力调取合法性审查通报及连带违约责任；"
            "（5）建立单路径暂停熔断与本地降级隔离机制——任一业务子场景或单一传输路径未获法定批准、未通过源端脱敏核验或监测到异常安全风险时，立即单独暂停该路径跨境传输并转入本地离线或境内留存预案，其余已合规路径保持逻辑隔离独立运行。"
        )
        uncond_reason = "跨法域汽车数据处理兼具动态演进性与持续法定义务约束，必须受上述前置合规条件、到期含备份销毁及周期复审机制刚性绑定，不适用无条件放行。"
        risk_clause = (
            f"风险评估台账共识别 {r_sum['total']} 项风险，经防控措施压降与整改核销后剩余高风险 {high_left} 项、中风险 {med_left} 项、低风险 {low_left} 项"
            if (r_sum and r_sum["total"] > 0)
            else "各项跨境链路安全控制、合同约束与权利响应机制已纳入统一内控核验"
        )
        rationale_para = (
            f"正式裁断理由与动态复审触发机制：综合合法性基础要件比对、多替代方案最小侵害排他论证及安全保护措施适当性核验（{risk_clause}），"
            + (
                "待上述缺失事实补齐及高风险事项完成整改降级、且全部刚性前置条件核验通过后，方可转入有条件放行状态。"
                if blocked
                else "本项目业务目的正当、出境字段控制在最小必要限度内、剩余风险处于低位可接受区间且无需启动监管事先磋商，核定为“有条件通过”（数据保护官审查意见已纳入上线门禁）。"
            )
            + "项目运行期间实行定期合规审计；一旦发生车载功能或采集字段扩张、自然人规模跨越法定申报阈值、境外接收方控制权或接收国法律执法环境重大变化、或出现数据安全事件，须立即启动专项重新评估。"
        )

    rows = [
        ["驳回或暂缓实施（终止或重新设计）", reject_mark, reject_reason],
        ["依法履行事前申报审批、备案或许可", filing_mark, filing_reason],
        ["有条件通过（落实刚性前置条件后实施）", cond_mark, cond_list],
        ["无条件通过", uncond_mark, uncond_reason],
    ]
    if definition and isinstance(definition.get("verdict_rules"), list):
        v_info = verdict_rules.compute_doc_verdict(
            code,
            facts,
            route,
            task_country_code=getattr(task, "country_code", None),
            missing_count=missing_count,
            definition=definition,
        )
        if v_info:
            rationale_para = f"综合判定结论（{v_info['label']}）：{v_info['text']}"

    return [
        {"type": "paragraph", "text": "结论性研判结果与放行条件核定表："},
        {
            "type": "table",
            "head": ["裁断决定类别", "核定结果", "法定触发依据与刚性执行要求"],
            "rows": rows,
            "widths": [24, 18, 58],
        },
        {"type": "paragraph", "text": rationale_para},
    ]


async def _source_jurisdiction_profile(session: AsyncSession, source_code: str | None, source_label: str) -> tuple[str, str]:
    """Return (primary_statute_display, regulator_display) dynamically for any sending jurisdiction."""
    if not source_code:
        return (f"{source_label or '数据发出国'}现行个人数据与汽车数据保护法律", f"{source_label or '数据发出国'}数据保护独立主管机关")
    if source_code in _EU_EEA_CODES:
        eu_law, eu_body = _EU_SUPERVISORS.get(
            source_code,
            (
                "欧盟《通用数据保护条例》（GDPR，Regulation (EU) 2016/679）及成员国实施立法",
                f"{NAMES.get(source_code, source_label or source_code)}国家数据保护独立监管机关",
            ),
        )
        return eu_law, eu_body
    law = await doc_rules.main_data_law(session, source_code)
    body = await main_regulator(session, source_code)
    law_disp = f"《{law}》" if law else f"{NAMES.get(source_code, source_label or source_code)}现行数据保护法律"
    body_disp = body or f"{NAMES.get(source_code, source_label or source_code)}数据保护主管机关"
    return law_disp, body_disp


async def dpia_overview_blocks(session: AsyncSession, task, values: dict[str, str], route: dict) -> list[dict]:
    """EDPB 2026 Section 0 (0.2–0.5): dynamic processor/recipient ledger & trigger/governance sheet."""
    facts = {**answered(task), **{k: v for k, v in values.items() if v}}
    source_code, recipient_code = doc_rules.ends(facts, task.country_code)
    src_zh = NAMES.get(source_code or "", facts.get("source_country") or "数据发出国")
    rcp_zh = NAMES.get(recipient_code or "", facts.get("recipient_country") or "数据接收国")
    law_disp, body_disp = await _source_jurisdiction_profile(session, source_code, src_zh)

    recipient_ent = _clean_fact_snippet(facts.get("recipient") or "", f"位于{rcp_zh}的指定接收主体（待补充登记全称）", 120)
    purpose_short = _first_clause_phrase(facts.get("purpose") or "", "履行既定车载服务与跨区域协同运维目标", 56)
    retention_txt = _clean_fact_snippet(facts.get("retention_and_blackbox") or "", "按最短必要业务周期留存，期满主库与备份同步安全删除", 130)
    has_dpo = bool((facts.get("dpo_opinion") or "").strip() and not str(facts.get("dpo_opinion")).startswith("暂不确定"))
    subjects_txt = facts.get("data_subjects") or ""

    pos_data_txt = _strip_negated_clauses(f"{facts.get('exterior_video_and_gnss') or ''} {facts.get('data_types') or ''}")
    pos_subj_txt = _strip_negated_clauses(subjects_txt)
    triggers = [
        f"跨法域个人数据流转（自{src_zh}向{rcp_zh}传输车载关联数据）",
        "依托智能网联车载终端及边缘网关开展自动化、事件触发式数据处理",
    ]
    has_vis_t = bool(re.search(r"视频|图像|影像|人脸|车牌|座舱|声纹|生物|点云", pos_data_txt, re.I))
    has_loc_t = bool(re.search(r"轨迹|定位|位置|GNSS|GPS|坐标", pos_data_txt, re.I))
    if has_vis_t and has_loc_t:
        triggers.append("涉及空间位置参数及车内外光学或环境感知数据处理")
    elif has_vis_t:
        triggers.append("涉及车内外光学、座舱或点云环境感知数据处理")
    elif has_loc_t:
        triggers.append("涉及特定业务或安全告警事件触发下的车辆空间位置关联数据")
    if re.search(r"路人|行人|第三方|乘员|乘客|未成年|儿童|驾驶人", pos_subj_txt):
        triggers.append("涉及除签约购车人外的驾驶人、乘员或道路通行第三方权益保护")

    proc_rows = [
        ["境外接收主体与所在法域", f"{recipient_ent}（接收法域：{rcp_zh}）"],
        ["限定处理范围与合同约束", f"仅限用于{purpose_short}所需最小字段范围；通过具强制执行力的跨境数据传输协议锁定用途，严禁未经事先书面授权向第三国或外部第三方再转移。"],
        ["存储位置、留存期限与销毁", retention_txt + "。"],
        ["上线前置门禁与复审周期", f"{(route.get('text') or '依法完结发出国前置合规程序后启用').rstrip('。')}；投产后按年度例行复审，遇功能扩容、接收方变更或法域规则修订立即重评。"],
    ]

    sheet_rows = [
        ["适用法域主法源与监管机关", f"{law_disp}；主管监管机关：{body_disp}"],
        ["评估方法论与编制基准", "参照欧洲数据保护委员会（EDPB）《数据保护影响评估标准范本（2026 年第 1.0 版）》0–6 章体系，结合可能性×严重程度量化标尺与补充措施实效评估编制。"],
        ["触发本次评估的法定事由", "；".join(f"（{i}）{t}" for i, t in enumerate(triggers, 1)) + "。"],
        [
            "跨部门联合评估与审查分工",
            "研发部门核定端云采集与白名单链路，信息安全团队验证脱敏加密控制，法务合规与数据保护官独立履行前置合规签批"
            + ("（已出具书面审查意见并纳入上线门禁）。" if has_dpo else "（投产前须完成书面签批归档）。"),
        ],
    ]

    return [
        {"type": "paragraph", "text": "境外数据接收主体、履约约束与运行周期档案表（对应标准范本 0.2、0.4）："},
        {"type": "table", "head": ["核查维度", "本项目核实内容"], "rows": proc_rows, "widths": [28, 72]},
        {"type": "paragraph", "text": "评估方法基准、法定触发事由与跨部门审查档案表（对应标准范本 0.5）："},
        {"type": "table", "head": ["档案要素", "核定说明"], "rows": sheet_rows, "widths": [28, 72]},
    ]


async def dpia_special_data_note_blocks(session: AsyncSession, task, values: dict[str, str]) -> list[dict]:
    """EDPB 2026 Section 1.1.a below-table Special-Category / Sensitive Personal Data Determination Note (concise, zero parroting)."""
    facts = {**answered(task), **{k: v for k, v in values.items() if v}}
    source_code, _ = doc_rules.ends(facts, task.country_code)
    src_zh = NAMES.get(source_code or "", facts.get("source_country") or "数据发出国")
    law_disp, _ = await _source_jurisdiction_profile(session, source_code, src_zh)

    data_raw = (facts.get("exterior_video_and_gnss") or facts.get("data_types") or "").strip()
    sens_raw = (facts.get("sensitive_pi") or "").strip()
    sens_kinds = _first_clause_phrase(facts.get("sensitive_pi_kinds") or "", "高敏感车载感知特征", 36)
    pos_scope = _strip_negated_clauses(
        f"{data_raw} {facts.get('data_types') or ''} {sens_kinds if sens_raw.startswith('是') else ''}"
    )
    cleaned_mask_probe = _PROHIBITED_UNMASKED_RE.sub(" ", data_raw)
    is_unmasked = bool(_UNMASKED_RE.search(cleaned_mask_probe))
    masked = (not is_unmasked) and bool(
        re.search(r"脱敏|去标识|匿名|模糊|遮蔽|擦除|哈希|降精|不出车|本地处理|相对局部|假名化", cleaned_mask_probe)
    )
    has_bio_or_health = bool(re.search(r"生物识别|指纹|虹膜|声纹|静脉|人脸识别|疲劳监测|驾驶员身份识别|健康|医疗|生理", pos_scope))
    has_vision = bool(re.search(r"视频|图像|影像|摄像头|环视|人脸|车牌|座舱画面|点云", pos_scope, re.I))
    has_loc = bool(re.search(r"轨迹|定位|位置|GNSS|GPS|坐标|网格编码", pos_scope, re.I))

    if not data_raw or data_raw == DRAFT_BLANK:
        note = (
            f"特殊类别与敏感个人数据边界判定说明：当前数据字段明细尚待补齐（{DRAFT_BLANK}）。"
            f"上线前须对照{law_disp}逐项核查是否涉及生物识别模板、健康生理特征或高精度行踪轨迹，未完成字段级定性前不得开启跨境传输。"
        )
    elif has_bio_or_health and (sens_raw.startswith("是") or not masked):
        note = (
            f"特殊类别与敏感个人数据边界判定说明：对照{law_disp}，上表所列字段涉及{sens_kinds}等特殊类别或敏感个人数据。"
            "此类处理严禁援引一般合同履行或概括合法利益作为依据，须严格以数据主体事前单独、明示同意（或法定重大人身安全紧急避险例外）为前提，且原始生物特征比对须锁定在车端安全隔离区内完成，禁止向境外汇集可逆原始生物识别模板。"
        )
    elif has_vision and has_loc and masked:
        note = (
            f"特殊类别与敏感个人数据边界判定说明：对照{law_disp}关于特殊类别（生物识别）数据的构成要件，上表所涉光学影像与空间定位参数在进入跨境链路前已完成不可逆人脸车牌遮蔽及坐标降精转换，全程不提取用于唯一识别特定自然人的面部、声纹或步态生物特征模板。"
            "因此，出境数据包不构成旨在唯一识别自然人的特殊类别生物识别数据处理；对仍具间接关联性的工况与去标识化位置参数，统一按高敏数据标准执行加密与最小权限隔离。"
        )
    elif has_vision and masked:
        note = (
            f"特殊类别与敏感个人数据边界判定说明：对照{law_disp}关于特殊类别（生物识别）数据的构成要件，上表所涉光学或点云感知片段在出境前已完成不可逆人脸与车牌遮蔽，全程不提取用于唯一识别特定自然人的生物特征模板。"
            "因此，出境数据包不构成特殊类别生物识别数据处理，并按高敏工程数据标准执行加密与受控访问隔离。"
        )
    elif has_loc and masked:
        note = (
            f"特殊类别与敏感个人数据边界判定说明：对照{law_disp}核验上表字段，本项目不采集车内外音视频与生物识别特征，仅在特定业务或告警事件触发时附带经源端降精模糊处理的区域位置与去标识化车辆令牌，不构成特殊类别生物识别数据处理或连续高精度行踪画像；所涉关联字段全程按高敏标准实施加密与只读权限管控。"
        )
    elif has_vision or has_loc:
        note = (
            f"特殊类别与敏感个人数据边界判定说明：对照{law_disp}，上表所涉车内外光学影像或高精度空间行踪轨迹尚未完成源端不可逆去标识化与精度降级（未脱敏高敏状态），存在直接识别自然人面部特征或精准还原个人生活行踪的重大隐私风险。"
            "未在车端或境内安全网关落实实时人脸车牌遮蔽、坐标降精脱敏并依法取得数据主体单独明示同意及前置出境许可前，严禁将未脱敏原始视频流或连续精确轨迹直接向境外传输。"
        )
    elif sens_raw.startswith("是"):
        note = (
            f"特殊类别与敏感个人数据边界判定说明：对照{law_disp}，本项目虽涉及敏感个人数据范围，但不包含种族、宗教信仰、政治观点、健康生理指标，亦不提取唯一识别自然人的生物特征模板。"
            "所涉敏感字段仅在取得单独告知授权或满足法定必要条件后按白名单最小范围出境，并受字段级加密与最短留存期限刚性约束。"
        )
    else:
        note = (
            f"特殊类别与敏感个人数据边界判定说明：对照{law_disp}核验上表全部字段，本次跨境流转仅限车辆运行诊断、服务履约或去标识化工程参数，不涉及种族、宗教信仰、健康医疗信息，亦不开展唯一识别自然人的生物特征提取处理，不触发特殊类别个人数据禁止处理条款。"
        )

    return [{"type": "paragraph", "text": note}]


async def dpia_legal_basis_blocks(session: AsyncSession, task, values: dict[str, str], route: dict) -> list[dict]:
    """EDPB 2026 Section 2.1.a/b: Archetype-Aware Multi-Purpose Legal Basis Table + Crisp Below-Table 3-Step Balancing Test."""
    facts = {**answered(task), **{k: v for k, v in values.items() if v}}
    source_code, recipient_code = doc_rules.ends(facts, task.country_code)
    src_zh = NAMES.get(source_code or "", facts.get("source_country") or "数据发出国")
    rcp_zh = NAMES.get(recipient_code or "", facts.get("recipient_country") or "数据接收国")
    law_disp, _ = await _source_jurisdiction_profile(session, source_code, src_zh)
    lb_rule = await doc_rules.legit_basis(session, source_code) if source_code else {"state": "未知", "cite": ""}

    stype = str(getattr(task, "scenario_type", "") or "").lower()
    raw_scenario_txt = f"{facts.get('scenario', '')} {facts.get('purpose', '')} {facts.get('exterior_video_and_gnss', '')}"
    scenario_txt = _strip_negated_clauses(raw_scenario_txt)
    has_legit_in_law = (source_code in _EU_EEA_CODES) or (lb_rule.get("state") == "有")
    route_short = _first_clause_phrase(route.get("text") or "", f"依法履行{src_zh}跨境传输法定保障机制", 64)

    is_av_or_map = stype in ("autonomous_trial", "hd_mapping") or (
        not stype and bool(re.search(r"自动驾驶|路测|道路测试|高精地图|测绘|点云", scenario_txt))
    )
    is_cockpit = stype == "cockpit_biometrics" or (
        not stype and bool(re.search(r"座舱|生物识别|疲劳监测|声纹|人脸识别", scenario_txt))
    )
    is_sales = stype == "sales_service" or (
        not stype and bool(re.search(r"销售|售后|客户数据|三包|维保|索赔", scenario_txt))
    )

    if is_av_or_map:
        rows = [
            [
                "受雇测试安全员或测绘作业人员履职与接管记录管理",
                "履行劳动或委托作业合同所必需，并取得书面单独同意",
                "测试车辆运行监控与人工接管标记直接关联道路测试作业安全职责；全员事前签署书面知情同意并以假名化工号替代明文姓名。",
            ],
            [
                "公开测试路段周边环境感知采集与源端实时脱敏",
                "正当合法利益与源端实时匿名化（辅以车身外部显著公示）" if has_legit_in_law else f"源端实时不可逆脱敏排除识别性（遵循{law_disp}最小必要要求）",
                "开放道路无法预先逐一取得偶发路人同意；车端及本地安全站在出境前完成人脸、车牌不可逆擦除与绝对门牌坐标剥离，确保原始可识别画面不出境。",
            ],
            [
                f"经质检合格的脱敏工程片段向{rcp_zh}研发节点跨境传输",
                f"法定跨境传输保障机制（{route_short}）",
                "严格以完结发出国法定事先授权、备案或标准合同程序为启动门禁，接收端仅限感知与定位安全性验证，严禁转作商业画像。",
            ],
        ]
        lia_focus = "提升智能驾驶系统在复杂道路气象条件下的主动避障安全性与感知可靠性"
    elif is_cockpit:
        rows = [
            [
                "基础车联网账号绑定与驾乘配置同步服务",
                "履行车联网用户服务协议所必需",
                "仅同步维持车辆基础互联与账户设置所必需的配置参数，不将非必要采集设为购车或基础驾驶功能的前置捆绑条件。",
            ],
            [
                "座舱乘员状态感知、语音交互或生物特征处理",
                "数据主体在充分知情下自由作出的单独、明示同意",
                "通过车载中控屏提供独立颗粒化授权开关与显著告知，原始特征比对优先在车端安全区本地完成，支持随时一键关闭并清除记录。",
            ],
            [
                f"去标识化座舱功能异常日志向{rcp_zh}技术团队协同排查",
                "控制者正当合法利益及法定跨境合同约束" if has_legit_in_law else f"有效同意及{route_short}",
                "仅在故障触发时回传剥离音视频明文与生物特征后的诊断错误码，用于定位软件缺陷并防范系统性交互故障。",
            ],
        ]
        lia_focus = "排查座舱软件交互故障并保障行车注意力监测系统的运行可靠性"
    elif is_sales:
        rows = [
            [
                "整车销售交付、三包质保兑现与售后维修工单履约",
                "履行购车买卖及售后服务合同所必需",
                "处理车主联系方式、车架号与维保记录系完成保修核定、备件调配与召回通知的客观必要条件，字段严格限于履约范围。",
            ],
            [
                "客户服务关怀回访、满意度调研及可选增值权益推送",
                "客户知情且自由作出的明确同意（含一键退订通道）",
                "与基础售后维保解耦，单独征求客户授权并在每次触达中保留便捷退订或撤回入口，撤回后立即停止非履约类触达。",
            ],
            [
                f"跨区域质量索赔核验与备件供应链协同（传往{rcp_zh}）",
                "控制者正当合法利益及跨境标准合同约束" if has_legit_in_law else f"履行合同必需及{route_short}",
                "对工单实行去标识化编码处理，仅向境外质量与备件支持团队开放故障件核验所需最小字段，严禁用于跨市场商业营销。",
            ],
        ]
        lia_focus = "完成跨区域售后质量索赔核验、备件精准调配与批次性质量缺陷追溯"
    else:
        rows = [
            [
                "在用车辆三电与核心电控运行安全监测、远程故障诊断及安全补丁升级",
                "履行购车质保与车联网安全服务合同所必需（兼涉法定产品安全义务）",
                "仅在告警或诊断事件触发时同步白名单内的工况参数与去标识化车辆标识，系消除行车安全隐患与履行维保义务所必需。",
            ],
            [
                f"跨法域远程诊断协同授权管理及车主可选网联功能（发往{rcp_zh}）",
                "数据主体知情、特定且自由作出的明确同意（叠加法定出境机制）",
                "通过车机屏幕及移动端隐私中心提供分层告知，列明境外接收方、留存期限与行权通道，支持随时撤回且不影响车辆基础行驶安全。",
            ],
            [
                f"车云通信链路安全防护及跨批次缺陷基线核验（协同{rcp_zh}工程节点）",
                "控制者正当合法利益（辅以源端脱敏与最小权限隔离）" if has_legit_in_law else f"法定安全保护义务及{route_short}",
                "仅针对去标识化异常报文与安全告警摘要开展核验，以防范车云网络入侵并识别批次性质量隐患，严禁开展个人行为画像。",
            ],
        ]
        lia_focus = "保障在用车辆道路运行安全、防范车云网络攻击及定位跨批次软硬件缺陷"

    if source_code in _EU_EEA_CODES:
        law_anchor = "《欧盟通用数据保护条例》（GDPR）第 6 条第 1 款 (f) 项"
    elif lb_rule.get("state") == "有" and lb_rule.get("cite"):
        law_anchor = f"{src_zh}{lb_rule['cite']}"
    else:
        law_anchor = f"{law_disp}比例原则（注：属地法未单列一般合法利益事由时，本测试作为内控比例性核验留档，对外法定依据仍以有效同意或合同必需为准）"

    lia_summary = (
        f"合法利益三步平衡测试说明（适用基准：{law_anchor}）："
        f"（1）目的正当性测试：本单位主张的利益聚焦于{lia_focus}，属于具体、合法的车辆运行安全与质量保障利益，排除任何未经授权的商业画像用途；"
        "（2）手段必要性测试：仅靠单车离线算力或完全剥离关联标识无法完成跨批次故障复现与定向风险处置，采用源端去标识化后的事件触发式最小字段处理系侵入性最小的必要手段；"
        "（3）权益平衡测试：依托默认关闭无关采集、源端去标识化与精度收敛、严格留存上限及便捷异议与撤回通道，数据处理对相关自然人的潜在影响已被控制在合理预期之内，未凌驾于数据主体基本权利与自由之上。"
    )

    return [
        {"type": "paragraph", "text": "分业务场景处理目的与合法性基础映射表（对应标准范本 2.1.a、2.1.b）："},
        {
            "type": "table",
            "head": ["细分处理场景与用途", f"适用的法定合法性基础（{src_zh}法域）", "要件适格性与边界核验说明"],
            "rows": rows,
            "widths": [26, 26, 48],
        },
        {"type": "paragraph", "text": lia_summary},
    ]


def dpia_stakeholders_blocks(task, values: dict[str, str]) -> list[dict]:
    """EDPB 2026 Section 5 (5.1 DPO Advice & 5.2 Data Subject Views / Rights Channels): structured table before prose."""
    facts = {**answered(task), **{k: v for k, v in values.items() if v}}
    dpo_raw = (facts.get("dpo_opinion") or "").strip()
    notice_raw = (facts.get("consent_and_notice") or "").strip()
    subjects_raw = _first_clause_phrase(facts.get("data_subjects") or "", "受评车型车主、乘员及关联服务用户", 40)

    dpo_cell = (
        dpo_raw
        if dpo_raw and not dpo_raw.startswith("暂不确定")
        else "（待企业数据保护官或合规法务负责人在正式投产前签署书面审查意见；审查重点涵盖最小白名单锁定、跨境保障工具完备性及剩余风险可接受性）"
    )
    dpo_status = "已记录审查意见并纳入放行门禁" if (dpo_raw and not dpo_raw.startswith("暂不确定")) else "待签署确认（上线前刚性门禁）"

    notice_cell = _clean_fact_snippet(
        notice_raw,
        f"面向{subjects_raw}通过车载终端界面、移动端隐私中心及随车说明提供分层透明告知",
        150,
    )
    notice_status = "已建立分层告知与行权通道" if (notice_raw and notice_raw != DRAFT_BLANK) else "待补充核验"

    rows = [
        ["数据保护官（DPO）/ 合规法务独立审查意见（5.1）", dpo_cell, dpo_status],
        ["数据主体透明告知与意见征询反馈机制（5.2）", notice_cell, notice_status],
        [
            "跨法域数据主体权利响应机制（查阅/更正/删除/撤回/反对）",
            "在车机端与移动端设立统一隐私权利受理入口，境外接收方通过合同绑定协同配合接口，确保在数据发出国法定时限内同步完成查询导出、更正或含备份彻底删除。",
            "合同与系统接口双重绑定",
        ],
    ]
    return [
        {"type": "paragraph", "text": "利益相关方参与、数据保护官审查意见及权利保障核验表（对应标准范本 5.1、5.2）："},
        {
            "type": "table",
            "head": ["治理与参与维度", "审查意见与权利保障安排", "核验状态"],
            "rows": rows,
            "widths": [28, 54, 18],
        },
    ]


async def lia_balancing_blocks(session: AsyncSession, task, values: dict[str, str]) -> list[dict]:
    """Dynamic 5-dimension Legitimate Interest Balancing Matrix placed at the top of LIA Chapter 3, followed below by legal prose."""
    facts = {**answered(task), **{k: v for k, v in values.items() if v}}
    source_code, _ = doc_rules.ends(facts, task.country_code)
    src_zh = NAMES.get(source_code or "", facts.get("source_country") or "数据发出国")
    lb_rule = await doc_rules.legit_basis(session, source_code) if source_code else {"state": "未知", "cite": ""}

    purpose = _first_clause_phrase(facts.get("purpose") or "", "保障车辆运行安全与远程故障排查", 48)
    data_txt = _clean_fact_snippet(facts.get("exterior_video_and_gnss") or "", DRAFT_BLANK, 90)
    notice_txt = _clean_fact_snippet(facts.get("consent_and_notice") or "", DRAFT_BLANK, 90)
    measures_txt = _clean_fact_snippet(facts.get("risk_measures") or "", DRAFT_BLANK, 90)
    ret_txt = _clean_fact_snippet(facts.get("retention_and_blackbox") or "", DRAFT_BLANK, 90)

    statute_status = (
        f"{src_zh}法规明确认可正当合法利益事由（{lb_rule['cite']}）"
        if lb_rule.get("state") == "有"
        else (
            f"{src_zh}已收录条款（{lb_rule['cite']}）未单列一般合法利益事由，本评估作为内控比例性权衡依据并配合法定同意适用"
            if lb_rule.get("state") == "无"
            else f"结合{src_zh}现行数据保护法比例原则与正当性要件核验"
        )
    )

    rows = [
        ["发出国法定地位核验", statute_status, "确立合法利益能否单独适用或须叠加同意适用"],
        ["数据性质与敏感度边界", data_txt, "源端剥离直接身份标识与非必要高精度感知帧，压降潜在隐私侵害烈度"],
        ["数据主体合理预期", f"围绕{purpose}开展处理；告知安排：{notice_txt}", "契合购车用车及道路安全维护场景下的正常合理预期，杜绝隐蔽突袭式采集"],
        ["控制权与便捷退出机制", "提供功能级开关、随时反对或关闭采集通道及一键撤回机制", "确保数据主体对非核心安全必需的数据流转保有实质自决权"],
        ["影响持续时间与对冲控制", f"留存周期：{ret_txt}；技术对冲：{measures_txt}", "通过最短留存窗口与加密隔离对冲残余风险，确保企业利益不凌驾于个人权利之上"],
    ]
    return [
        {"type": "paragraph", "text": "企业正当利益与数据主体基本权利五维权衡测试表："},
        {
            "type": "table",
            "head": ["权衡评估维度", "本项目事实与控制状态", "对权利平衡结果的影响判定"],
            "rows": rows,
            "widths": [22, 44, 34],
        },
    ]


async def tia_six_steps_blocks(session: AsyncSession, task, values: dict[str, str], route: dict) -> list[dict]:
    """Dynamic EDPB Recommendations 01/2020 Six-Step Transfer Impact Overview Table placed at the top of TIA Chapter 1."""
    facts = {**answered(task), **{k: v for k, v in values.items() if v}}
    source_code, recipient_code = doc_rules.ends(facts, task.country_code)
    src_zh = NAMES.get(source_code or "", facts.get("source_country") or "数据发出国")
    rcp_zh = NAMES.get(recipient_code or "", facts.get("recipient_country") or "数据接收国")
    rcp_law_rows = await receiving_law(session, recipient_code) if recipient_code else []
    rcp_law_summary = rcp_law_rows[0][1] if rcp_law_rows else f"{rcp_zh}现行数据保护立法"
    data_short = _first_clause_phrase(facts.get("exterior_video_and_gnss") or "", DRAFT_BLANK, 56)

    rows = [
        ["第一步：梳理跨境传输全貌", f"自{src_zh}向{rcp_zh}传输：{data_short}", "锁定端侧采集、预处理、跨境链路与境外接收存储边界"],
        ["第二步：确认法定传输保障工具", _clean_fact_snippet(facts.get("cross_border_mechanism") or route.get("text") or "", DRAFT_BLANK, 90), "核验所依托的标准合同、事先授权或安全评估机制适格性"],
        ["第三步：评估接收国法律与执法实践", f"{rcp_law_summary}及当地公权力调取程序比例边界", "识别当地执法调取是否会实质削弱传输工具保护效力"],
        ["第四步：部署技术合同组织补充措施", _clean_fact_snippet(facts.get("supplementary_measures") or "", DRAFT_BLANK, 90), "依托源端脱敏、高强度加密、密钥受控与调取抗辩通报弥补潜在法域落差"],
        ["第五步：履行法定程序性前置步骤", _clean_fact_snippet(route.get("text") or "", f"按{src_zh}法规办结前置备案、申报或授权", 90), "确保正式启动传输前完结全部法定行政程序"],
        ["第六步：建立动态复评与熔断机制", _clean_fact_snippet(facts.get("reevaluation") or "", "按年度例行复评；遇接收国法律变更或调取风险上升立即暂停重评", 90), "持续维持与数据发出国实质同等的保护水平"],
    ]
    return [
        {"type": "paragraph", "text": "跨境传输影响评估（TIA）六步法核查总览表（对标 EDPB 第 01/2020 号建议书）："},
        {
            "type": "table",
            "head": ["评估步骤", "本项目核查事实与机制", "阶段控制目标"],
            "rows": rows,
            "widths": [24, 46, 30],
        },
    ]


async def slots(
    session: AsyncSession,
    task,
    code: str,
    values: dict[str, str],
    *,
    missing_count: int = 0,
    definition: dict | None = None,
) -> dict[str, list[dict]]:
    """The platform-written blocks this document's template places."""
    from app.analysis import verdict_rules

    out: dict[str, list[dict]] = {}
    if code in ("risk_report", "remediation", "dpia", "lia", "tia"):
        merged_vals = {**answered(task), **{k: v for k, v in values.items() if v}}
        r = await doc_rules.route(session, merged_vals, task.country_code)
        out["route"] = [{"type": "paragraph", "text": "出境路径：" + r["text"]}] if r.get("text") else []
        out["necessity_comparison"] = necessity_alternatives_blocks(task, code, merged_vals)
        out["safeguard_effectiveness"] = safeguard_adequacy_blocks(task, code, merged_vals, r)
        out["verdict_decision"] = verdict_decision_blocks(
            task, code, merged_vals, r, missing_count=missing_count, definition=definition
        )
        if code == "dpia":
            out["dpia_ch0_overview"] = await dpia_overview_blocks(session, task, merged_vals, r)
            out["dpia_special_data_note"] = await dpia_special_data_note_blocks(session, task, merged_vals)
            out["dpia_legal_basis"] = await dpia_legal_basis_blocks(session, task, merged_vals, r)
            out["dpia_stakeholders"] = dpia_stakeholders_blocks(task, merged_vals)
        elif code == "lia":
            out["lia_balancing"] = await lia_balancing_blocks(session, task, merged_vals)
        elif code == "tia":
            out["tia_six_steps"] = await tia_six_steps_blocks(session, task, merged_vals, r)
        if code in ("risk_report", "remediation"):
            from app.analysis import risk_items

            items = await issues(session, task, merged_vals, r)
            has_verif = bool((values.get("verification") or answered(task).get("verification") or "").strip())
            out["issues"] = issue_blocks(items)
            out["fix_route"] = route_blocks(items, verified=has_verif)
            out["advice"] = advice_blocks(items)
            raw_risk = values.get("risk_rating") or answered(task).get("risk_rating") or ""
            out["risk_evolution"] = risk_items.evolution_blocks(raw_risk)
            if code == "remediation":
                rem_v = verdict_rules.compute_doc_verdict(
                    "remediation",
                    merged_vals,
                    r,
                    task_country_code=getattr(task, "country_code", None),
                    missing_count=missing_count,
                    definition=definition,
                )
                out["remediation_verdict"] = (
                    [
                        {
                            "type": "paragraph",
                            "text": f"整改闭环与放行条件综合判定结论（{rem_v['label']}）：{rem_v['text']}",
                        }
                    ]
                    if rem_v
                    else []
                )
    if code == "admission":
        a = await admission_blocks(session, task, values, definition=definition)
        out.update({f"admission_{k}": v for k, v in a.items()})
    return out
