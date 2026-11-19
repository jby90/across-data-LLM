# ruff: noqa: E501
"""The fixed facts every admission item card shows, read from the item's own law: who may apply and
the hard thresholds the applicant must meet (years of registration, capital, turnover, shareholding,
nationality, licences held) with the original document that proves each, whether the product must
be tested in the country, whether a local representative is required, the application language,
how long the approval is valid, which body handles which part when two are named, and which laws a
guide says it implements. Only what the provided text states is kept; anything it does not state is
"待核实" (to be confirmed) — never a guess. A field an administrator corrected (``edited`` holds
``profile:<field>``) is left alone by the rebuild."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admission.models import AdmissionEntry, AdmissionStep
from app.analysis.llm import LlmUnavailableError, call_llm_json
from app.ingestion.models import Clause

# 同一部法规里写到申请人条件、门槛、语言、有效期、当地代表、本地检测的条文（事项所依据的那一条常常不写这些）
HINTS = re.compile(
    r"langue|arabe|français|English|Arabic|language|لغة|العربية|valable|validit|durée|renouvel|valid for|period of|مدة|صلاحية|"
    r"représentant|mandataire|local agent|local representative|registered in|établi au|ممثل|وكيل|laboratoire|test report|testing|"
    r"essai|اختبار|مختبر|فحص|capital|رأس ?المال|رأسمال|turnover|chiffre d.affaires|حجم الأعمال|رقم الأعمال|"
    r"شريك|associé|partner|share|%|نسبة|السجل التجاري|registre du commerce|commercial regist|بطاقة|carte|card|"
    r"الجنسية|nationalit|سنة على الأقل|at least one year|au moins|لا يقل|not less than|minimum",
    re.IGNORECASE,
)
WHOLE_DOC = 7000  # 依据文件不长（办事指南、单页规定）时整份给模型


async def _related(session: AsyncSession, clause_id, cache: dict) -> str:
    if clause_id is None:
        return ""
    clause = await session.get(Clause, clause_id)
    if clause is None:
        return ""
    if clause.version_id not in cache:
        rows = (await session.scalars(select(Clause).where(Clause.version_id == clause.version_id).order_by(Clause.ordinal))).all()
        cache[clause.version_id] = [(c.id, " ".join((c.original_text or "").split())) for c in rows]
    rows = cache[clause.version_id]
    if sum(len(t) for _, t in rows) <= WHOLE_DOC:
        return "\n".join(t for cid, t in rows if cid != clause_id)
    out = []
    for cid, text in rows:
        if cid == clause_id:
            continue
        m = HINTS.search(text)
        if m:
            out.append(text[max(0, m.start() - 160) : m.end() + 240])
        if len(out) >= 8:
            break
    return "\n".join(out)


FIELDS = ("eligibility", "local_test", "local_rep", "language", "validity")
EXTRA = ("eligibility_proofs", "authority_roles", "cited_laws")
UNKNOWN = "待核实"
NOT_APPLICABLE = "不适用"
LIMITS = {"eligibility": 260, "local_test": 120, "local_rep": 90, "language": 50, "validity": 60, "authority_roles": 160}

SYSTEM = """你在整理外国准入事项（许可、批准、认证、登记、申报）的固定信息，读者是准备申请材料的中国车企。每个事项给你名称、摘要、办理步骤、所依据条文原文和同一文件的相关条文。
只依据给你的文字（相关条文须确实说的是这个事项），文字没写的一律写“待核实”，不得推测或凭常识补。对每个事项回答：
- eligibility：谁可以申请 + 硬门槛。先写申请人是谁（如“在埃及商业登记的进口企业”，不写“申请人：”这类标签），再逐条写条文里的门槛（不写“门槛：”，不写“自然人还是法人”这类问句）：登记或经营年限、实缴资本、营业额、持股或合伙份额比例、国籍、须持有的证照（如进口卡、税卡）。条文有的门槛必须全部写上，数字照原文；用分号隔开，不超过 220 字。只写申请人和门槛，办理要求、要交的材料、程序不写在这里；没有门槛就只写申请人是谁，不要写“条文未规定……”之类的话；文字连申请人是谁都没写，只写“待核实”。条文的说法不通顺时改成通顺的中文法律表述（如“自然人独资企业”而不是“个人机构”）。
- applicant：申请人是谁（一句话，如“在埃及商业登记的进口企业”）；没写就“待核实”。
- conditions：申请人本身的身份要求和每一条硬门槛，各配一份证明它的原件，数组，每项 {"condition": 门槛转述, "proof": 证明它的原件名称, "what": 一句话说原件要显示什么}，如 {"condition": "商业登记满 1 年", "proof": "商业登记摘录", "what": "显示登记日期"}、{"condition": "进口负责经理为埃及国籍", "proof": "进口负责经理的身份证明", "what": "显示埃及国籍"}、{"condition": "持有税卡", "proof": "税卡", "what": "有效期内"}、{"condition": "须为法人", "proof": "法人登记证明", "what": "显示法人身份"}、{"condition": "须指定数据保护负责人", "proof": "数据保护负责人任命书", "what": "写明姓名与资质"}。每条都要有 proof；没有任何要求给 []。
- eligibility_proofs：同 conditions 的 proof 列表，每项 {"name": proof, "what": what}。
- applies_to：这个事项是给谁办的。车企、车辆进口商、车载设备厂商、在当地处理车辆数据的企业都可能要办的写 ""；只有特定行业主体才办的（如电子认证服务机构、电信运营商、频谱使用者）写那个主体，如“电子认证服务机构”。
- name_zh：事项的规范中文名，写成“对象 + 许可 / 登记 / 认证 / 批准 / 备案”，如“进口商登记”“个人数据跨境传输许可”“电信设备型式认证”，不放材料说明、括号说明和外文缩写，不超过 20 字。
- law_ids：这个事项所依据的法律、法令、部令，从“可选的法规”里选编号（如 ["L3"]）。依据条文本身就是法律/法令的，选它自己；依据的是办事指南、说明、问答的，选它所执行的那部法律（指南里写了的，或按主管机构和事项明显所依据的上位法）；都对不上给 []。
- local_test：是否须在该国本地检测/测试（“需要：……”或“不需要：……”），检测报告由谁出具也写在这里
- local_rep：境外企业是否须指定当地代表/代理替它办理（“需要：……”或“不需要：……”）。如果申请人本身就必须是当地主体（如须在当地商业登记、须为本国企业），写“不适用”
- language：申请材料须用的语言
- validity：批准/证书的有效期（如“5 年，可续”）；申报类无有效期的写“长期有效（变更时重新申报）”仅当文字这样写
- authority_roles：文字写了两个及以上机构参与时，写各管哪一段（如“A 受理申请并发证；B 负责检测”）；只有一个机构写 ""
- cited_laws：文字里写明这个事项依据的法律、法令、部令（编号和年份照原文，如“1982 年第 121 号法律”“2023 年第 173 号法律”），数组；没有写 []
local_test、local_rep、language、validity 文字没写的，只写“待核实”三个字，不加解释。
转述时不改变原话的意思和强弱；条文里的分组、类别、方案名、缩写（如 Group A、VoC）第一次出现时用中文说明它指什么。不要在答案后面加括号补充自己的判断。不要用中国特有的制度说法（等级保护、网信办、安全评估申报等），按这个国家的法规写。
输出 JSON：{"items": [{"i": 编号, "name_zh": "...", "applicant": "...", "eligibility": "...", "conditions": [], "eligibility_proofs": [], "applies_to": "", "local_test": "...", "local_rep": "...", "language": "...", "validity": "...", "authority_roles": "", "cited_laws": [], "law_ids": []}]}"""


# 申请时要写的说明、要交的表格是材料，不是资格门槛（“须说明……”“须描述……”“须按……表格”）
NOT_A_CONDITION = re.compile(r"^(?:须|应|需)(?:说明|描述|写明|确定|列明|提交|提供|按.{0,12}表格|填写)")
FOREIGN_PAREN = re.compile(r"[（(][^）)]*[؀-ۿÀ-ɏ][^）)]*[）)]")


def tidy_conditions(conds: list[dict]) -> list[dict]:
    """Keep the real thresholds, each with a proof; foreign words in brackets go (shown in the detail)."""
    out = []
    for c in conds:
        cond, proof = c.get("condition", "").strip(), c.get("proof", "").strip()
        if not cond or not proof or UNKNOWN in proof or NOT_A_CONDITION.match(cond):
            continue
        out.append({"condition": cond, "proof": FOREIGN_PAREN.sub("", proof).strip(), "what": FOREIGN_PAREN.sub("", c.get("what", "")).strip()})
    return out


META = re.compile(r"[；;，,。]?\s*(?:硬门槛[:：]\s*)?(?:条文|文字)(?:未|没有?)(?:规定|写明|列明|限定)[^；;。]*[；;。]?")


def _clean(v, field: str = "") -> str:
    v = " ".join(str(v or "").split())
    if UNKNOWN in v and field != "eligibility":
        return UNKNOWN  # “待核实：条文未规定……”只留“待核实”
    v = META.sub("", v).replace(UNKNOWN, "").strip("；;，,。 ")
    v = re.sub(r"第\s*(\d+)\s*类", r"第 \1 类", v)  # 全站写法：“第 1 类”
    limit = LIMITS.get(field, 60)
    if len(v) > limit:  # 超长时在最后一个分句处收住，不截半句
        cut = max(v.rfind(x, 0, limit) for x in "；。;")
        v = v[:cut] if cut > limit // 2 else v[:limit]
    return v or UNKNOWN


def _proofs(v) -> list[dict]:
    out = []
    for p in v if isinstance(v, list) else []:
        if isinstance(p, dict) and str(p.get("name") or "").strip():
            out.append({"name": " ".join(str(p["name"]).split())[:60], "what": " ".join(str(p.get("what") or "").split())[:80]})
    return out[:8]


async def fill(session: AsyncSession, *, redo: bool = False, country: str | None = None) -> dict:
    import app.models_registry  # noqa: F401  模型调用要写出站审计

    q = select(AdmissionEntry).where(AdmissionEntry.review_status.in_(("pending_review", "approved")), AdmissionEntry.merged_into.is_(None))
    if country:
        q = q.where(AdmissionEntry.country_code == country.upper())
    rows = [e for e in (await session.scalars(q)).all() if redo or not e.profile or "eligibility_proofs" not in (e.profile or {})]
    steps: dict = {}
    for st in (await session.scalars(select(AdmissionStep).where(AdmissionStep.entry_id.in_([e.id for e in rows])).order_by(AdmissionStep.seq))).all():
        steps.setdefault(st.entry_id, []).append(st)
    done = tokens = 0
    cache: dict = {}
    from sqlalchemy import text as _text

    from app.admission import names as _names

    libs: dict = {}
    for cc in {e.country_code for e in rows}:
        found = (await session.execute(_text(
            "select id, title_zh, title from documents where country_code=:c and relevance in ('relevant','related') and document_type in ('LAW','REGULATION') order by title_zh"), {"c": cc})).all()
        libs[cc] = [(str(r[0]), _names.law_name(str(r[0]), r[1] or r[2])) for r in found if _names.is_law(str(r[0]), "LAW", None)]
    for k in range(0, len(rows), 2):
        group = rows[k : k + 2]
        listing = []
        for i, e in enumerate(group):
            how = "；".join(f"{s.title_zh}：{s.detail_zh}（材料：{'、'.join(s.materials or [])}）" for s in steps.get(e.id, []))[:900]
            related = await _related(session, e.evidence_clause_id, cache)
            listing.append(f"{i}. {e.name_zh}（{e.country_code}，受理：{e.authority_zh or '未写'}）\n摘要：{e.summary_zh[:300]}\n步骤：{how}\n条文：{(e.evidence_quote or '')[:1500]}"
                           + (f"\n同一文件相关条文：\n{related[:WHOLE_DOC]}" if related else ""))
        lib = [x for cc in dict.fromkeys(e.country_code for e in group) for x in libs.get(cc, [])]  # 同批两国时两国都列，取回时按国家过滤
        listing.append("可选的法规：\n" + "\n".join(f"L{j}. {n}" for j, (_, n) in enumerate(lib)))
        try:
            res = await call_llm_json([{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n\n".join(listing)}], max_tokens=7000, temperature=0.0)
        except LlmUnavailableError as exc:
            print("没写成：", [e.name_zh for e in group], exc, flush=True)
            continue
        tokens += int(res.prompt_tokens or 0) + int(res.completion_tokens or 0)
        for item in (res.data or {}).get("items", []) if isinstance(res.data, dict) else []:
            try:
                i = int(item.get("i"))
            except (TypeError, ValueError):
                continue
            if not 0 <= i < len(group):
                continue
            e = group[i]
            kept = {f.split(":", 1)[1] for f in (e.edited or []) if f.startswith("profile:")}  # 管理员改过的栏目不动
            if "conditions" in kept:
                kept |= {"eligibility", "eligibility_proofs", "applicant"}
            new = {f: _clean(item.get(f), f) for f in FIELDS}
            if new["local_rep"].startswith(NOT_APPLICABLE):
                new["local_rep"] = NOT_APPLICABLE
            new["eligibility_proofs"] = _proofs(item.get("eligibility_proofs"))
            roles = _clean(item.get("authority_roles"), "authority_roles")
            new["authority_roles"] = "" if roles in ("", UNKNOWN) else roles
            new["cited_laws"] = [" ".join(str(x).split())[:60] for x in item.get("cited_laws") or [] if str(x).strip()][:6]
            conds = []
            for c in item.get("conditions") or []:
                if isinstance(c, dict) and str(c.get("condition") or "").strip() and str(c.get("proof") or "").strip():
                    conds.append({k2: " ".join(str(c.get(k2) or "").split())[:80] for k2 in ("condition", "proof", "what")})
            new["conditions"] = tidy_conditions(conds)[:10]
            conds = new["conditions"]
            applicant = _clean(item.get("applicant"), "local_rep")
            new["applicant"] = applicant
            applicant = re.sub(r"^(?:申请人|申请主体)[:：]\s*", "", applicant)
            applicant = re.sub(r"[（(][^）)]*还是[^）)]*[）)]", "", applicant).strip() or UNKNOWN
            new["applicant"] = applicant
            new["eligibility"] = re.sub(r"[（(][^）)]*还是[^）)]*[）)]", "", re.sub(r"(?:^(?:申请人|申请主体)|硬门槛|门槛)[:：]\s*", "", new["eligibility"])).strip("；; ") or UNKNOWN
            if conds:  # 资格栏 = 申请人 + 每条门槛；证明原件与门槛一一对应
                new["eligibility"] = (f"{applicant}；" if applicant != UNKNOWN else "") + "；".join(c["condition"] for c in conds)
                new["eligibility_proofs"] = list({c["proof"]: {"name": c["proof"], "what": c["what"]} for c in conds}.values())
            new["applies_to"] = " ".join(str(item.get("applies_to") or "").split())[:30]
            ids = []
            for x in item.get("law_ids") or []:
                m = re.match(r"L?([0-9]+)$", str(x).strip())
                if m and int(m.group(1)) < len(lib):
                    ids.append(lib[int(m.group(1))][0])
            new["law_ids"] = list(dict.fromkeys(ids))[:4]
            nm = " ".join(str(item.get("name_zh") or "").split())
            if 2 <= len(nm) <= 24 and "name_zh" not in (e.edited or []):
                e.name_zh = nm
            e.profile = {**(e.profile or {}), **{f: v for f, v in new.items() if f not in kept}}
            done += 1
    await session.commit()
    return {"checked": len(rows), "filled": done, "tokens": tokens}
