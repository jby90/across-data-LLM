# ruff: noqa: E501
"""Chinese names for the materials an admission step lists.

The extraction sometimes keeps the original words of the law (“clauses contractuelles ou règles
internes du traitement”, “TCF”, Arabic phrases). The enterprise reads these as a checklist of what to
prepare, so each gets its usual Chinese name with the original in brackets: “合同条款或内部处理规则
（clauses contractuelles ou règles internes du traitement）”. Runs after admission generation and on
demand; only names without any Chinese are touched.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admission.models import AdmissionEntry, AdmissionStep
from app.analysis.llm import LlmUnavailableError, call_llm_json

HAN = re.compile(r"[一-鿿]")
SYSTEM = (
    "下面每行是某国法规里列出的一项申请材料的原文名称（可能是缩写，如 TCF = Technical Construction File）。"
    "给出它在中文里通行的叫法，简短、准确，不加解释；缩写要写出它指的材料。"
    '只返回 JSON：{"items": [{"i": 序号, "zh": "中文名"}]}'
)


async def translate(session: AsyncSession) -> dict:
    import app.models_registry  # noqa: F401
    steps = (
        await session.execute(
            select(AdmissionStep, AdmissionEntry.country_code).join(AdmissionEntry, AdmissionEntry.id == AdmissionStep.entry_id)
        )
    ).all()
    todo = sorted({m for st, _ in steps for m in (st.materials or []) if m and not HAN.search(m)})
    if not todo:
        return {"translated": 0, "tokens": 0}
    names: dict[str, str] = {}
    tokens = 0
    for start in range(0, len(todo), 40):
        batch = todo[start : start + 40]
        try:
            res = await call_llm_json(
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(f"{i}. {m}" for i, m in enumerate(batch))}],
                max_tokens=3000,
                temperature=0.0,
            )
        except LlmUnavailableError:
            continue
        tokens += int(res.prompt_tokens or 0) + int(res.completion_tokens or 0)
        for item in (res.data or {}).get("items", []) if isinstance(res.data, dict) else []:
            i = item.get("i")
            i = int(i) if isinstance(i, str) and i.isdigit() else i
            zh = str(item.get("zh") or "").strip()
            if isinstance(i, int) and 0 <= i < len(batch) and HAN.search(zh) and len(zh) <= 80:
                names[batch[i]] = f"{zh}（{batch[i]}）"
    for st, _ in steps:
        if st.edited:  # 管理员改过的环节不动
            continue
        if st.materials and any(m in names for m in st.materials):
            st.materials = [names.get(m, m) for m in st.materials]
    await session.commit()
    return {"untranslated": len(todo), "translated": len(names), "tokens": tokens}
