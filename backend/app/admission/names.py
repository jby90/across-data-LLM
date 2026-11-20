# ruff: noqa: E501
"""How laws and receiving bodies are written on admission pages (one wording across the site).

- ``law_names.json``: for each law an admission item rests on, its display name “中文名称（年份 + 编号）”
  without source labels (阿文公报、议会版、合并版…), and whether it is a law, decree or ministerial
  order at all (guides, explanatory statements and forms are not: they go under “参考：官方办事说明”).
- ``authorities.json``: per country, each receiving body as the law writes it → “中文全称（缩写）”;
  a post (minister, director) becomes the body it belongs to; "" when it names no body.
Both are written by ``build`` (model, checked by hand in the admin page) and read at request time.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).parent
LAWS, BODIES = HERE / "law_names.json", HERE / "authorities.json"
SOURCE_LABEL = re.compile(r"[（(](?:阿文公报|阿文|阿语|法、阿两版|英文|英文译本[^）)]*|议会版|内安部队门户版|合并版|电信部版|央行版|人权委员会转载版|[^）)]*版)[）)]")


@lru_cache(maxsize=1)
def _laws() -> dict:
    return json.loads(LAWS.read_text(encoding="utf-8")) if LAWS.exists() else {}


@lru_cache(maxsize=1)
def _bodies() -> dict:
    return json.loads(BODIES.read_text(encoding="utf-8")) if BODIES.exists() else {}


def reload() -> None:
    _laws.cache_clear()
    _bodies.cache_clear()


def law_name(doc_id: str, fallback: str) -> str:
    hit = _laws().get(str(doc_id))
    return hit["name"] if hit and hit.get("name") else SOURCE_LABEL.sub("", fallback or "").strip()


def is_law(doc_id: str, doc_type: str | None, kind: str | None) -> bool:
    hit = _laws().get(str(doc_id))
    if hit is not None:
        return bool(hit.get("is_law"))
    return (doc_type or "").upper() in ("LAW", "REGULATION") and kind != "ADMISSION_GUIDANCE"


# 各国受理机构的规范名（国名 + 中文全称（缩写））和它在条文、表格里的各种短写法
CANON = [
    "埃及个人数据保护中心（PDPC）", "埃及国家电信监管局（NTRA）", "埃及进出口监管总局（GOEIC）", "埃及信息技术产业发展局（ITIDA）",
    "摩洛哥国家个人数据保护委员会（CNDP）", "摩洛哥国家电信监管局（ANRT）", "摩洛哥数字转型与行政改革部（MTNRA）", "摩洛哥海关和间接税管理局（ADII）",
    "摩洛哥国家道路安全局（NARSA）", "摩洛哥信息系统安全总局（DGSSI）", "摩洛哥政府总秘书处（SGG）",
    "新加坡竞争与消费者委员会（CCCS）", "新加坡网络安全局（CSA）", "新加坡资讯通信媒体发展局（IMDA）", "新加坡陆路交通管理局（LTA）",
    "新加坡个人数据保护委员会（PDPC）", "新加坡网络安全服务监管办公室（CSRO）",
    "突尼斯国家个人数据保护局（INPDP）", "突尼斯国家电子认证局（ANCE）", "突尼斯电信研究中心（CERT）", "突尼斯陆路运输技术局（ATTT）",
    "黎巴嫩电信监管局（TRA）", "黎巴嫩认可委员会（COLIBAC）",
]
_PAIRS = [(m.group(1), m.group(2), f) for f in CANON if (m := re.fullmatch(r"(.+)（([A-Z]+)）", f))]
_CN = {"EG": "埃及", "TN": "突尼斯", "SG": "新加坡", "MA": "摩洛哥", "LB": "黎巴嫩"}
_ALIAS = {
    "EG": [("国家电信(?:监管|管理)局", "埃及国家电信监管局（NTRA）", "NTRA"), ("个人数据保护中心", "埃及个人数据保护中心（PDPC）", "PDPC"),
           ("进出口监管总局", "埃及进出口监管总局（GOEIC）", "GOEIC"), ("信息技术产业发展局", "埃及信息技术产业发展局（ITIDA）", "ITIDA")],
    "TN": [("国家电子认证(?:局|机构)", "突尼斯国家电子认证局（ANCE）", "ANCE"), ("国家个人数据保护(?:局|机构)", "突尼斯国家个人数据保护局（INPDP）", "INPDP"),
           ("电信研究与研究中心|电信研究中心", "突尼斯电信研究中心（CERT）", "CERT"), ("陆路运输技术局", "突尼斯陆路运输技术局（ATTT）", "ATTT")],
    "SG": [("陆路交通管理局", "新加坡陆路交通管理局（LTA）", "LTA"), ("资讯通信媒体发展局", "新加坡资讯通信媒体发展局（IMDA）", "IMDA"),
           ("网络安全局", "新加坡网络安全局（CSA）", "CSA"), ("个人数据保护委员会", "新加坡个人数据保护委员会（PDPC）", "PDPC")],
    "MA": [("国家个人数据保护监管委员会|国家个人数据保护委员会|国家数据保护委员会", "摩洛哥国家个人数据保护委员会（CNDP）", "CNDP"),
           ("国家电信(?:监管|管理)局", "摩洛哥国家电信监管局（ANRT）", "ANRT"),
           ("海关和间接税管理局", "摩洛哥海关和间接税管理局（ADII）", "ADII"), ("国家道路安全局", "摩洛哥国家道路安全局（NARSA）", "NARSA"),
           ("信息系统安全总局", "摩洛哥信息系统安全总局（DGSSI）", "DGSSI")],
    "LB": [("电信(?:监管|管理)局", "黎巴嫩电信监管局（TRA）", "TRA")],
}


def normalize_body(t: str, cc: str = "") -> str:
    """One wording for a receiving body wherever it is shown: “国名 + 中文全称（缩写）”. Short forms
    (no country, “局/机构”, “全称 缩写”, abbreviation alone, French Instance) are brought to it."""
    if not t:
        return t
    for bare, abbr, full in _PAIRS:
        t = re.sub(rf"(?<![A-Za-z]){abbr}\s*[（(]\s*{re.escape(bare)}\s*[）)]", full, t)
        t = re.sub(rf"{re.escape(bare)}\s*[（(]\s*{abbr}\s*[）)]", full, t)
        t = re.sub(rf"{re.escape(bare)}\s+{abbr}(?![A-Za-z])", full, t)
        t = re.sub(rf"{re.escape(bare)}(?![（(]|[\u4e00-\u9fff]{{1,12}}[（(]\s*{re.escape(abbr)}\s*[）)])", full, t)
    for short, full, abbr in _ALIAS.get((cc or "").upper(), []):
        t = re.sub(rf"(?<![A-Za-z]){abbr}\s*[（(]\s*(?:{_CN[cc.upper()]})?(?:{short})\s*[）)]", full, t)
        t = re.sub(
            rf"(?:{_CN[cc.upper()]})?(?:{short})(?:\s*[（(]\s*{abbr}\s*[）)]|\s+{abbr}(?![A-Za-z]))?(?![（(]|[\u4e00-\u9fff]{{1,12}}[（(]\s*{re.escape(abbr)}\s*[）)])",
            full,
            t,
        )
        t = re.sub(rf"(?<![A-Za-z（(\u4e00-\u9fff]){abbr}(?![A-Za-z）)])", full, t)
    if (cc or "").upper() == "TN":
        t = re.sub(r"(?<![’'A-Za-z])Instance(?![A-Za-z])", "突尼斯国家个人数据保护局（INPDP）", t)
        t = re.sub(r"（(ANCE|INPDP)）构", r"（\1）", t)
    t = re.sub(r"[（(]([A-Za-z]{2,10})[）)]([\u4e00-\u9fff]{1,12})[（(]\1[）)]", r"\2（\1）", t)
    return t


def body(cc: str, raw: str) -> str | None:
    """The normalized body, "" when the text names none, None when not in the dictionary."""
    return _bodies().get(cc, {}).get((raw or "").strip())


LAW_SYSTEM = """下面是一些外国法律文件的原文标题和平台给的中文名。逐份给出：
- name：统一的中文名称，写成“中文名称（年份 + 编号）”，如“电信监管法（2003 年第 10 号法律）”“个人数据保护法实施条例（2025 年第 816 号总理令）”“道路交通法典（第 52-05 号法律）”。编号、年份从原文标题读，读不出就不写括号。去掉来源标签（阿文公报、议会版、内安部队门户版、合并版、阿文、英文译本、某某版）。
- is_law：是不是法律、法令、条例、部令、决定这类有约束力的立法文件（true）；办事指南、说明、问答、声明、解释性说明、表格、政策草案、方案程序是 false。
只返回 JSON：{"items": [{"i": 序号, "name": "...", "is_law": true}]}"""

BODY_SYSTEM = """下面是外国准入事项里写的受理机构原文（可能是简称、职位、外文或几个机构）。逐条统一成“国名 + 中文全称（英文或法文缩写）”，如“埃及国家电信监管局（NTRA）”“摩洛哥国家个人数据保护委员会（CNDP）”。
- 写的是职位（部长、部长会议、司长、法官等）的，改成它所属的机构（如“投资司司长”→“黎巴嫩经济与贸易部投资司”，“国防部长”→“黎巴嫩国防部”）；
- 几个机构的，用“；”隔开逐个写；
- 只写“主管机关”“机构”“委员会”这类泛称、看不出是哪个机构的，写 ""；
- 外文（阿拉伯文、法文）不保留在结果里。
只返回 JSON：{"items": [{"i": 序号, "name": "..."}]}"""


async def build(session) -> dict:
    """Write both dictionaries with the model (all admission items' laws and bodies)."""
    from sqlalchemy import text

    from app.analysis.llm import call_llm_json

    tokens = 0
    docs = (await session.execute(text("""select distinct d.id::text, d.country_code, d.title, coalesce(d.title_zh,'') from documents d
        where d.relevance in ('relevant','related') and d.document_type in ('LAW','REGULATION','GUIDANCE','OTHER')
        and (d.id in (select v.document_id from admission_entries e join clauses c on c.id=e.evidence_clause_id join document_versions v on v.id=c.version_id)
             or d.document_type in ('LAW','REGULATION'))"""))).all()
    laws = {}
    for k in range(0, len(docs), 15):
        part = docs[k : k + 15]
        res = await call_llm_json([{"role": "system", "content": LAW_SYSTEM}, {"role": "user", "content": "\n".join(f"{i}. [{d[1]}] {d[2][:220]} ｜ 平台中文名：{d[3]}" for i, d in enumerate(part))}], max_tokens=8000, temperature=0.0)
        tokens += res.total_tokens
        for it in res.data.get("items", []):
            i = int(it.get("i", -1))
            if 0 <= i < len(part):
                laws[part[i][0]] = {"name": SOURCE_LABEL.sub("", str(it.get("name") or "")).strip(), "is_law": bool(it.get("is_law"))}
    LAWS.write_text(json.dumps(laws, ensure_ascii=False, indent=1), encoding="utf-8")
    raws = (await session.execute(text("select distinct country_code, authority_zh from admission_entries where coalesce(authority_zh,'')<>''"))).all()
    bodies: dict = {}
    for cc in sorted({r[0] for r in raws}):
        part = [r[1] for r in raws if r[0] == cc]
        res = await call_llm_json([{"role": "system", "content": BODY_SYSTEM}, {"role": "user", "content": f"国家：{cc}\n" + "\n".join(f"{i}. {a}" for i, a in enumerate(part))}], max_tokens=4000, temperature=0.0)
        tokens += res.total_tokens
        for it in res.data.get("items", []):
            i = int(it.get("i", -1))
            if 0 <= i < len(part):
                bodies.setdefault(cc, {})[part[i]] = re.sub(r"[؀-ۿ]+", "", str(it.get("name") or "")).strip()
    BODIES.write_text(json.dumps(bodies, ensure_ascii=False, indent=1), encoding="utf-8")
    reload()
    return {"laws": len(laws), "bodies": sum(len(v) for v in bodies.values()), "tokens": tokens}
