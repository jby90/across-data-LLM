# ruff: noqa: E501
"""How each material an admission item's law lists is prepared. The platform helps the enterprise
prepare its filing; it does not take the enterprise's own documents or file anything for it. So each
material is one of:

- ``info``: a description the platform writes into the generated application from information the
  enterprise gives once (address, legal representative, organisation, nature of the activity, the
  personal data processed...). It maps to an information item the platform already asks somewhere
  (any compliance document or official form) — the same answer is reused — or, only when none fits,
  to one of a few admission-only items (``NEW_FACTS``);
- ``form``: an official form of the authority (filled on the platform when the form is attached);
- ``application``: the written application itself, i.e. the generated material;
- ``original``: something only the enterprise has and brings to the authority (commercial register
  extract, licences, test reports, certificates, signed powers of attorney). Listed with one
  sentence on what kind is required, taken from the law (``what``), never collected;
- ``none``: not a material at all (a statement about the authority's website, a fee...).

The model reads the material names with the clause each step rests on; ``what`` is kept only when
the clause really says it (its ``quote`` must be in the clause), otherwise left empty. Stored on the
entry as ``materials_plan`` [{name, kind, fact, what}], in the order the law lists them.
"""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admission.models import AdmissionEntry, AdmissionStep
from app.analysis.llm import LlmUnavailableError, call_llm_json

KINDS = ("info", "form", "application", "original", "none")

# 只有准入申请才问、别处都没问过的说明类信息（其余一律复用已有的填写项）
NEW_FACTS: dict[str, dict[str, str]] = {
    "org_structure": {
        "label": "组织结构说明",
        "question": "申请主体的组织结构是怎样的？（母公司、在当地的分支或子公司、负责这项业务的部门）",
        "why": "受理机构要求说明申请主体的组织结构。",
    },
    "activity_nature": {
        "label": "业务活动说明",
        "question": "在当地开展的是什么业务？（如进口销售整车、提供车联网服务、道路测试）",
        "why": "受理机构要求说明申请主体在当地的业务活动。",
    },
    "processing_description": {
        "label": "数据处理说明",
        "question": "在当地处理哪些个人数据、为了什么、怎么处理？（数据种类、目的、收集与存储方式）",
        "why": "受理机构要求说明个人数据处理的情况。",
    },
    "after_sales_network": {
        "label": "经认证维修中心的分布与服务能力",
        "question": "在当地有哪些经认证的维修中心、分布在哪些地区、每年能服务多少辆车？",
        "why": "进口放行条件要求维修中心按地区分布、服务能力与年销量相当。",
    },
    "spare_parts_supply": {
        "label": "基本备件供应",
        "question": "按厂家保养计划需要的基本备件，在当地备货能覆盖多少比例的车辆？",
        "why": "进口放行条件要求基本备件至少覆盖一定比例的车辆。",
    },
    "vehicle_airbags": {
        "label": "安全气囊配置",
        "question": "每个车型配了几个安全气囊，分别在什么位置？",
        "why": "进口放行条件要求车辆配有规定数量的安全气囊。",
    },
    "data_subject_rights": {
        "label": "数据主体权利保障说明",
        "question": "用户怎样查询、更正、删除自己的数据，怎样撤回同意？",
        "why": "受理机构要求说明保障数据主体权利的做法。",
    },
}


def fact_registry() -> dict[str, str]:
    """Every information item the platform already asks (key → label): the compliance documents'
    facts and the facts the official forms carry, then the admission-only items."""
    from app.analysis.document_templates_forms import FORM_TEMPLATES
    from app.forms.service import catalog

    out: dict[str, str] = {}
    skip = {"country_name", "source_country", "recipient_country", "scenario", "entry_type", "authority"}
    for _, _, definition in FORM_TEMPLATES:
        for f in definition.get("facts", []):
            if f["key"] not in skip:
                out.setdefault(f["key"], f["label"])
    for form in catalog().values():
        for section in form.get("sections", []):
            for item in section.get("items", []):
                if item.get("fact") and item.get("kind") == "text":
                    raw_lbl = (item.get("label") or item.get("label_zh") or item["fact"]).split("：")[-1]
                    raw_lbl = re.sub(r"[（(]N°RC[）)]", "", raw_lbl).strip()
                    out.setdefault(item["fact"], raw_lbl)
    for k, f in NEW_FACTS.items():
        out.setdefault(k, f["label"])
    # 官方表格里只有外文标签或没有标签的几项，给中文名（页面上不出现字段名）
    for k, label in KNOWN_LABELS.items():
        if out.get(k, k) == k or not re.search(r"[一-鿿]", out.get(k, "")):
            out[k] = label
    return out


KNOWN_LABELS = {
    "dpo_name": "数据保护负责人姓名", "dpo_email": "数据保护负责人电子邮箱", "dpo_phone": "数据保护负责人电话",
    "applicant": "申请人", "company_name": "申请企业名称", "vehicle_model": "车型或设备型号",
    "company_reg_no": "商业登记号", "commercial_reg": "商业登记号",
}


