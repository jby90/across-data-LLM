"""Token-accurate scrubber for internal engineering acronyms so user/admin views, error responses,
audit payloads, and process notes stay in clear Chinese without corrupting English words."""

from __future__ import annotations

import re

_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9_]*[A-Za-z][A-Za-z0-9_]*")
_TRAILING_VER_RE = re.compile(r"(?:_?v?\d+)$")

_ENGLISH_RAG_WORDS = frozenset(
    {
        "brag",
        "crag",
        "drag",
        "flag",
        "frag",
        "defrag",
        "gag",
        "hag",
        "lag",
        "mag",
        "nag",
        "sag",
        "shag",
        "slag",
        "snag",
        "stag",
        "swag",
        "tag",
        "wag",
        "zigzag",
        "dishrag",
        "washrag",
        "ragtag",
        "ragtime",
        "raga",
        "ragout",
        "ragweed",
    }
)

_JSON_TOKENS = frozenset(
    {
        "json",
        "jsonl",
        "json5",
        "jsonb",
        "ndjson",
        "geojson",
        "orjson",
        "ujson",
        "rapidjson",
        "simplejson",
        "fastjson",
        "hjson",
        "cjson",
    }
)


def classify_jargon_token(token: str) -> str | None:
    """Return the jargon category ('ocr'|'llm'|'rag'|'json'|'uuid'|'pydantic'|'cjk') or None."""
    low = token.lower().strip("_")
    if not low:
        return None
    base = _TRAILING_VER_RE.sub("", low).strip("_") or low
    parts = [p for p in base.split("_") if p]

    if "pydantic" in low:
        return "pydantic"
    if "sqlalchemy" in low or "asyncpg" in low:
        return "sqlalchemy"
    if "traceback" in low:
        return "traceback"
    if "deepseek" in low:
        return "llm"
    if low in ("readtimeout", "connecttimeout", "pooltimeout", "writetimeout", "timeoutexception"):
        return "timeout"
    if base in ("uuid", "uuids") or "uuid" in parts or base.startswith("uuid"):
        return "uuid"
    if base in _JSON_TOKENS or "json" in parts or base.startswith("json") or base.endswith("json"):
        if base != "jason":
            return "json"
    if base == "cjk" or "cjk" in parts:
        return "cjk"
    if (
        base in ("llm", "llms")
        or "llm" in parts
        or "llms" in parts
        or base.startswith("llm")
        or base.endswith(("llm", "llms"))
    ):
        return "llm"
    if base != "ocrea" and (
        base == "ocr"
        or "ocr" in parts
        or base.startswith("ocr")
        or base.endswith("ocr")
    ):
        return "ocr"
    if (
        base == "rag"
        or "rag" in parts
        or base in ("ragflow", "ragas")
        or base.startswith(("ragflow", "ragas"))
        or (base.endswith("rag") and base not in _ENGLISH_RAG_WORDS)
    ):
        return "rag"
    return None


def contains_engineering_jargon(text: str | None) -> bool:
    """True when ``text`` contains any banned engineering jargon token or validation boilerplate."""
    if not text:
        return False
    if re.search(
        r"input should be|field required|value error|\b\d+(?:\.\d+)?\s*ms\b",
        text,
        re.IGNORECASE,
    ):
        return True
    return any(
        classify_jargon_token(m.group(0)) is not None for m in _ASCII_TOKEN_RE.finditer(text)
    )


def scrub_engineering_jargon(
    text: str,
    *,
    ocr_zh: str = "版面文字识别",
    llm_zh: str = "智能分析模型",
    rag_zh: str = "条文检索增强",
    json_zh: str = "结构化数据",
    uuid_zh: str = "唯一标识",
    pydantic_zh: str = "格式核验",
    cjk_zh: str = "中文汉字",
    sqlalchemy_zh: str = "数据存储服务",
    traceback_zh: str = "运行异常记录",
    timeout_zh: str = "网络请求超时",
) -> str:
    """Replace all engineering jargon tokens (including prefix/suffix compounds) while preserving
    ordinary English prose words such as 'paragraph', 'storage', 'democrat', or 'hallmark'."""
    if not text:
        return text
    mapping = {
        "ocr": ocr_zh,
        "llm": llm_zh,
        "rag": rag_zh,
        "json": json_zh,
        "uuid": uuid_zh,
        "pydantic": pydantic_zh,
        "cjk": cjk_zh,
        "sqlalchemy": sqlalchemy_zh,
        "traceback": traceback_zh,
        "timeout": timeout_zh,
    }

    def _repl(m: re.Match[str]) -> str:
        kind = classify_jargon_token(m.group(0))
        return mapping[kind] if kind is not None else m.group(0)

    out = _ASCII_TOKEN_RE.sub(_repl, text)
    out = re.sub(r"\b(\d+(?:\.\d+)?)\s*ms\b", r"\1 毫秒", out, flags=re.IGNORECASE)
    return out
