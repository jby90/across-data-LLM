# ruff: noqa: E501
"""受法律术语库约束的真实大模型机器翻译、译前脱敏、译前术语提取、长条款分块与译后程序检查。

遵循 S1 规范:
1. 发往外部大模型前先做敏感信息脱敏(邮箱、电话、证件号)。
2. 译前提取条款中命中的术语(国家覆盖优先于通用概念);保留术语命中统计,不再产出本地假译文。
3. 分块:长条款按句/款拆分,每块 <= 约 1500 字符,保持顺序合并。
4. 提示词(translate-v4):法律翻译、逐句忠实、不增不减、保留条号与数字、术语表强制。
5. 调用 OpenAI 兼容接口(沿用 settings.llm_*);并发用 asyncio.Semaphore(默认 4)限流,带指数退避重试。
6. 译后程序检查(任一失败则重译一次,仍失败标 translation_failed 并记录原因):
   ① CJK 占比 >= 30%(阿语/法语/英文原文都应翻成中文);
   ② 原文中的数字、金额、百分比、年份、条号在译文中全部出现;
   ③ 术语表要求的译法出现;
   ④ 译文不等于原文。
7. 状态机:machine_translated / translation_failed / pending_review / approved /
   rejected / stale / not_translated / superseded。
8. 成本核算:记录 engine=llm、model_name、prompt_version、tokens_in、tokens_out、duration_ms。
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import re
import time
from datetime import UTC, date, datetime
from typing import Any

import httpx  # noqa: F401 - compatibility for existing mocks of shared HTTP transport
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.ingestion.models import Clause, Document, DocumentVersion
from app.knowledge.modality import modality_issues
from app.knowledge.models import (
    ClauseTranslation,
    GlossaryTerm,
    TranslationChunkCache,
    TranslationReviewStatus,
)
from app.knowledge.numeric_integrity import (
    MONTHS,
    NUMERIC,
    canonical,
    dates,
    is_numeric_only,
    missing_amounts,
    normalize,
    tokens,
)

log = logging.getLogger(__name__)

PROMPT_VERSION = "translate-v13"  # v13：六道译后质量门槛升级（新增译文完整性覆盖率、半句断尾拦截与款项序号格式统一）
COMPATIBLE_TRANSLATION_CACHE_PROMPTS = (PROMPT_VERSION, "translate-v12", "translate-v11")
MAX_CHUNK_CHARS = 1500
MAX_CHUNK_NUMBERS = 30
DEFAULT_CONCURRENCY = 4

_META_POLLUTION_RE = re.compile(
    r"\[REDACTED_[A-Z_]+\]|<Placeholder>|<占位符>"
    r"|_*CYSH_(?:PRIVATE|LITERAL|DATE|SECRET|RESTORE)[A-Za-z0-9_]*"
    r"|无法执行(?:该)?翻译|无法完成(?:该)?翻译|待翻译原文仅包含|不含任何可翻译|没有可据以(?:逐句)?忠实翻译"
    r"|原文仅含(?:分隔线|装饰性|页码|标点)|无可翻译的?(?:法律)?正文|〔此段暂无中文〕"
    r"|抱歉[，,]?\s*我?无法(?:对上述|为您)?|请提供完整的?(?:待翻译)?(?:法条)?原文|以下是(?:该|上述)?(?:法条|条款)?的?中文译文\s*[:：]"
    r"|原文此处疑似缺失|版面行号|此处版面行|原文所引用的相关法规编号标记为|受保护的数字或金额|（受保护内容）|\(受保护内容\)"
    r"|translate-v\d+|作为\s*AI\s*(?:语言)?模型",
    re.IGNORECASE,
)
_CYSH_LEAK_RE = re.compile(r"_*CYSH_(?:PRIVATE|LITERAL|DATE|SECRET|RESTORE)[A-Za-z0-9_]*")
_BARE_PROCESSOR_RE = re.compile(r"(?<![微中央信号协图形网络流数字音频视频图像])处理器")
_BIS_SOURCE_RE = re.compile(
    r"(?:\b(?:Article|Art\.?|Section)\s+\d+\s+bis\b|(?:المادة|الفصل)\s*\(?\s*[\d٠-٩]+\s*\)?\s*مكرر)",
    re.IGNORECASE,
)

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?:\+\d{1,3}[-\s]?)?\b\d{3,4}[-\s]\d{4,8}\b")
_SG_NRIC_RE = re.compile(r"\b[STFGM]\d{7}[A-Z]\b")
_NUM_TOKEN_RE = NUMERIC
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_WS_RE = re.compile(r"\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？；;])\s+")

_EASTERN_ARABIC_MAP = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹০১২৩৪৫৬৭৮৯๐๑๒๓๔๕๖๗๘๙％",
    "0123456789012345678901234567890123456789%",
)

# 进程级并发信号量(按需绑定当前事件循环)
_CONCURRENCY_LIMIT: int = DEFAULT_CONCURRENCY
_SEMAPHORE: asyncio.Semaphore | None = None
_SEMAPHORE_LOOP_ID: int | None = None


def set_translation_concurrency(concurrency: int) -> None:
    """设置翻译并发限流上限(默认 4)。"""
    global _CONCURRENCY_LIMIT, _SEMAPHORE, _SEMAPHORE_LOOP_ID
    _CONCURRENCY_LIMIT = max(1, int(concurrency))
    _SEMAPHORE = None
    _SEMAPHORE_LOOP_ID = None


def _get_llm_semaphore() -> asyncio.Semaphore:
    global _SEMAPHORE, _SEMAPHORE_LOOP_ID
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = None
    if _SEMAPHORE is None or _SEMAPHORE_LOOP_ID != loop_id:
        _SEMAPHORE = asyncio.Semaphore(_CONCURRENCY_LIMIT)
        _SEMAPHORE_LOOP_ID = loop_id
    return _SEMAPHORE


# 未纳入已批术语库、但车企合规阅读常遇到的候选解释词典(用于"对不上的词保留原词加解释")
_PROVISIONAL_EXPLANATIONS: dict[str, tuple[str, str, str]] = {
    "telematics": (
        "车载远程信息处理数据",
        "车辆通过无线网络采集并回传的运行状态、位置、诊断与驾驶行为数据。",
        "行业通行定义(待各国法规专项确认)",
    ),
    "black box": (
        "车载事件数据记录器(EDR/黑匣子)",
        "记录车辆碰撞或自动驾驶接管前后关键控制与传感器数据的车载装置。",
        "SG Road Traffic (Autonomous Motor Vehicles) Rules 2017 r 9",
    ),
    "data recorder": (
        "数据记录装置",
        "自动驾驶测试车辆强制搭载的、用于记录系统状态与故障数据的装置。",
        "SG Road Traffic (Autonomous Motor Vehicles) Rules 2017 r 9",
    ),
    "vital interest": (
        "重大利益/生命安全利益",
        "涉及自然人生命、健康或紧急安全的情形,部分国家允许豁免事前同意。",
        "国际通行概念(参考 GDPR Art. 6(1)(d))",
    ),
    "anonymised": (
        "匿名化数据",
        "经不可逆处理后无法再识别特定自然人的数据;各国对匿名化不可逆标准存在差异。",
        "SG PDPC Advisory Guidelines on Anonymisation",
    ),
    "sous-traitant": (
        "数据处理者(法文原词)",
        "代表数据控制者(responsable du traitement)处理个人数据的自然人或法人。",
        "Loi n° 09-08 (MA) Art. 1 / Loi 2004-63 (TN)",
    ),
}


def redact_sensitive_text(text: str) -> str:
    """发往外部大模型 API 前脱敏邮箱、电话与身份证件号。"""
    out = _EMAIL_RE.sub("[REDACTED_EMAIL]", text or "")
    out = _SG_NRIC_RE.sub("[REDACTED_ID]", out)
    out = _PHONE_RE.sub("[REDACTED_PHONE]", out)
    return out


def _redact_with_mapping(text: str) -> tuple[str, dict[str, str]]:
    """Only placeholders leave the process; exact original tokens are restored locally.

    Even a legal reference mistaken for a phone number can then be preserved faithfully.
    """
    from app.core.redaction import Redactor, egress_context

    r = Redactor(egress_context.get().aliases, prefix="__CYSH_PRIVATE_")
    return r.redact(text or ""), r.mapping


def _protect_numeric_literals(text: str) -> tuple[str, dict[str, str]]:
    """Protect exact numbers and explicit prefix currencies in transit, never rewrite source.

    The strict verifier still receives the complete original and restored translation.
    These placeholders cannot turn a failed translation into a passed one by themselves.
    """
    mapping = {}

    def replace(match):
        number, letters = len(mapping), ""
        while True:
            number, remainder = divmod(number, 26)
            letters = chr(65 + remainder) + letters
            if not number:
                break
        marker = f"__CYSH_LITERAL_{letters}__"
        mapping[marker] = match.group()
        return marker

    currency_amount = re.compile(
        r"(?<![A-Za-z])(?:US\$|S\$|A\$|C\$|R\$|Mex\$|S/\.|"
        r"SGD|USD|EUR|GBP|MAD|EGP|TND|LBP|CNY|RMB|JPY|KRW|THB|MYR|IDR|VND|PHP|"
        r"AED|SAR|QAR|KWD|OMR|DZD|RUB|KZT|KGS|TRY|ILS|INR|PKR|BDT|AUD|CAD|BRL|"
        r"MXN|ARS|CLP|COP|PEN|ZAR|NGN|PLN|SEK|\$|€|£|₩|฿|₫|₱|₽|₸|₺|₪|₹|৳|₦)"
        r"\s*\d+(?:,\d{3})*(?:\.\d+)?",
        re.IGNORECASE,
    )
    safe = re.sub(r"٪\s*\d+(?:[٫.]\d+)?", replace, text)
    safe = currency_amount.sub(replace, safe)
    return _NUM_TOKEN_RE.sub(replace, safe), mapping


def restore_markers(content: str, tokens: dict[str, str]) -> str:
    """Put the protected originals back. Dense text sometimes comes back with a placeholder
    shortened ("DATE_A", "_CYSH_DATE_A"); the letters still identify it, so those forms are
    restored too. Only whole placeholders are matched, never part of another word."""
    for marker, original in tokens.items():
        content = content.replace(marker, original)
        core = marker.strip("_")  # CYSH_DATE_A
        short = core.removeprefix("CYSH_")  # DATE_A
        if not (short.startswith(("DATE_", "LITERAL_"))):
            continue  # 私密占位符必须原样还原，不做宽松匹配
        pattern = rf"(?<![A-Za-z0-9_])_*(?:CYSH_)?{re.escape(short)}_*(?![A-Za-z0-9_])"
        content = re.sub(pattern, lambda _m, o=original: o, content)
    return content


def _protect_explicit_dates(text: str) -> tuple[str, dict[str, str]]:
    """Format only fully explicit, valid day/month/year dates, without model guessing."""
    mapping = {}
    month_words = "|".join(sorted(MONTHS, key=len, reverse=True))

    def replace(match):
        day, month, year = match.group("day", "month", "year")
        try:
            value = date(int(year), MONTHS[month.casefold()], int(day))
        except (KeyError, ValueError):
            return match.group()
        number, letters = len(mapping), ""
        while True:
            number, remainder = divmod(number, 26)
            letters = chr(65 + remainder) + letters
            if not number:
                break
        marker = f"__CYSH_DATE_{letters}__"
        mapping[marker] = f"{value.year}年{value.month}月{value.day}日"
        return marker

    for pattern in (
        rf"(?<!\d)(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:\s+day\s+of)?\s+(?P<month>{month_words})\s*[,،]?\s*(?P<year>\d{{4}})(?!\d)",
        rf"(?P<month>{month_words})\s+(?P<day>\d{{1,2}}),?\s+(?P<year>\d{{4}})(?!\d)",
    ):
        text = re.sub(pattern, replace, text, flags=re.IGNORECASE)
    return text, mapping


def compute_cjk_ratio(text: str) -> float:
    """计算去除空白字符后的中日韩统一表意文字(CJK)占比。"""
    compact = _WS_RE.sub("", text or "")
    if not compact:
        return 0.0
    return len(_CJK_RE.findall(compact)) / len(compact)


_URL_OR_EMAIL_RE = re.compile(r"(?:https?://|www\.)\S+|[\w.+-]+@[\w-]+\.[\w.-]+")
_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9&/-]+\b")


MIN_FLEX_LETTERS = 40  # 原文可译字母少于此数的短片段（表格单元、条号行）按严格口径，防止凑字数


def translatable_cjk_ratio(text: str, kept_terms: tuple[str, ...] = ()) -> float:
    """中文字符占“该翻成中文的字母”的比例。

    网址、邮箱、全大写缩写（GDPR、TLP）和术语表要求保留的原词，本来就不译成中文，
    不能拿它们去拖低占比；数字、标点、空白更不是字母。没有任何该翻的字母时返回 1.0。
    """
    s = text or ""
    for term in kept_terms:
        words = [re.escape(w) for w in term.split()]
        if words:
            s = re.sub(r"\s+".join(words), " ", s, flags=re.IGNORECASE)
    s = _ACRONYM_RE.sub(" ", _URL_OR_EMAIL_RE.sub(" ", s))
    cjk = len(_CJK_RE.findall(s))
    others = sum(1 for ch in s if ch.isalpha() and not _CJK_RE.match(ch))
    return 1.0 if cjk + others == 0 else cjk / (cjk + others)


def _translatable_letters(text: str) -> int:
    """Letters that a translator has to render: not URLs, e-mails or all-capital acronyms."""
    s = _ACRONYM_RE.sub(" ", _URL_OR_EMAIL_RE.sub(" ", text or ""))
    return sum(1 for ch in s if ch.isalpha())


def _normalize_digits(text: str) -> str:
    return normalize(text)


def extract_numeric_tokens(text: str) -> list[str]:
    """提取原文中的数字、金额数值、百分比、年份与条号数字(去重保序)。"""
    norm = _normalize_digits(text)
    seen: set[str] = set()
    tokens: list[str] = []
    for tok in _NUM_TOKEN_RE.findall(norm):
        t = tok.strip()
        if t and t not in seen:
            seen.add(t)
            tokens.append(t)
    return tokens


def _keep_date_together(cur: str, nxt: str) -> tuple[str, str]:
    """A chunk may not end in the middle of a written date ("du 6 | février 2018", "6 février |
    2018"): the words that start the date move to the next chunk, so each chunk carries whole
    dates and the per-chunk date check can see them. Returns (chunk text, words carried over)."""
    words = cur.split(" ")
    nxt_l = nxt.casefold().strip(",،.;")
    if nxt_l in MONTHS and len(words) > 1 and re.fullmatch(r"\d{1,2}(?:st|nd|rd|th|er)?", words[-1]):
        return " ".join(words[:-1]), words[-1]
    if (
        re.fullmatch(r"\d{4}", nxt_l)
        and len(words) > 2
        and words[-1].casefold().strip(",،.;") in MONTHS
        and re.fullmatch(r"\d{1,2}(?:st|nd|rd|th|er)?", words[-2])
    ):
        return " ".join(words[:-2]), " ".join(words[-2:])
    return cur, ""


def split_clause_into_chunks(
    text: str, max_chars: int = MAX_CHUNK_CHARS, max_numbers: int = MAX_CHUNK_NUMBERS
) -> list[str]:
    """将长条款按款/句拆分为每块 <= max_chars 字符、数字不超过 max_numbers 个的片段,保持原有顺序。

    数字多的块（税则号清单、费率表）翻译时每个数字都要原样带回，数量一多模型就会丢，
    所以数字密集的块要切得更短，而不是只看字数。
    """

    def too_big(piece: str) -> bool:
        return len(piece) > max_chars or len(_NUM_TOKEN_RE.findall(piece)) > max_numbers

    cleaned = (text or "").strip()
    if not cleaned:
        return [""]
    if not too_big(cleaned):
        return [cleaned]

    # 先按换行分段,再对超长段按句号/分号切分,仍超长则按空格或硬切
    atomic_segments: list[str] = []
    for raw_para in cleaned.splitlines():
        para = raw_para.strip()
        if not para:
            continue
        if not too_big(para):
            atomic_segments.append(para)
            continue
        for sent in _SENTENCE_SPLIT_RE.split(para):
            s = sent.strip()
            if not s:
                continue
            if not too_big(s):
                atomic_segments.append(s)
            else:
                # 按词边界切分超长单句
                words = s.split(" ")
                cur = ""
                for w in words:
                    cand = f"{cur} {w}".strip() if cur else w
                    if not too_big(cand):
                        cur = cand
                    else:
                        carry = ""
                        if cur:
                            cur, carry = _keep_date_together(cur, w)
                            atomic_segments.append(cur)
                        while len(w) > max_chars:
                            atomic_segments.append(w[:max_chars])
                            w = w[max_chars:]
                        cur = f"{carry} {w}".strip() if carry else w
                if cur:
                    atomic_segments.append(cur)

    chunks: list[str] = []
    current = ""
    for seg in atomic_segments:
        candidate = f"{current}\n{seg}" if current else seg
        if not too_big(candidate):
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = seg
    if current:
        chunks.append(current)
    # A trailing citation/heading is not a meaningful standalone translation unit.
    # Move real neighbouring source text into that chunk; never edit a persisted clause.
    if len(chunks) > 1 and len(chunks[-1]) < 120 and len(chunks[-2]) > 400:
        previous, tail = chunks[-2:]
        start = max(1, len(previous) - min(300, max_chars - len(tail) - 1))
        boundary = previous.find(" ", start)
        if boundary > 0:
            chunks[-2] = previous[:boundary]
            chunks[-1] = previous[boundary:].lstrip() + "\n" + tail
    return chunks or [cleaned[:max_chars]]


def select_effective_terms(
    terms: list[GlossaryTerm], country_code: str, language: str
) -> list[GlossaryTerm]:
    """按"国家特定覆盖 > 跨国通用术语"及来源优先级(1->4)筛选生效术语。"""
    from app.knowledge.legal_terms import is_stopword_term

    lang = language.lower()
    by_concept: dict[str, list[GlossaryTerm]] = {}
    for t in terms:
        if t.language.lower() != lang or t.review_status == "rejected":
            continue
        if is_stopword_term(t.source_term, t.target_zh):
            continue
        if t.country_code not in (None, country_code.upper()):
            continue
        by_concept.setdefault(t.concept_code, []).append(t)

    effective: list[GlossaryTerm] = []
    for _, group in by_concept.items():
        group.sort(
            key=lambda x: (0 if x.country_code == country_code.upper() else 1, x.priority_level)
        )
        effective.append(group[0])
    effective.sort(key=lambda x: (x.priority_level, -len(x.source_term)))
    return effective


def match_clause_glossary(
    original_text: str, effective_terms: list[GlossaryTerm]
) -> tuple[list[dict], list[dict]]:
    """在条款原文中识别命中的术语库词条,以及未入库但需悬浮解释的专业词(仅作统计与提示词传入)。"""
    from app.knowledge.legal_terms import is_stopword_term

    hits: list[dict] = []
    lower_text = (original_text or "").lower()
    for term in effective_terms:
        if is_stopword_term(term.source_term, term.target_zh):
            continue
        aliases = getattr(term, "aliases", []) or [term.source_term]
        aliases = sorted(
            (s.strip() for s in aliases if s.strip() and not is_stopword_term(s, term.target_zh)),
            key=len,
            reverse=True,
        )
        if not aliases:
            continue
        expressions = []
        for alias in aliases:
            escaped = r"\s+".join(re.escape(word) for word in alias.split())
            if re.search(r"[\u0600-\u06ff]", alias):
                variants = [escaped]
                if alias.startswith("ال"):
                    variants.append(re.escape("ل" + alias[1:]))
                escaped = r"[وف]?(?:[بك])?(?:" + "|".join(variants) + ")"
            expressions.append(escaped)
        pattern = re.compile(r"(?<!\w)(?:" + "|".join(expressions) + r")s?(?!\w)", re.I)
        match = pattern.search(original_text)
        matched = bool(match)
        if matched:
            hits.append(
                {
                    "term_id": str(term.id),
                    "concept_code": term.concept_code,
                    "source_term": match.group(),
                    "canonical_source": term.source_term,
                    "target_zh": term.target_zh,
                    "priority_level": term.priority_level,
                    "statutory_definition": term.statutory_definition,
                    "source_reference": term.source_reference,
                    "country_code": term.country_code,
                    "source_url": getattr(term, "source_url", ""),
                    "source_kind": getattr(term, "source_kind", "legacy_rendering"),
                    "definition_id": getattr(term, "definition_id", None),
                    "preserve_original": getattr(term, "preserve_original", False),
                    "matched_in_translation": True,
                }
            )

    hit_sources = {h["source_term"].lower() for h in hits}
    unmatched: list[dict] = []
    for raw_term, (prov_zh, definition, ref) in _PROVISIONAL_EXPLANATIONS.items():
        if raw_term in hit_sources:
            continue
        if raw_term in lower_text:
            unmatched.append(
                {
                    "source_term": raw_term,
                    "provisional_zh": prov_zh,
                    "statutory_definition": definition,
                    "source_reference": ref,
                }
            )
    return hits, unmatched


def _number_appears_in_translation(
    num_token: str, translated_norm: str, translated_no_comma: str
) -> bool:
    """检查原文数字/金额/百分比/年份/条号是否在译文中出现。"""
    return not num_token.strip() or canonical(num_token) in tokens(translated_norm)


def verify_translation(
    original_text: str,
    translated_text: str,
    glossary_hits: list[dict],
    clause_number: str | None = None,
    *,
    allow_flex_cjk: bool = False,
) -> tuple[bool, list[str], list[dict]]:
    """译后程序检查(四项强校验):
    ① CJK 占比 >= 30%(阿语/法语/英文原文都应翻成中文);
    ② 原文中的数字、金额、百分比、年份、条号在译文中全部出现;
    ③ 术语表要求的译法出现;
    ④ 译文不等于原文且非空。
    """
    issues: list[str] = []
    orig_clean = (original_text or "").strip()
    trans_clean = (translated_text or "").strip()
    if orig_clean and not any(char.isalpha() for char in orig_clean):
        issues.append("原文仅有数字或引用标记，不能冒充独立正文翻译；须关联真实原文上下文")

    # ④ 译文非空且不等于原文
    if not trans_clean:
        issues.append("译文为空")
    elif trans_clean == orig_clean:
        issues.append("译文与原文完全相同")
    if _CYSH_LEAK_RE.search(trans_clean):
        issues.append("脱敏占位符丢失或未能安全还原，译文不完整")
    if _META_POLLUTION_RE.search(trans_clean):
        issues.append("译文混入模型拒答说明、行号凑字或未还原占位标记，严禁入库")
    if _BIS_SOURCE_RE.search(orig_clean) and re.search(r"重复条|（重复）|\(重复\)", trans_clean):
        issues.append("条号后的 bis/مكرر 应规范译为“之二”，不得译为“重复”")
    # A numeric table cell must not gain invented prose just to meet the CJK gate.
    # These closed vocabulary cases have unambiguous short labels. Other legal
    # prose still needs ordinary translation and human semantic review.
    table_labels = {
        ("per", "number"): {
            "每个号码",
            "每一号码",
            "每号码",
            "每个编号",
            "每一编号",
            "按号码",
            "按编号",
        },
        ("amount",): {"金额", "数额"},
        ("fee",): {"费用", "收费", "费额"},
        ("processing", "fee"): {"手续费", "处理费", "办理费", "办理手续费", "处理手续费"},
    }
    source_words = tuple(re.findall(r"[A-Za-z]+", orig_clean.casefold()))
    if (
        source_words in table_labels
        and "".join(_CJK_RE.findall(trans_clean)) not in table_labels[source_words]
    ):
        issues.append("短表格单元译文含原文未支持的扩写；须关联真实表头或上下文")

    # ① CJK 字符占比 >= 30%
    kept = tuple(h["source_term"] for h in glossary_hits if h.get("preserve_original"))
    if allow_flex_cjk or _translatable_letters(orig_clean) >= MIN_FLEX_LETTERS:
        cjk_ratio = translatable_cjk_ratio(trans_clean, kept)
    else:
        cjk_ratio = compute_cjk_ratio(trans_clean)
    if cjk_ratio < 0.30:
        issues.append(f"CJK 字符占比未达标: {cjk_ratio:.1%} < 30.0%")

    # ③ 术语表要求的译法全部出现
    updated_hits: list[dict] = []
    for h in glossary_hits:
        # 原词跨行（PDF 换行）时术语里带换行：比较前统一空白，免得把对的译法判错
        squashed = " ".join(trans_clean.split())
        ok = bool(trans_clean) and (" ".join(h["target_zh"].split()) in squashed)
        if h.get("preserve_original"):
            trans_folded = " ".join(trans_clean.casefold().split())
            src_folded = " ".join(h["source_term"].casefold().split())
            canon_folded = " ".join((h.get("canonical_source") or h["source_term"]).casefold().split())
            target_unrendered = not any("\u4e00" <= ch <= "\u9fff" for ch in (h.get("target_zh") or ""))
            if target_unrendered:
                ok = bool(trans_clean) and (src_folded in trans_folded or canon_folded in trans_folded)
            elif src_folded not in trans_folded and canon_folded not in trans_folded:
                ok = False
        item = dict(h, matched_in_translation=ok)
        updated_hits.append(item)
        if not ok:
            if h.get("preserve_original") and not any("\u4e00" <= ch <= "\u9fff" for ch in (h.get("target_zh") or "")):
                issues.append(
                    f"术语须保留原词: 译为中文时须紧跟括号写出原词 '{h['target_zh']}'（如 '（{h['target_zh']}）'）"
                )
            elif h.get("preserve_original") and h["target_zh"] in trans_clean:
                # 译法对了、只是没带原词：提示要说准，否则重试时模型会反复改译法而不是补原词
                issues.append(
                    f"术语须保留原词: 译为 '{h['target_zh']}' 时须紧跟括号写出原词，"
                    f"如 '{h['target_zh']}（{h['source_term']}）'"
                )
            else:
                issues.append(
                    f"术语未按规定译法输出: 原词 '{h['source_term']}' 必须译为 '{h['target_zh']}'"
                )
        # A correct label elsewhere does not legitimise an everyday mistranslation.
        if h.get("concept_code") == "data_controller" and "控制器" in trans_clean:
            issues.append("法律语境中的 controller 不得译为控制器")
        if h.get("concept_code") == "data_processor" and _BARE_PROCESSOR_RE.search(trans_clean):
            issues.append("法律语境中的 processor 不得译为处理器")
    if (
        re.search(r"\bdata\s+processors?\b|\bsous-traitants?\b", orig_clean, re.IGNORECASE)
        and _BARE_PROCESSOR_RE.search(trans_clean)
        and not any("processor 不得译为处理器" in x for x in issues)
    ):
        issues.append("法律语境中的 processor/sous-traitant 应译为数据处理者，不得译为处理器")

    # ② 原文中的数字、金额、百分比、年份、条号在译文中全部出现
    translated_norm = _normalize_digits(trans_clean)
    translated_no_comma = translated_norm.replace(",", "")
    orig_nums = extract_numeric_tokens(orig_clean)
    # Persisted derived identifiers (e.g. 53.2) are not source numbers. Every real source
    # token is already in orig_nums; substring matching "2" inside "2020" invents an obligation.

    for num in orig_nums:
        if not _number_appears_in_translation(num, translated_norm, translated_no_comma):
            issues.append(f"译文缺失原文数字或比例: {num}")
    for amount in missing_amounts(orig_clean, trans_clean):
        issues.append(f"译文金额或币种不一致: {amount}")
    for year, month, day in sorted(dates(orig_clean) - dates(trans_clean)):
        issues.append(
            f"译文日期不一致: {year:04}-{month:02}-{day:02}；原文中的这个日期须完整译为"
            f"“{year}年{month}月{day}日”，年、月、日连写，不可拆开或省略年份"
        )

    # ⑤ 否定词一致：“不得/禁止”不能译丢，也不能凭空译出（只抓高把握的错误，见 modality.py）
    issues.extend(modality_issues(orig_clean, trans_clean))

    # ⑥ 译文完整性与半句截断门禁（防只译封面漏译正文、防句末停在虚词/逗号断句）
    orig_no_leaders = re.sub(r"[.…·\-_]{4,}", " ", orig_clean)
    dedup_orig = " ".join(
        dict.fromkeys(
            s.strip() for s in re.split(r"(?<=[.!?;:。！？])\s+|\n+", orig_no_leaders) if s.strip()
        )
    )
    dedup_compact = _WS_RE.sub("", dedup_orig)
    trans_compact = _WS_RE.sub("", trans_clean)
    if (
        len(dedup_compact) >= 600
        and trans_compact
        and len(trans_compact) / len(dedup_compact) < 0.14
    ):
        issues.append(
            f"译文篇幅严重不足（仅占去重去点线后原文的 {len(trans_compact) / len(dedup_compact):.1%}），"
            "疑似漏译后段或目录正文，须逐段完整翻译"
        )
    if (
        len(trans_clean) >= 15
        and re.search(r"[.!?؟。！？]\s*$", orig_clean)
        and re.search(
            r"(?:[，、]|(?:以及|或者|并且|针对|关于|根据|依据|按照|不具有额外的|进行传输的|电子邮件地址和))\s*$",
            trans_clean,
        )
    ):
        issues.append("译文在句末未完整收尾（停在逗号、顿号或悬空连词/介词），须补全完整句子")

    return len(issues) == 0, issues, updated_hits


def _build_translation_prompts(
    safe_text: str,
    language: str,
    glossary_hits: list[dict],
    unmatched_terms: list[dict],
    retry_issues: list[str] | None = None,
) -> tuple[str, str]:
    """构造版本化系统提示词与用户提示词。"""
    sys_lines = [
        f"你是跨境汽车与数据合规领域的专业法律翻译引擎（提示词版本 {PROMPT_VERSION}）。",
        "任务性质：法律翻译、逐句忠实、不增不减、保留条号与数字、术语表强制。",
        "请将给定的外文法律条款精准翻译为简体中文，并严格遵守以下强制准则：",
        (
            "1. 【逐句忠实、不增不减】：严格忠实于原文法律含义，"
            "逐句完整翻译为规范简体中文法律表述，不得擅自增删、概括、省略或添加解释性评注。"
        ),
        (
            "2. 【保留条号与数字】：原文中的所有条号、款项编号、阿拉伯数字、"
            "金额、百分比、日期与年份必须在译文中原样保留阿拉伯数字形式"
            "（如 1,000,000、10%、2020、3 等，严禁遗漏或仅改写为汉字数字）。"
        ),
        (
            "3. 【术语表强制】：凡命中下方【强制法律术语表】的原词，译文中必须严格、"
            "逐字使用规定的中文译法（规定译法），不得替换为同义词或近义词。"
        ),
        (
            "4. 【中文占比要求】：即使原文包含专有名词、机构缩写或公式标题，"
            "主体内容也必须翻译为简体中文（中文字符占比必须 >= 30%），严禁直接照抄外文原文。"
        ),
        (
            "5. 【纯译文输出】：只输出翻译后的简体中文法条正文，"
            "不要输出任何前言、后记、Markdown 代码块标记或说明；"
            "严禁输出“无法执行翻译”“待翻译原文仅包含”“版面行号”“受保护内容”等元解释。"
            "若原文含 <Placeholder>，统一译为“（官方原件留空待填）”，不得输出 <Placeholder> 或 <占位符>。"
        ),
        "6. 【币种与单位】：保留原文货币符号/代码（如原文含 AED、SAR、QAR、KWD、OMR、DZD、INR、PKR、PHP、MXN、ARS、COP、CLP、AUD、CAD、BRL、KRW、JPY、THB、MYR、IDR、VND、RUB、TRY、ZAR、NGN、PLN、SEK 等，须原样保留代码或使用带国别的全称如“阿联酋迪拉姆（AED）”“沙特里亚尔（SAR）”“印度卢比（INR）”“菲律宾比索（PHP）”），$ 不得自行改成美元、新元或欧元。"
        "不得把金额改写为万元/百万；百分比保留 %，日期月份和引用条号必须精确对应原文。",
        "7. __CYSH_PRIVATE_ 开头的占位符必须原样逐一保留，禁止翻译、改写或猜测其内容。",
        "8. __CYSH_LITERAL_ 是受保护的原文数字或金额，必须逐一原样输出，"
        "保留它们在句子、引用及页眉/边栏中的对应位置，不得删除或猜测替代值。"
        "原文页码、行号、档案编号和页眉页脚也必须保留。",
        "9. 目录的装饰性连续点线可用制表符替代，但目录项目及对应页码必须完整保留。"
        "Section 128(1)(a) 应明确译为第128条第(1)款第(a)项，不可遗漏条款层级名称。"
        "不要为提高中文占比添加原文没有的解释或法律结论。",
        "10. 文字月份也应规范转为中文年月日格式，如 19 December 2022 应输出2022年12月19日。"
        "若年份和日是占位符，移动对应占位符到正确位置；月份仍须使用阿拉伯数字，不可留成七月等文字。",
        "11. __CYSH_DATE_ 是经程序核对的完整日期，必须原样保留，系统会还原为准确中文日期。",
        "12. 短表格单元：per number 仅译每个号码，Amount仅译金额，Fee仅译费用，"
        "Processing fee仅译手续费。保留全部数字、金额；"
        "不得添加处理工作、支付或收取等原文没有的文字。"
        "数字表格如无法同时满足中文占比和忠实性，仍只给忠实译文，由系统判定失败，禁止扩写凑字。",
        "13. 【中文法规体例】：按中国法律法规的行文习惯表达，读起来像中文法规，而不是逐词直译。"
        "（1）编、章、节标题写成“第一章 总则”“第三节 车辆的技术条件”：序号与标题之间空一格，不加冒号、破折号；若条文开头、中间或末尾带有编/章/节标题（如 Chapter / Chapitre / Capítulo / Seção / Título / Abschnitt / الباب / الفصل），必须逐一完整译出并保留在原位，严禁将其当作页眉遗漏；"
        "标题用中文法规常见的名词性短语意译，如 De la conduite des véhicules et des animaux 译“车辆及畜力车的驾驶规则”，"
        "不要直译成“车辆和动物的驾驶”；Dispositions générales / أحكام عامة 译“总则”或“一般规定”。"
        "（2）条号写“第47条”“第47条第2款”，不加括号；东阿拉伯数字（٠١٢٣٤٥٦٧٨٩）一律改写为阿拉伯数字（0123456789）；注意区分法典修订版年份“2020 Ed.”或“2020 Rev. Ed.”（应译为“2020年修订版”，严禁误写为“第2020条 Ed.”）。"
        "（3）突尼斯、摩洛哥等国法规里的“الفصل + 数字”和法文 Article 一样是“条”，写“第X条”；“الباب”“Titre”是“编”，“Chapitre”是“章”，“Section”是“节”。"
        "（4）原文独立成行的款项编号 (1)、(2)、(4A) 一律保留括号序号形式写作（1）、（2）、（4A），不得在同一条内混用“第5款”与“（4）”；阿拉伯文分项字母 أ、ب、ج、د、هـ、و 依次写作（一）（二）（三）（四）（五）（六）；法文、英文的 a)、b) 写作（a）、（b）。"
        "（5）术语用中国大陆法律法规的通行说法；除术语表要求保留原词外，不在译文里夹带外文单词。"
        "（6）条号后的 مكرر（法文 bis）译“之二”，مكررا ثانيا（ter）译“之三”，ثالثا（quater）译“之四”，写成“第12条之二”“第4条之三”，"
        "不写“（重复）”“重复条”；同一条里的“最后一款”“最后一段”写“最后一款”。",
    ]
    if glossary_hits:
        sys_lines.append("\n【强制法律术语表（原词 -> 规定译法 -> 法定定义）】：")
        for h in glossary_hits:
            defn = (h.get("statutory_definition") or "仅规范译名，不能据此推定本国法定含义").strip()
            sys_lines.append(
                f"- 原词: {h['source_term']} -> 规定译法: {h['target_zh']} -> 来源释义: {defn}"
                f"；来源类型: {h.get('source_kind', 'legacy_rendering')}"
                f"；出处: {h.get('source_reference', '')}"
            )
            if h.get("preserve_original"):
                if not any("\u4e00" <= ch <= "\u9fff" for ch in (h.get("target_zh") or "")):
                    sys_lines.append(
                        f"该词在本法规中有专门定义，译为中文时须在紧随其后的括号内原样保留外文原词“{h['target_zh']}”"
                        f"（如“影响（{h['target_zh']}）”），不得省略原词“{h['target_zh']}”，也不得不译中文直接裸写外文。"
                    )
                else:
                    sys_lines.append(
                        f"该词须保留原词 {h['source_term']}，并使用暂译 {h['target_zh']}；"
                        "不得自行消除定义冲突。"
                    )
    if unmatched_terms:
        sys_lines.append("\n【专业词汇参考译法】：")
        for u in unmatched_terms:
            sys_lines.append(
                f"- 原词: {u['source_term']} -> 建议译法: {u['provisional_zh']} "
                f"({u['statutory_definition']})"
            )

    req_nums = extract_numeric_tokens(safe_text)
    user_parts = [f"源语言: {language}"]
    protected_markers = re.findall(r"__CYSH_(?:PRIVATE|LITERAL|DATE)_[A-Z]+__", safe_text)
    if protected_markers:
        # 同一个值在原文里出现几次，占位符就出现几次：每个标记只列一行，写明要输出的次数
        counts: dict[str, int] = {}
        for m in protected_markers:
            counts[m] = counts.get(m, 0) + 1
        user_parts.append(
            "完整性清单：下列每个标记必须原样输出，次数与原文相同（未注次数的只输出一次，禁止省略或添加任何解释说明）：\n"
            + "\n".join(m if n == 1 else f"{m}（共 {n} 次）" for m, n in counts.items())
        )
    if req_nums:
        user_parts.append(f"必须在译文中原样保留的数字/金额/百分比/条号: {', '.join(req_nums)}")
    if glossary_hits:
        must_zh = ", ".join(
            f"中文译名（{h['target_zh']}）"
            if h.get("preserve_original") and not any("\u4e00" <= ch <= "\u9fff" for ch in (h.get("target_zh") or ""))
            else f"'{h['target_zh']}'"
            for h in glossary_hits
        )
        user_parts.append(f"必须在译文中原样出现的强制术语译法: {must_zh}")
    if retry_issues:
        user_parts.append(
            "【重要修正指令】上一次翻译未通过译后程序检查，具体原因如下：\n"
            + "\n".join(f"  * {iss}" for iss in retry_issues)
            + "\n请严格修正上述问题，确保所有必现数字、强制术语完整出现且简体中文字符占比 >= 30%！"
        )
    user_parts.append(f"待翻译原文:\n{safe_text}")
    return "\n".join(sys_lines), "\n\n".join(user_parts)


_LIST_START = re.compile(  # a list item or heading starts here: never join it to the line above
    r"^\s*(?:\(?[0-9a-zA-Z]{1,3}[.)]\s|[-•–—*]\s|\([ivxIVX]+\)|Article\b|Section\b|المادة|第)"
)


def unwrap_layout_lines(text: str) -> str:
    """Join lines that the page layout broke in the middle of a sentence.

    A PDF wraps "du 7 mai" and "2002" onto two lines; a model that copies the break puts the year
    in the wrong place, and the date protection does not recognise a date split by a line end.
    A line is joined to the next only when it does not end a sentence and the next line does not
    start a list item or heading; blank lines are kept."""
    lines = (text or "").split(chr(10))
    out: list[str] = []
    for line in lines:
        if (
            out
            and out[-1].strip()
            and line.strip()
            and not re.search(r"[.;:!?؟。]\s*$", out[-1])
            and not _LIST_START.match(line)
            and not (_LIST_START.match(out[-1]) and len(out[-1].strip()) < 40)
        ):
            out[-1] = out[-1].rstrip() + " " + line.lstrip()
        else:
            out.append(line)
    return chr(10).join(out)


async def _call_llm_translate(
    original_text: str,
    language: str,
    glossary_hits: list[dict],
    unmatched_terms: list[dict],
    *,
    retry_issues: list[str] | None = None,
) -> tuple[str, str, int, int, int] | tuple[str, str] | None:
    """调用 OpenAI 兼容接口进行受术语表约束的法条翻译。

    并发通过 asyncio.Semaphore(默认 4)限流,带指数退避重试。
    返回 (translated_text, model_name, tokens_in, tokens_out, duration_ms);
    当未配置密钥或重试后仍不可用时返回 None。
    """
    settings = get_settings()
    if not settings.llm_api_key:
        return None

    original_text = unwrap_layout_lines(original_text)
    safe_text, private_tokens = (
        _redact_with_mapping(original_text)
        if settings.llm_redaction == "on"
        else (original_text, {})
    )
    safe_text, date_tokens = _protect_explicit_dates(safe_text)
    safe_text, numeric_tokens = _protect_numeric_literals(safe_text)
    protected_tokens = private_tokens | date_tokens | numeric_tokens
    sys_prompt, user_prompt = _build_translation_prompts(
        safe_text, language, glossary_hits, unmatched_terms, retry_issues=retry_issues
    )

    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if "deepseek" in (settings.llm_model or "").lower():
        payload["thinking"] = {"type": settings.llm_translation_thinking}
        if settings.llm_translation_thinking == "enabled":
            payload["reasoning_effort"] = "low"

    semaphore = _get_llm_semaphore()
    max_attempts = 3
    t_start = time.perf_counter()

    async with semaphore:
        for attempt in range(max_attempts):
            try:
                from app.analysis.llm import post_model

                resp = await post_model(
                    payload,
                    timeout=settings.llm_timeout_seconds,
                    purpose="clause_translation",
                    protected=private_tokens,
                )
                if resp.status_code in (429, 500, 502, 503, 504) and attempt + 1 < max_attempts:
                    await asyncio.sleep(0.6 * (2**attempt))
                    continue
                resp.raise_for_status()
                data = resp.json()
                content = (data["choices"][0]["message"].get("content") or "").strip()
                # 私密占位符（邮箱、电话、证件号）必须逐个原样还原，否则信息丢了；
                # 数字、金额、日期占位符由后面的程序核对逐项检查（缺了就会被挡下），
                # 这里不因为模型把某个数字写了两遍或换了位置就整条作废
                # 同一个电话、邮箱在一段里出现几次，占位符就出现几次：按原文里的次数核，不是一律 1 次
                invalid_markers = [
                    marker for marker in private_tokens if content.count(marker) != safe_text.count(marker)
                ]
                content = restore_markers(content, protected_tokens)
                if invalid_markers:
                    content += "\n__CYSH_RESTORE_MISSING__"
                usage = data.get("usage") or {}
                tokens_in = int(usage.get("prompt_tokens") or 0)
                tokens_out = int(usage.get("completion_tokens") or 0)
                duration_ms = max(1, int((time.perf_counter() - t_start) * 1000))
                model_used = str(data.get("model") or settings.llm_model)
                return content, model_used, tokens_in, tokens_out, duration_ms
            except Exception as exc:
                if attempt + 1 < max_attempts:
                    await asyncio.sleep(0.6 * (2**attempt))
                    continue
                log.warning(
                    "外部翻译模型调用失败 (已重试 %d 次): %s", max_attempts, type(exc).__name__
                )
                return None
    return None


def _unpack_llm_result(
    res: tuple[Any, ...] | str | None, default_model: str
) -> tuple[str, str, int, int, int] | None:
    """兼容单元测试 monkeypatch 返回的 (text, model) 二元组或完整五元组。"""
    if res is None:
        return None
    if isinstance(res, str):
        return res, default_model, 0, 0, 0
    if len(res) == 2:
        return str(res[0]), str(res[1]), 0, 0, 0
    if len(res) >= 5:
        return str(res[0]), str(res[1]), int(res[2] or 0), int(res[3] or 0), int(res[4] or 0)
    return str(res[0]), default_model, 0, 0, 0


async def _invoke_llm_translate(
    chunk_text: str,
    language: str,
    chunk_hits: list[dict],
    chunk_unmatched: list[dict],
    retry_issues: list[str] | None = None,
) -> tuple[str, str, int, int, int] | None:
    """调用 _call_llm_translate,自动检测被 monkeypatch 的假函数是否支持 retry_issues 关键字参数。"""
    settings = get_settings()
    fn = _call_llm_translate
    supports_retry_kw = False
    try:
        sig = inspect.signature(fn)
        for p in sig.parameters.values():
            if p.name == "retry_issues" or p.kind == inspect.Parameter.VAR_KEYWORD:
                supports_retry_kw = True
                break
    except (ValueError, TypeError):
        supports_retry_kw = False

    if supports_retry_kw and retry_issues:
        raw = await fn(chunk_text, language, chunk_hits, chunk_unmatched, retry_issues=retry_issues)
    else:
        raw = await fn(chunk_text, language, chunk_hits, chunk_unmatched)
    return _unpack_llm_result(raw, settings.llm_model or "llm")


async def _semantic_issues(
    original: str, translated: str, language: str, cache: dict[str, list[str]]
) -> list[str]:
    """模型语义对照核验：返回会挡住译文的错误清单。开关关闭、模型不可用时返回空（不因此拒绝译文）。"""
    if not get_settings().translation_judge_enabled or not translated.strip():
        return []
    if translated in cache:
        return cache[translated]
    from app.knowledge.translation_judge import judge_translation

    verdict = await judge_translation(original, translated, language)
    issues = verdict.issues() if verdict is not None else []
    cache[translated] = issues
    return issues


def _reusable(
    cache: dict[str, str],
    chunk: str,
    clause: Clause,
    hits: list[dict],
    *,
    whole: bool = True,
    allow_flex_cjk: bool = False,
) -> bool:
    """A cached translation is used only if it still passes today's deterministic checks."""
    zh = cache.get(chunk_key(chunk, clause.language))
    if not zh:
        return False
    ok, _, _ = verify_translation(
        clause.original_text if whole else chunk,
        zh,
        hits,
        clause_number=clause.clause_number if whole else None,
        allow_flex_cjk=False if whole else allow_flex_cjk,
    )
    return ok


def chunk_key(chunk: str, language: str) -> str:
    return hashlib.sha256(f"{language}\0{chunk}".encode()).hexdigest()


async def _load_chunk_cache(
    session: AsyncSession, chunks: list[str], language: str
) -> dict[str, str]:
    keys = {chunk_key(c, language) for c in chunks if c.strip()}
    if not keys:
        return {}
    rows = await session.execute(
        select(TranslationChunkCache.chunk_hash, TranslationChunkCache.translated_text).where(
            TranslationChunkCache.chunk_hash.in_(keys),
            TranslationChunkCache.prompt_version.in_(COMPATIBLE_TRANSLATION_CACHE_PROMPTS),
        )
    )
    return {k: v for k, v in rows.all() if not _META_POLLUTION_RE.search(v or "")}


async def _store_chunk_cache(
    session: AsyncSession, items: dict[str, tuple[str, str]], known: dict[str, str]
) -> None:
    """items: key -> (language, translated text). Only passages that passed every check.

    Conflicts are ignored: two workers translating the same passage at once, or a deliberate
    re-translation of a clause, must never fail on a row that is already there.
    """
    from sqlalchemy.dialects.postgresql import insert

    rows = [
        {
            "chunk_hash": key,
            "prompt_version": PROMPT_VERSION,
            "language": language[:8],
            "translated_text": zh,
        }
        for key, (language, zh) in items.items()
        if key not in known and zh.strip()
    ]
    if rows:
        await session.execute(
            insert(TranslationChunkCache)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_translation_chunk_cache")
        )


async def translate_clause(
    session: AsyncSession,
    clause: Clause,
    country_code: str,
    terms: list[GlossaryTerm] | None = None,
) -> ClauseTranslation:
    """对单条法条执行:术语提取 -> 分块 -> LLM 翻译 -> 四项检查(失败重译一次) -> 持久化。"""
    # 条款文字层缺字时，另有一份经双路核对的校读文本：翻译与核验都以它为输入，
    # 库里的 original_text（证据）保持不变；没有校读文本就用 original_text
    from app.ingestion.models import ClauseReading

    reading = await session.scalar(
        select(ClauseReading.reading_text).where(ClauseReading.clause_id == clause.id)
    )
    # 版面折行先接回，再分块：否则一个日期会被切在两块之间，分块核验各自都看不到完整日期；
    # 同时去掉公报页眉、末尾下一章标题等非条文噪声，保证原文与译文两侧核验口径一致
    from app.ingestion.noise import clean_text

    source_text = unwrap_layout_lines(clean_text(reading or clause.original_text))

    settings = get_settings()
    if terms is None:
        terms = list(
            (
                await session.execute(
                    select(GlossaryTerm).where(
                        or_(
                            GlossaryTerm.country_code == country_code.upper(),
                            GlossaryTerm.country_code.is_(None),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )

    from app.knowledge.legal_terms import translation_terms

    terms = await translation_terms(session, clause, country_code, terms)
    effective_terms = select_effective_terms(terms, country_code, clause.language)
    hits, unmatched = match_clause_glossary(source_text, effective_terms)

    # Serialize retries for one clause; never reuse a previous row for new model output.
    await session.execute(select(Clause.id).where(Clause.id == clause.id).with_for_update())
    existing_rows = list(
        (
            await session.scalars(
                select(ClauseTranslation)
                .where(ClauseTranslation.clause_id == clause.id)
                .order_by(ClauseTranslation.updated_at.desc(), ClauseTranslation.id)
            )
        ).all()
    )
    current_rows = [
        row
        for row in existing_rows
        if row.is_current and row.review_status not in ("superseded", "stale", "rejected")
    ]
    approved = next((row for row in current_rows if row.review_status == "approved"), None)
    if approved is not None:
        return approved  # Human approval is bound to exactly this text, not to a retry.
    previous_good = next(
        (
            row
            for row in current_rows
            if row.engine == "llm"
            and row.quality_check_passed
            and verify_translation(source_text, row.translated_text, row.glossary_hits or [])[0]
        ),
        None,
    )

    chunks = split_clause_into_chunks(source_text, max_chars=MAX_CHUNK_CHARS)
    # 已有合格译文的条款是要重新翻译：必须拿到新的模型输出，不复用缓存
    chunk_cache = (
        await _load_chunk_cache(session, chunks, clause.language) if previous_good is None else {}
    )
    new_cache: dict[str, tuple[str, str]] = {}

    total_tokens_in = 0
    total_tokens_out = 0
    total_duration_ms = 0
    model_name = settings.llm_model or "deepseek-flash"
    llm_unavailable = False
    chunk_failures: list[str] = []
    sem_cache: dict[str, list[str]] = {}  # 同一段译文只做一次语义核验

    if is_numeric_only(source_text):
        # Do not pay a model to invent Chinese prose for a citation-only parse fragment.
        translated_text = ""
        model_name = "not_called"
    elif len(chunks) == 1 and _reusable(chunk_cache, chunks[0], clause, hits):
        translated_text = chunk_cache[chunk_key(chunks[0], clause.language)]  # 同一段原文已译过
        sem_cache[translated_text] = []  # 当时已通过语义核验，不再花一次模型调用
    elif len(chunks) == 1:
        # 单块条款:翻译 -> 译后检查 -> 若未通过则携带问题原因重译一次
        first_res = await _invoke_llm_translate(chunks[0], clause.language, hits, unmatched)
        if first_res is None:
            llm_unavailable = True
            translated_text = ""
        else:
            translated_text, model_name, tin, tout, dms = first_res
            total_tokens_in += tin
            total_tokens_out += tout
            total_duration_ms += dms
            passed, issues, checked_hits = verify_translation(
                source_text,
                translated_text,
                hits,
                clause_number=clause.clause_number,
            )
            if passed:
                issues = await _semantic_issues(
                    source_text, translated_text, clause.language, sem_cache
                )
                passed = not issues
            if not passed:
                retry_res = await _invoke_llm_translate(
                    chunks[0],
                    clause.language,
                    hits,
                    unmatched,
                    retry_issues=issues,
                )
                if retry_res is not None:
                    translated_text, model_name, tin2, tout2, dms2 = retry_res
                    total_tokens_in += tin2
                    total_tokens_out += tout2
                    total_duration_ms += dms2
    else:
        parent_is_flex = _translatable_letters(source_text) >= MIN_FLEX_LETTERS

        # 多块长条款:每块独立提取命中术语并校验,单块失败重译一次,按顺序合并后再做整条总校验
        async def _translate_halves(piece: str):
            """A block that failed twice is cut in two at a sentence end and each half is
            translated and checked on its own. Dense text (many dates, figures, exceptions)
            fails as a whole block but passes in smaller parts; nothing is accepted unless
            both halves pass every check."""
            halves = split_clause_into_chunks(piece, max_chars=max(300, len(piece) // 2))
            if len(halves) < 2:
                return None
            parts: list[str] = []
            tin = tout = dms = 0
            model = model_name
            for half in halves:
                if is_numeric_only(half) or (parent_is_flex and _translatable_letters(half) == 0):
                    parts.append(half)
                    continue
                h_hits, h_unmatched = match_clause_glossary(half, effective_terms)
                res = await _invoke_llm_translate(half, clause.language, h_hits, h_unmatched)
                if res is None:
                    return None
                zh, model, a_in, a_out, a_ms = res
                tin, tout, dms = tin + a_in, tout + a_out, a_ms + dms
                ok, found, _ = verify_translation(
                    half, zh, h_hits, allow_flex_cjk=parent_is_flex
                )
                if ok:
                    found = await _semantic_issues(half, zh, clause.language, sem_cache)
                    ok = not found
                if not ok:
                    return None
                parts.append(zh)
            return chr(10).join(parts), model, tin, tout, dms

        async def _translate_single_chunk(chunk_str: str) -> tuple[str | None, str, int, int, int]:
            if is_numeric_only(chunk_str) or (
                parent_is_flex and _translatable_letters(chunk_str) == 0
            ):
                # 多块长条款中的纯数字/纯矩阵代号子块原样直通，绝不送模型编造行号说明
                return chunk_str, model_name, 0, 0, 0
            c_hits, c_unmatched = match_clause_glossary(chunk_str, effective_terms)
            if _reusable(
                chunk_cache, chunk_str, clause, c_hits, whole=False, allow_flex_cjk=parent_is_flex
            ):
                return chunk_cache[chunk_key(chunk_str, clause.language)], model_name, 0, 0, 0
            r1 = await _invoke_llm_translate(chunk_str, clause.language, c_hits, c_unmatched)
            if r1 is None:
                return None, model_name, 0, 0, 0
            c_zh, c_model, tin1, tout1, dms1 = r1
            c_ok, c_issues, _ = verify_translation(
                chunk_str, c_zh, c_hits, allow_flex_cjk=parent_is_flex
            )
            if c_ok:
                c_issues = await _semantic_issues(chunk_str, c_zh, clause.language, sem_cache)
                c_ok = not c_issues
            if c_ok:
                new_cache[chunk_key(chunk_str, clause.language)] = (clause.language, c_zh)
            if not c_ok:
                r2 = await _invoke_llm_translate(
                    chunk_str,
                    clause.language,
                    c_hits,
                    c_unmatched,
                    retry_issues=c_issues,
                )
                if r2 is not None:
                    c_zh, c_model, tin2, tout2, dms2 = r2
                    retry_ok, retry_issues, _ = verify_translation(
                        chunk_str, c_zh, c_hits, allow_flex_cjk=parent_is_flex
                    )
                    if retry_ok:
                        retry_issues = await _semantic_issues(
                            chunk_str, c_zh, clause.language, sem_cache
                        )
                        retry_ok = not retry_issues
                    if not retry_ok:
                        halves = await _translate_halves(chunk_str)
                        if halves is not None:
                            h_zh, h_model, h_in, h_out, h_ms = halves
                            new_cache[chunk_key(chunk_str, clause.language)] = (
                                clause.language,
                                h_zh,
                            )
                            return (
                                h_zh,
                                h_model,
                                tin1 + tin2 + h_in,
                                tout1 + tout2 + h_out,
                                dms1 + dms2 + h_ms,
                            )
                        chunk_failures.extend(retry_issues)
                    else:
                        new_cache[chunk_key(chunk_str, clause.language)] = (clause.language, c_zh)
                    return c_zh, c_model, tin1 + tin2, tout1 + tout2, dms1 + dms2
                chunk_failures.extend(c_issues)
            return c_zh, c_model, tin1, tout1, dms1

        chunk_results = await asyncio.gather(*(_translate_single_chunk(ch) for ch in chunks))
        merged_parts: list[str] = []
        for c_zh, c_model, tin, tout, dms in chunk_results:
            total_tokens_in += tin
            total_tokens_out += tout
            total_duration_ms += dms
            if c_zh is None:
                llm_unavailable = True
                continue
            merged_parts.append(c_zh)
            model_name = c_model
        translated_text = "\n".join(merged_parts) if not llm_unavailable else ""

    if llm_unavailable:
        checked_hits = [dict(h, matched_in_translation=False) for h in hits]
        passed = False
        issues = ["LLM 服务不可用或未配置密钥，未生成译文"]
        next_status = TranslationReviewStatus.NOT_TRANSLATED.value
        translated_text = ""
    else:
        from app.ingestion.noise import clean_translation

        translated_text = clean_translation(translated_text)  # 公报页眉、网页导航、阿拉伯文残片等不进库
        passed, issues, checked_hits = verify_translation(
            source_text,
            translated_text,
            hits,
            clause_number=clause.clause_number,
        )
        if passed and len(chunks) == 1 and translated_text:
            # 单块条款：重译后的最终文本也要过语义核验（首次通过的已在缓存里，不会重复调用）
            semantic = await _semantic_issues(
                source_text, translated_text, clause.language, sem_cache
            )
            if semantic:
                passed = False
                issues.extend(semantic)
        if chunk_failures:
            passed = False
            issues.extend("分块校验未通过: " + issue for issue in chunk_failures)
        passed = bool(passed and not issues)
        next_status = (
            TranslationReviewStatus.MACHINE_TRANSLATED.value
            if passed
            else TranslationReviewStatus.TRANSLATION_FAILED.value
        )

    if len(chunks) == 1 and passed and translated_text and not llm_unavailable:
        new_cache[chunk_key(chunks[0], clause.language)] = (clause.language, translated_text)
    await _store_chunk_cache(session, new_cache, chunk_cache)

    from app.knowledge.history import snapshot

    # A failed attempt is retained for diagnostics, while a good current translation stays usable.
    make_current = passed or previous_good is None
    if make_current:
        for row in existing_rows:
            if not row.is_current:
                continue
            snapshot(session, row, "before_translation_replacement")
            row.is_current = False
            if row.review_status not in ("approved", "rejected"):
                row.review_status = TranslationReviewStatus.SUPERSEDED.value
    attempt = ClauseTranslation(
        clause_id=clause.id,
        is_current=make_current,
        translated_text=translated_text,
        engine="none" if model_name == "not_called" else "llm",
        model_name=model_name,
        prompt_version=(
            PROMPT_VERSION + "-direct"
            if settings.llm_translation_thinking == "disabled"
            else PROMPT_VERSION
        ),
        tokens_in=total_tokens_in,
        tokens_out=total_tokens_out,
        duration_ms=total_duration_ms,
        glossary_hits=checked_hits,
        unmatched_terms=unmatched,
        quality_check_passed=passed,
        quality_issues=issues,
        review_status=next_status,
        updated_at=datetime.now(UTC),
    )
    session.add(attempt)
    await session.flush()
    snapshot(session, attempt, "translation_attempt")
    return attempt


async def translate_document_latest_version(
    session: AsyncSession,
    document_id,
    limit_clauses: int | None = None,
    only_missing: bool = True,
) -> list[ClauseTranslation]:
    """对指定文档最新已解析版本的条款批量执行受术语约束翻译。"""
    doc = await session.get(Document, document_id)
    if doc is None:
        return []
    from app.ingestion.safety import document_is_held

    if await document_is_held(session, doc.id):
        return []
    ver = (
        await session.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == doc.id, DocumentVersion.state == "parsed")
            .order_by(DocumentVersion.version_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if ver is None:
        return []

    terms = list(
        (
            await session.execute(
                select(GlossaryTerm).where(
                    or_(
                        GlossaryTerm.country_code == doc.country_code,
                        GlossaryTerm.country_code.is_(None),
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    clauses = list(
        (
            await session.execute(
                select(Clause).where(Clause.version_id == ver.id).order_by(Clause.ordinal)
            )
        )
        .scalars()
        .all()
    )
    from app.ingestion.models import ClauseReading
    from app.ingestion.noise import clean_text

    out: list[ClauseTranslation] = []
    for c in clauses:
        from app.knowledge.legal_terms import translation_terms

        rd_txt = await session.scalar(
            select(ClauseReading.reading_text).where(ClauseReading.clause_id == c.id)
        )
        c_src = unwrap_layout_lines(clean_text(rd_txt or c.original_text))
        effective = select_effective_terms(
            await translation_terms(session, c, doc.country_code, terms),
            doc.country_code,
            c.language,
        )
        required_hits, _ = match_clause_glossary(c_src, effective)
        current = (
            await session.scalars(
                select(ClauseTranslation).where(
                    ClauseTranslation.clause_id == c.id,
                    ClauseTranslation.is_current.is_(True),
                    ClauseTranslation.review_status.in_(
                        ("machine_translated", "pending_review", "approved")
                    ),
                )
            )
        ).all()
        if only_missing and any(
            row.review_status == "approved"
            or (
                row.quality_check_passed
                and verify_translation(c_src, row.translated_text, required_hits)[0]
            )
            for row in current
        ):
            continue
        if limit_clauses is not None and len(out) >= limit_clauses:
            break
        out.append(await translate_clause(session, c, doc.country_code, terms))
        await session.commit()  # Resume skips completed clauses after a later failure.
    return out
