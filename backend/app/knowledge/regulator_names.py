# ruff: noqa: E501
"""Chinese names for the supervisory bodies the laws name.

The graph extraction writes a regulator's Chinese name, but for bodies named only in an Arabic or
French text it sometimes keeps the original (“هيئة تنمية صناعة تكنولوجيا المعلومات (ITIDA)”), and a
law's short forms (“the Commission”, “CCC”, “المركز”) cannot be named from the bare words: “CCC” in a
Singapore IoT labelling clause is the Cybersecurity Certification Centre, not a citizens' committee.
So each body is named from the sentence it appears in and the title of the law, as a lawyer reading
the text would; a mention that is only a role (“the competent authority”, “law enforcement agency”)
is marked as such and is not listed as a regulator.

The original stays in ``name_orig``. It runs after every graph extraction (only bodies not yet named
from context), a few thousand tokens per country.
"""

from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.llm import LlmUnavailableError, call_llm_json
from app.analysis.workflow_engine import country_name_zh
from app.ingestion.models import Clause, Document, DocumentVersion
from app.knowledge.models import KgNode

log = logging.getLogger(__name__)
DEAD = ("rejected", "superseded")
HAN = re.compile(r"[一-鿿]")
MARK = "named_from_context"
SYSTEM = (
    "你是熟悉各国监管体制的法律翻译。下面每一项是某国法规条文里提到的一个机构，附它所在的法规名和条文原句。"
    "根据原句和法规判断它具体指哪个机构，给出中文通行的正式名称：国名放最前（如“新加坡”），机构缩写照原样放在末尾括号里（如“（CSA）”）。"
    "条文用简称或代称时（如 the Commission、the Authority、CCC、المركز），按这部法规的语境写出它指的那个机构的全称；"
    "看不出具体指哪个机构、只是泛指一种角色的（如“主管机关”“执法机构”“注册官”），role 写 true，zh 写这个角色的中文。不要解释。"
    '只返回 JSON：{"items": [{"i": 序号, "zh": "中文名", "role": false}]}'
)


def _context(text: str, name: str, width: int = 160) -> str:
    """The sentence around the body's name in the clause (the clause start if not found)."""
    flat = " ".join((text or "").split())
    key = re.sub(r"[（(].*", "", name or "").strip()[:24]
    i = flat.find(key) if key else -1
    return flat[max(0, i - width): i + width] if i >= 0 else flat[: 2 * width]


async def translate(session: AsyncSession, country_code: str | None = None, *, recheck: bool = False) -> dict:
    """Name the live regulators that have not been named from context yet: those without Chinese
    in their name, or (``recheck``) every one whose original name is in a foreign language."""
    q = select(KgNode).where(KgNode.node_type == "REGULATOR", KgNode.review_status.notin_(DEAD))
    if country_code:
        q = q.where(KgNode.country_code == country_code.upper())
    nodes = [n for n in (await session.scalars(q)).all() if not (n.attributes or {}).get(MARK)]
    todo = [
        n
        for n in nodes
        if not HAN.search(n.name_zh or "") or (recheck and (n.attributes or {}).get("evidence_clause_ids") and not HAN.search(n.name_orig or n.name_zh or ""))
    ]
    by_country: dict[str, list[KgNode]] = {}
    for n in todo:
        by_country.setdefault(n.country_code, []).append(n)
    done = roles = tokens = 0
    for cc, group in by_country.items():
        lines = []
        for i, n in enumerate(group):
            cid = ((n.attributes or {}).get("evidence_clause_ids") or [None])[0]
            row = None
            if cid:
                row = (
                    await session.execute(
                        select(Clause.original_text, Document.title_zh, Document.title)
                        .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
                        .join(Document, Document.id == DocumentVersion.document_id)
                        .where(Clause.id == uuid.UUID(str(cid)))
                    )
                ).one_or_none()
            name = n.name_orig or n.name_zh
            law = (row[1] or row[2]) if row else ""
            lines.append(f"{i}. 机构：{name}\n   法规：{law}\n   原句：{_context(row[0], name) if row else '（无）'}")
        listing = f"国家：{country_name_zh(cc)}\n" + "\n".join(lines)
        try:
            res = await call_llm_json(
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": listing}],
                max_tokens=4000,
                temperature=0.0,
            )
        except LlmUnavailableError:
            log.warning("监管机构译名：模型不可用（%s）", cc)
            continue
        tokens += int(res.prompt_tokens or 0) + int(res.completion_tokens or 0)
        for item in (res.data or {}).get("items", []) if isinstance(res.data, dict) else []:
            i = item.get("i")
            i = int(i) if isinstance(i, str) and i.isdigit() else i
            zh = str(item.get("zh") or "").strip()
            if isinstance(i, int) and 0 <= i < len(group) and HAN.search(zh) and len(zh) <= 120:
                node = group[i]
                if not node.name_orig:
                    node.name_orig = node.name_zh
                node.name_zh = zh
                attrs = dict(node.attributes or {})
                attrs[MARK] = True
                attrs["is_role"] = bool(item.get("role"))
                node.attributes = attrs
                done += 1
                roles += bool(item.get("role"))
    await session.commit()
    return {"checked": len(todo), "named": done, "roles": roles, "tokens": tokens}
