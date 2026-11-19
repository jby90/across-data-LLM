# ruff: noqa: E501
"""From the conversation to a documents project: when the user asks the assistant to produce the
compliance documents, read the route (where the data leaves, where it goes) and the business from
what the user has said, create the project, and start reading the same words for suggested answers.

The route is what the documents are about, so it is never guessed: each country must be named in the
user's own words (any country, not only those in the library). Nothing reaches a document before the
user adopts it on the documents page.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.analysis.document_narrative import _numbers
from app.analysis.llm import call_llm_json
from app.analysis.workflow_engine import SCENARIO_LABELS

SYSTEM = (
    "你在读汽车企业用户对合规助手说的话，找出“数据从哪个国家发出、传到哪个国家”以及业务用途。只依据用户的原话。\n"
    "source_country / recipient_country：国家的中文名（如“埃及”“中国”“德国”）。用户必须明确说到这个国家或其城市、"
    "或用“国内/总部”明确指中国（用户是中国车企）；没说到就留空，不要推测。车辆在哪国运行、数据在哪国产生，那就是发出国；"
    "数据传回、存到、发往哪国，那就是接收国。\n"
    "function：从 functions 里选一个最接近的键；都不像就留空。\n"
    "business：用不超过 30 个字概括业务（不得出现原话里没有的数字）。vehicle：车型或车队（原话里有才填，逐字摘抄）。\n"
    "evidence：分别逐字摘抄支撑发出国、接收国的原话片段；place：分别写出原话里指这个国家的那个地名（国名、城市名，或“国内”“总部”），逐字照抄。\n"
    '只输出 JSON：{"source_country":"","recipient_country":"","function":"","business":"","vehicle":"",'
    '"evidence":{"source":"","recipient":""},"place":{"source":"","recipient":""}}'
)
_CHINA_WORDS = ("中国", "国内", "境内", "总部")


@dataclass
class Route:
    source: str = ""
    recipient: str = ""
    function: str = ""
    business: str = ""
    vehicle: str = ""


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _named(country: str, evidence: str, place: str, said: str) -> bool:
    """The country is in the user's own words: the quote is verbatim and names the country itself, a
    place in it (a city — which country it is in is the model's reading, the city must be what the user
    wrote), or, for China, home (“国内”“总部”)."""
    if not country or not evidence or _flat(evidence) not in _flat(said):
        return False
    if country in evidence:
        return True
    if country == "中国" and any(w in evidence for w in _CHINA_WORDS):
        return True
    place = place.strip()
    return len(place) >= 2 and place in evidence and place not in _CHINA_WORDS


def _business(text: str, said: str) -> str:
    text = text.strip()[:30]
    return "" if _numbers(text) - _numbers(said) else text  # 概括里不许出现原话没有的数字


async def read_route(said: str) -> Route:
    """``said``: everything the user has written in this conversation. Raises when the model is down."""
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps({"said": said[-4000:], "functions": SCENARIO_LABELS}, ensure_ascii=False)},
    ]
    res = await call_llm_json(messages, max_tokens=1500, temperature=0)
    data = res.data if isinstance(res.data, dict) else {}
    ev = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    pl = data.get("place") if isinstance(data.get("place"), dict) else {}
    src, dst = str(data.get("source_country") or "").strip(), str(data.get("recipient_country") or "").strip()
    route = Route(
        source=src if _named(src, str(ev.get("source") or ""), str(pl.get("source") or ""), said) else "",
        recipient=dst if _named(dst, str(ev.get("recipient") or ""), str(pl.get("recipient") or ""), said) else "",
        function=str(data.get("function") or "") if data.get("function") in SCENARIO_LABELS else "",
        business=_business(str(data.get("business") or ""), said),
        vehicle="",
    )
    vehicle = str(data.get("vehicle") or "").strip()
    if vehicle and _flat(vehicle) in _flat(said):
        route.vehicle = vehicle[:128]
    if route.source and route.source == route.recipient:
        route.recipient = ""  # 同一个国家不是跨境，宁可再问一句
    return route