SYSTEM = """你在帮中国车企准备外国准入申报材料。平台只帮企业准备材料，不收企业的证照原件，也不代为递交。
下面是一个准入事项的条文原文，以及条文列出的各项“材料”。逐项判断它属于哪一类：
- info：一段说明、陈述或基本信息（如地址、法定代表人、组织结构、活动性质、处理的个人数据的性质与规模、安全措施、数据主体权利的保障方式），平台能根据企业填的信息写进申请材料；
- form：受理机构的官方表格（申请表、申报表、某号表格）；
- application：书面申请/申请函本身；
- original：只有企业自己有、递交时要带的原件或证明文件（商业登记、营业执照、许可证、检测报告、证书、合格证明、委托书、发票、身份证明、经认证的副本等）；
- none：根本不是一项材料（如“机构网站公布的信息”、收费说明、主管机关自己出具的东西），或者只是另一项已列材料的组成部分（如某证书的“颁发机构”“取得日期”“有效期”）。
企业自己能写出来的一段说明（如“数据存储方式说明”“服务地理区域”“数据接收方”），也算 info，不算 original。
info 类再从“已有填写项”里选意思相同或能涵盖它的键填进 fact（如“数据存储方式”可用“保存期限与存放位置”，“处理数据受益方”可用“境外接收方”）；若一项说明需要两三项信息（如“地址及法定代表人”），用英文逗号把键连起来；已有填写项确实都不涵盖，才选“准入专用项”里的键；都不合适留空。
original 类写 what：用中文一句话说明要什么样的（如“经公证的副本”“由该国认可的实验室出具”），必须是条文原话里写了的、且比材料名称多出信息，并在 quote 里给出条文原文中支持它的那几个词（逐字摘录）；条文没写或只是重复材料名称，就留空。
只返回 JSON：{"items": [{"i": 序号, "kind": "...", "fact": "", "what": "", "quote": ""}]}"""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip().lower()


HAN = re.compile(r"[一-鿿]")


def _adds(name: str, what: str) -> bool:
    """``what`` says something in Chinese beyond the material's own name."""
    from difflib import SequenceMatcher

    if not HAN.search(what):
        return False
    bare = re.sub(r"（[^）]*）|\([^)]*\)", "", name)
    w = re.sub(r"（[^）]*）|\([^)]*\)|[，。、；\s]", "", what)
    return bool(w) and w not in bare and SequenceMatcher(None, bare, w).ratio() < 0.7


def _materials(steps: list[AdmissionStep]) -> list[tuple[str, str]]:
    """(material, clause quote of the step that lists it), each material once, in law order."""
    seen, out = set(), []
    for st in steps:
        for m in st.materials or []:
            k = re.sub(r"\s", "", m)
            if k and k not in seen:
                seen.add(k)
                out.append((m, st.evidence_quote or ""))
    return out


async def classify(session: AsyncSession, *, redo: bool = False, country: str | None = None) -> dict:
    import app.models_registry  # noqa: F401

    registry = fact_registry()
    shared = {k: v for k, v in registry.items() if k not in NEW_FACTS}
    q = select(AdmissionEntry).where(AdmissionEntry.merged_into.is_(None), AdmissionEntry.review_status != "out_of_scope")
    if country:
        q = q.where(AdmissionEntry.country_code == country.upper())
    entries = (await session.scalars(q)).all()
    done = tokens = 0
    for entry in entries:
        steps = (await session.scalars(select(AdmissionStep).where(AdmissionStep.entry_id == entry.id).order_by(AdmissionStep.seq))).all()
        mats = _materials(steps)
        have = {p.get("name") for p in entry.materials_plan or []}
        if "materials_plan" in (entry.edited or []):  # 管理员改过的递交清单不动，--redo 也不动
            continue
        if not mats or (not redo and all(m in have for m, _ in mats)):
            continue
        quotes = "\n".join(dict.fromkeys(" ".join(qt.split()) for _, qt in mats if qt))[:6000]
        listing = "\n".join(f"{i}. {m}" for i, (m, _) in enumerate(mats))
        user = (
            f"准入事项：{entry.name_zh}\n条文原文：\n{quotes or entry.evidence_quote}\n\n材料：\n{listing}\n\n"
            "已有填写项：" + "；".join(f"{k}={v}" for k, v in shared.items())
            + "\n准入专用项：" + "；".join(f"{k}={v['label']}" for k, v in NEW_FACTS.items())
        )
        try:
            res = await call_llm_json([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], max_tokens=3000, temperature=0.0)
        except LlmUnavailableError:
            continue
        tokens += int(res.prompt_tokens or 0) + int(res.completion_tokens or 0)
        got = {}
        for item in (res.data or {}).get("items", []) if isinstance(res.data, dict) else []:
            i = item.get("i")
            i = int(i) if isinstance(i, str) and i.isdigit() else i
            if isinstance(i, int) and 0 <= i < len(mats):
                got[i] = item
        plan = []
        for i, (name, quote) in enumerate(mats):
            item = got.get(i) or {}
            kind = item.get("kind") if item.get("kind") in KINDS else "original"
            facts = [k.strip() for k in str(item.get("fact") or "").split(",") if k.strip() in registry]
            if kind == "info" and not facts:
                kind = "original"  # 说不清由哪项信息写成的说明，当作企业自备的文件列在清单里
            what = " ".join(str(item.get("what") or "").split())[:60]
            cited = _norm(str(item.get("quote") or ""))
            if kind != "original" or not what or not cited or cited not in _norm(quote or entry.evidence_quote) or not _adds(name, what):
                what = ""
            plan.append({"name": name, "kind": kind, "fact": ",".join(facts) if kind == "info" else "", "what": what})
        entry.materials_plan = plan
        done += 1
    await session.commit()
    return {"entries": done, "tokens": tokens}


