# ruff: noqa: E501
"""How close each legal term is to an automotive cross-border data business, so the country glossary
starts with the terms that matter (personal data, sensitive data, controller, transfer, vehicle data)
rather than in alphabetical order (“account identifier”, “address-harvesting software”).

The model reads only the term, its Chinese name and the start of its Chinese definition (a few
hundred terms per country, a few tens of thousands of tokens) and puts it into one of three levels;
the level is stored on the definition row. Terms without a level sort after the tagged close ones.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.llm import LlmUnavailableError, call_llm_json

LEVELS = {"core": 0, "related": 1, "remote": 2, "not_term": 9}
HIDDEN = "not_term"  # 误识别成“定义”的句子（“备注”“以下各项”“鉴于宪法第24条”），不在术语表里列
BATCH = 40

SYSTEM = (
    "你在整理汽车数据跨境合规平台的法律术语表。读者是车企的合规和法务人员。每个术语给一个等级：\n"
    "core：数据合规的核心概念，车企做数据出境一定要懂（如个人数据、敏感数据、数据控制者/处理者、数据主体、"
    "同意、跨境传输、数据泄露、匿名化、位置数据、生物识别、关键信息基础设施）；\n"
    "related：车企处理数据或产品准入时会用到（如处理、存储、数据中介、网络安全事件、车辆、无线电设备、认证）；\n"
    "remote：与车辆数据业务关系较远（如营销电话号码登记、电子认证服务商资质、法院与诉讼程序、机构内部任命、银行账户）；\n"
    "not_term：其实不是术语的定义，而是被误认成定义的句子或片段（如“备注”“以下各项”“鉴于宪法第24条规定”"
    "“世界范围内观察到的趋势”、只有一个介词）。\n"
    '只输出 JSON：{"items":[{"i":序号,"level":"core|related|remote|not_term"}]}，序号与输入一致。'
)


async def tag(session: AsyncSession, rows: list, *, redo: bool = False) -> dict:
    """Give the definitions in ``rows`` that have no level one (all of them with ``redo``)."""
    todo = [t for t in rows if redo or not t.business_close]
    done = tokens = 0
    for k in range(0, len(todo), BATCH):
        batch = todo[k : k + BATCH]
        listing = "\n".join(
            f"{i}. {t.source_term}（{t.target_zh}）：{(t.definition_zh or '')[:60]}" for i, t in enumerate(batch)
        )
        try:
            res = await call_llm_json(
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": listing}],
                max_tokens=4000,
                temperature=0.0,
            )
        except LlmUnavailableError:
            continue
        tokens += int(res.prompt_tokens or 0) + int(res.completion_tokens or 0)
        for item in (res.data or {}).get("items", []) if isinstance(res.data, dict) else []:
            i = item.get("i")
            i = int(i) if isinstance(i, str) and i.isdigit() else i
            if isinstance(i, int) and 0 <= i < len(batch) and item.get("level") in LEVELS:
                batch[i].business_close = item["level"]
                done += 1
        await session.commit()
    return {"terms": len(todo), "tagged": done, "tokens": tokens}
