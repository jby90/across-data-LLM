# ruff: noqa: E501
"""Short, readable texts for a country page: three overview lines and one sentence per dimension.

The structure and the numbers on the page come from the graph (whitepaper_graph); only these few
lines are written by the model, from the same facts, and a line that carries a figure or a name
the facts do not contain is replaced by the program's own plain line. Roughly 3–4 thousand tokens
per country."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.analysis import document_narrative as nar
from app.analysis.llm import LlmUnavailableError, call_llm_json
from app.core.db import Base

OVERVIEW_MAX = 60
LINE_MAX = 36
LABELS = ("法律体系", "监管核心", "风险警示")

SYSTEM = (
    "你为汽车数据合规平台的国家页写几句很短的提要，读者是车企合规人员。只能用 facts 里给出的内容："
    "不得加入 facts 以外的法规名、机构名、数字或法律判断。"
    f"overview 恰好三条，label 依次为{'、'.join(LABELS)}，每条一句话，不超过 {OVERVIEW_MAX} 字，"
    "写清最关键的一点，不要罗列多个法规或机构名（最多点出一个最核心的）。"
    f"dimensions 给每个维度（按 code）写一句话，不超过 {LINE_MAX} 字，说明这个维度里最值得先看的事，不要重复数字。"
    "用中文，平实，不写“本库”“收录”这类系统口吻，不写内部编号。"
    '只输出 JSON：{"overview":[{"label":"","text":""}],"dimensions":{"D1":""}}。'
)


class CountryPageText(Base):
    __tablename__ = "country_page_texts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    input_hash: Mapped[str] = mapped_column(String(64))
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def facts_of(meta: dict) -> dict:
    return {
        "overview": [{"label": o["label"], "facts": o["text"]} for o in meta["overview"]],
        "dimensions": [
            {"code": d["code"], "title": d["title"], "count": d["total"], "unit": d["unit"], "examples": [i["title"] for i in d["items"][:5]]}
            for d in meta["dimensions"]
        ],
    }


def input_hash(facts: dict) -> str:
    return hashlib.sha256(json.dumps(facts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _ok(text: str, allowed: set[str], limit: int) -> bool:
    text = text.strip()
    if not text or len(text) > limit:
        return False
    if re.search(r"条文\s*\d|\bclauses?\b|\bcites?\b|本库|收录", text, re.I):
        return False
    return not (nar._numbers(text) - allowed)


def check(data: Any, facts: dict) -> dict:
    """Keep only the lines that pass; the rest are left out and the page uses its own line."""
    allowed = nar._numbers(json.dumps(facts, ensure_ascii=False))
    out: dict[str, Any] = {"overview": {}, "dimensions": {}}
    if not isinstance(data, dict):
        return out
    for o in data.get("overview") or []:
        if isinstance(o, dict) and o.get("label") in LABELS and _ok(str(o.get("text") or ""), allowed, OVERVIEW_MAX):
            out["overview"][o["label"]] = str(o["text"]).strip()
    dims = data.get("dimensions") if isinstance(data.get("dimensions"), dict) else {}
    codes = {d["code"] for d in facts["dimensions"]}
    for code, text in dims.items():
        if code in codes and _ok(str(text or ""), allowed, LINE_MAX):
            out["dimensions"][code] = str(text).strip()
    return out


async def write(meta: dict) -> tuple[dict, int, str]:
    facts = facts_of(meta)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}]
    try:
        res = await call_llm_json(messages, max_tokens=4000, temperature=0.2)
    except LlmUnavailableError as exc:
        raise nar.NarrativeError(str(exc)) from exc
    tokens = int(getattr(res, "prompt_tokens", 0) or 0) + int(getattr(res, "completion_tokens", 0) or 0)
    return check(res.data, facts), tokens, input_hash(facts)


async def latest(session: AsyncSession, country_code: str) -> CountryPageText | None:
    return await session.scalar(
        select(CountryPageText).where(CountryPageText.country_code == country_code).order_by(CountryPageText.created_at.desc()).limit(1)
    )


def apply(meta: dict, row: CountryPageText | None) -> dict:
    """The page structure with the model's lines where they are still valid for the current facts."""
    if row is None or row.input_hash != input_hash(facts_of(meta)):
        return meta
    ov, dims = row.data.get("overview", {}), row.data.get("dimensions", {})
    meta = {**meta, "overview": [{**o, "text": ov.get(o["label"], o["text"])} for o in meta["overview"]]}
    meta["dimensions"] = [{**d, "headline": dims.get(d["code"], d["headline"])} for d in meta["dimensions"]]
    return meta