CN_ONLY = re.compile(r"等级保护|等保|网信|个人信息出境标准合同|出境安全评估|关键信息基础设施")


def _bare_name(name: str) -> str:
    return re.sub(r"（[^）]*）|\([^)]*\)|原件|正本|副本|复印件|摘录|[\s，、。]", "", name or "")


def plan_of(entry: AdmissionEntry, materials: list[str]) -> list[dict]:
    """The plan for the materials the item lists now; a material not classified yet counts as an
    original to bring (listed, never asked for). Then the originals that prove the applicant meets
    each condition of eligibility (commercial register extract, tax card, paid-up capital...; see
    item_profile ``eligibility_proofs``), unless the law already lists the same document. An
    administrator who edited the checklist (``materials_plan`` in ``edited``) decides the whole list."""
    known = {p.get("name"): p for p in entry.materials_plan or []}
    out = [dict(known.get(m) or {"name": m, "kind": "original", "fact": "", "what": ""}) for m in materials]
    if entry.country_code != "CN":
        # 中国特有的制度（等级保护、网信办申报……）不套到别国：这类填写项不问，材料按企业自带列
        labels = fact_registry()
        for p in out:
            if p.get("kind") == "info" and p.get("fact"):
                keep = [k for k in p["fact"].split(",") if not CN_ONLY.search(labels.get(k, ""))]
                p["fact"] = ",".join(keep)
                if not keep:
                    p["kind"] = "original"
    if "materials_plan" in (entry.edited or []):
        listed = {p["name"] for p in out}
        # 管理员加的行（法规环节里没有的）也列上，顺序以管理员排的为准
        order = [p.get("name") for p in entry.materials_plan or []]
        out += [p for p in entry.materials_plan or [] if p.get("name") not in listed]
        out.sort(key=lambda p: order.index(p["name"]) if p["name"] in order else len(order))
    # 管理员删掉的证明原件记为“不列入”留在清单里，这里不再补回
    have = {_bare_name(p["name"]): p for p in out}
    # 每条资格配一份证明原件；同一份原件要证明好几件事的，把要显示的内容合并写全
    proofs: dict[str, dict] = {}
    for c in (entry.profile or {}).get("conditions") or [{"proof": x.get("name", ""), "what": x.get("what", "")} for x in (entry.profile or {}).get("eligibility_proofs") or []]:
        name = (c.get("proof") or "").strip()
        if not name:
            continue
        what = (c.get("what") or "").strip().removeprefix("显示")
        row = proofs.setdefault(_bare_name(name), {"name": name, "whats": []})
        if what and what not in row["whats"]:
            row["whats"].append(what)
    for key, row in proofs.items():
        what = ("显示" + "；".join(row["whats"])) if row["whats"] else ""
        # 递交清单里已有同一份原件的，把要显示的内容并进去；对上的是“不列入”或写进申请材料的说明，不算已有
        hit = next((p for h, p in have.items() if h and (key in h or h in key) and p.get("kind") in ("original", "form")), None)
        if hit is not None:
            if what and what not in (hit.get("what") or ""):
                hit["what"] = "；".join(x for x in (hit.get("what") or "", what) if x)
            continue
        have["proof:" + key] = {"name": row["name"], "kind": "original", "fact": "", "what": what, "proves": True}
        out.append(have["proof:" + key])
    return tidy_plan(out)


_ATTRIBUTE_ONLY = re.compile(
    r"^(颁发机构信息|颁发机构|取得日期|颁发日期|签发日期|申请日期|签署日期|填表日期|提交日期|落款日期|盖章日期|有效期|证书编号)$"
)
_KIND_RANK = {"info": 0, "form": 1, "application": 2, "original": 3, "none": 4}


def tidy_plan(plan: list[dict]) -> list[dict]:
    """One row per material: what is only an attribute of another document (issuer, date, validity)
    is not a document of its own; the same material listed twice appears once, and one that goes
    into the application is not also a document to bring."""
    out: list[dict] = []
    seen: dict[str, int] = {}
    for p in plan:
        bare = _bare_name(p.get("name", ""))
        if _ATTRIBUTE_ONLY.match(bare):
            continue
        if bare in seen:
            i = seen[bare]
            if _KIND_RANK.get(p.get("kind"), 3) < _KIND_RANK.get(out[i].get("kind"), 3):
                out[i] = p
            continue
        seen[bare] = len(out)
        out.append(p)
    return out
