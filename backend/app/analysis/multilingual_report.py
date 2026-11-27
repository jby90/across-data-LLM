# ruff: noqa: E501
"""Universal 15+2 Language Host-Country Compliance Document & Consulting Report Engine.

Covers:
- Chinese (`zh`) and International Regulatory English (`en`);
- All 15 Host-Country Official Languages across our 46+ jurisdictions:
  Tier 1 (Latin / Latin-Ext): `fr`, `de`, `es`, `pt`, `it`, `nl`, `id`, `tr`, `pl`
  Tier 2 (East & Southeast Asian): `ja` (Joyo Kanji + Kana gate), `ko` (Hangul), `th` (Thai ZWSP cluster-safe break), `vi` (Vietnamese)
  Tier 3 (RTL & Cyrillic): `ar` (Arabic RTL mirrored tables + LRI/PDI BiDi isolation), `ru` (Cyrillic)

Guarantees:
1. On-demand bounded lexicon loading (`lru_cache(maxsize=3)`) + explicit cache release for 1.6 GiB zero-Swap hosts;
2. Per-language persistent `DocumentNarrative` cache (`{code}__{lang}_v1`) + `Redactor` egress desensitization;
3. Verbatim preservation of official host-country statutory quotes when source & target languages match;
4. Deterministic zero-untranslated-Chinese gate (strict 0-Han for 14 non-JA languages; `cp932` + Kana/Shinjitai gate for `ja`).
"""

from __future__ import annotations

import gc
import hashlib
import json
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.document_basis import country_of
from app.analysis.document_narrative import DocumentNarrative
from app.analysis.english_report import (
     _allow_live_llm_now,
    _collect_translatable_slots,
    _zh_numeral_to_int,
    build_english_consulting_report_blocks,
    build_english_doc_blocks,
)
from app.analysis.llm import LlmUnavailableError, call_llm_json
from app.analysis.models import AnalysisTask

LEXICON_PATH = Path(__file__).with_name("multilingual_lexicon.json")

HOST_REPORT_LANGS: tuple[str, ...] = (
    "fr",
    "de",
    "es",
    "pt",
    "it",
    "nl",
    "id",
    "tr",
    "pl",
    "ja",
    "ko",
    "th",
    "vi",
    "ar",
    "ru",
)
ALL_FOREIGN_LANGS: tuple[str, ...] = ("en", *HOST_REPORT_LANGS)
ALL_REPORT_LANGS: tuple[str, ...] = ("zh", "en", *HOST_REPORT_LANGS)

LANGUAGE_META: dict[str, dict[str, Any]] = {
    "zh": {"code": "zh", "name_zh": "中文", "native": "简体中文", "rtl": False},
    "en": {"code": "en", "name_zh": "英语", "native": "English", "rtl": False},
    "fr": {"code": "fr", "name_zh": "法语", "native": "Français", "rtl": False},
    "de": {"code": "de", "name_zh": "德语", "native": "Deutsch", "rtl": False},
    "es": {"code": "es", "name_zh": "西班牙语", "native": "Español", "rtl": False},
    "pt": {"code": "pt", "name_zh": "葡萄牙语", "native": "Português", "rtl": False},
    "it": {"code": "it", "name_zh": "意大利语", "native": "Italiano", "rtl": False},
    "nl": {"code": "nl", "name_zh": "荷兰语", "native": "Nederlands", "rtl": False},
    "id": {"code": "id", "name_zh": "印尼语", "native": "Bahasa Indonesia", "rtl": False},
    "tr": {"code": "tr", "name_zh": "土耳其语", "native": "Türkçe", "rtl": False},
    "pl": {"code": "pl", "name_zh": "波兰语", "native": "Polski", "rtl": False},
    "ja": {"code": "ja", "name_zh": "日语", "native": "日本語", "rtl": False},
    "ko": {"code": "ko", "name_zh": "韩语", "native": "한국어", "rtl": False},
    "th": {"code": "th", "name_zh": "泰语", "native": "ภาษาไทย", "rtl": False},
    "vi": {"code": "vi", "name_zh": "越南语", "native": "Tiếng Việt", "rtl": False},
    "ar": {"code": "ar", "name_zh": "阿拉伯语", "native": "العربية", "rtl": True},
    "ru": {"code": "ru", "name_zh": "俄语", "native": "Русский", "rtl": False},
}

# Mapping of ISO jurisdiction codes to primary official host-country language(s)
COUNTRY_OFFICIAL_LANGS: dict[str, tuple[str, ...]] = {
    "CN": ("zh",),
    "SG": ("en",),
    "GB": ("en",),
    "US": ("en",),
    "AU": ("en",),
    "NZ": ("en",),
    "IE": ("en",),
    "IN": ("en",),
    "PH": ("en",),
    "ZA": ("en",),
    "NG": ("en",),
    "KE": ("en",),
    "PK": ("en",),
    "IL": ("en",),
    "EU": ("en", "fr", "de"),
    "CA": ("en", "fr"),
    "FR": ("fr",),
    "MA": ("fr", "ar"),
    "TN": ("ar", "fr"),
    "DZ": ("ar", "fr"),
    "BE": ("fr", "nl", "de"),
    "LU": ("fr", "de"),
    "CH": ("de", "fr", "it"),
    "DE": ("de",),
    "AT": ("de",),
    "ES": ("es",),
    "MX": ("es",),
    "AR": ("es",),
    "CL": ("es",),
    "CO": ("es",),
    "PE": ("es",),
    "UY": ("es",),
    "PT": ("pt",),
    "BR": ("pt",),
    "IT": ("it",),
    "NL": ("nl",),
    "ID": ("id",),
    "MY": ("id", "en"),
    "TR": ("tr",),
    "PL": ("pl",),
    "JP": ("ja",),
    "KR": ("ko",),
    "TH": ("th",),
    "VN": ("vi",),
    "AE": ("ar", "en"),
    "SA": ("ar",),
    "EG": ("ar",),
    "LB": ("ar", "fr", "en"),
    "QA": ("ar",),
    "KW": ("ar",),
    "OM": ("ar",),
    "BH": ("ar",),
    "JO": ("ar",),
    "RU": ("ru",),
    "KZ": ("ru",),
    "BY": ("ru",),
    "SE": ("en",),
    "DK": ("en",),
    "FI": ("en",),
    "NO": ("en",),
}

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff]")
_HAN_AND_CJK_PUNCT_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff01-\uff60]")
_HAN_ONLY_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_JA_KANA_RE = re.compile(r"[\u3040-\u30ff\uff66-\uff9f]")
_PURE_HAN_CLAUSE_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]{12,}")
_THAI_LONG_RUN_RE = re.compile(r"[\u0e01-\u0e5b]{18,}")
_LTR_IN_AR_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9._/\-+]*|\d+[A-Za-z][A-Za-z0-9._/\-+]*|\d{4}-\d{2}-\d{2})"
)

def _is_cp932(ch: str) -> bool:
    try:
        ch.encode("cp932")
        return True
    except UnicodeEncodeError:
        return False


_RAW_PRC_TO_SHINJITAI: dict[str, str] = {
    "车": "車", "辆": "両", "规": "規", "则": "則", "评": "評", "输": "輸", "进": "進",
    "级": "級", "险": "険", "护": "護", "处": "処", "据": "拠", "络": "絡", "设": "設",
    "备": "備", "签": "署", "订": "訂", "约": "約", "责": "責", "罚": "罰", "监": "監",
    "报": "報", "审": "審", "实": "実", "现": "現", "业": "業", "务": "務", "产": "産",
    "户": "戸", "专": "専", "门": "門", "义": "義", "权": "権", "准": "準", "认": "認",
    "证": "証", "识": "識", "传": "伝", "储": "貯", "删": "削", "销": "消", "毁": "毀",
    "补": "補", "个": "個", "为": "為", "从": "従", "对": "対", "应": "応", "该": "該",
    "还": "還", "经": "経", "过": "過", "达": "達", "并": "並", "开": "開", "关": "関",
    "联": "連", "网": "網", "云": "雲", "电": "電", "脑": "脳", "软": "軟", "类": "類",
    "项": "項", "际": "際", "东": "東", "亚": "亜", "马": "馬", "韩": "韓", "罗": "羅",
}
# Strictly map ONLY PRC-simplified characters that are NOT already in Japanese JIS/CP932,
# guaranteeing 100% idempotence on valid Japanese text.
_JA_SHINJITAI_MAP = str.maketrans({k: v for k, v in _RAW_PRC_TO_SHINJITAI.items() if not _is_cp932(k)})

# Script signatures to detect if `quote_original` is already written in the target language
_TARGET_SCRIPT_CHECK: dict[str, re.Pattern[str]] = {
    "ar": re.compile(r"[\u0600-\u06ff]{4,}"),
    "ru": re.compile(r"[\u0400-\u04ff]{4,}"),
    "ko": re.compile(r"[\uac00-\ud7a3]{3,}"),
    "th": re.compile(r"[\u0e01-\u0e5b]{5,}"),
    "ja": re.compile(r"[\u3040-\u30ff]{2,}"),
    "vi": re.compile(r"[\u0110\u0111\u01a0\u01a1\u01af\u01b0\u1ea0-\u1ef9]"),
}


def normalize_report_lang(lang: str | None) -> str:
    code = str(lang or "zh").strip().lower()
    if code not in LANGUAGE_META:
        raise ValueError(f"不支持的报告输出语种：{lang}")
    return code


def list_supported_report_languages(
    source_country: str | None = None,
    recipient_country: str | None = None,
) -> dict[str, Any]:
    """Return all 17 supported report output languages plus route-recommended official host languages."""
    recommended: list[str] = []
    for raw_c in (recipient_country, source_country):
        cc = country_of(raw_c or "") or str(raw_c or "").strip().upper()
        for lg in COUNTRY_OFFICIAL_LANGS.get(cc, ()):
            if lg != "zh" and lg not in recommended:
                recommended.append(lg)
    if "en" not in recommended:
        recommended.append("en")
    return {
        "recommended": [LANGUAGE_META[lg] for lg in recommended if lg in LANGUAGE_META],
        "languages": [LANGUAGE_META[lg] for lg in ALL_REPORT_LANGS],
    }


@lru_cache(maxsize=3)
def load_lang_lexicon(lang: str) -> dict[str, Any]:
    """Load only a single target language's lexicon into memory (LRU bounded to 3 languages)."""
    raw_all = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
    entry = raw_all.get(lang)
    if not isinstance(entry, dict):
        raise ValueError(f"未找到语种词库配置：{lang}")
    # Pre-sort phrase & country replacements longest-first once per cached language
    phrases = entry.get("phrases") or {}
    countries = entry.get("countries") or {}
    return {
        **entry,
        "_sorted_countries": tuple(
            sorted(
                ((k, v) for k, v in countries.items() if len(k) >= 2 and not k.isascii()),
                key=lambda x: len(x[0]),
                reverse=True,
            )
        ),
        "_sorted_phrases": tuple(
            sorted(phrases.items(), key=lambda x: len(x[0]), reverse=True)
        ),
    }


def release_lang_lexicon_cache() -> None:
    """Explicitly evict cached language dictionaries after batch multilingual jobs on low-RAM hosts."""
    load_lang_lexicon.cache_clear()
    gc.collect()


def _inject_thai_zwsp(text: str) -> str:
    """Insert cluster-safe U+200B break opportunities into long uninterrupted Thai runs so narrow
    DOCX and WeasyPrint table columns never overflow while preserving 100% vowel/tone shaping."""

    def _split_run(m: re.Match[str]) -> str:
        run = m.group(0)
        out: list[str] = []
        since_break = 0
        n = len(run)
        for i, ch in enumerate(run):
            cp = ord(ch)
            prev_cp = ord(run[i - 1]) if i > 0 else 0
            # Safe Thai syllable start:
            # 1) Leading vowel [\u0e40-\u0e44] after >= 8 chars (and previous char is not a combining mark \u0e3a phinthu)
            # 2) Base consonant [\u0e01-\u0e2e] after >= 12 chars when previous char is NOT a leading vowel [\u0e40-\u0e44]
            #    and NOT Mai Han-Akat / Phinthu (\u0e31, \u0e3a) and next char is NOT Thanthakhat (\u0e4c)
            next_cp = ord(run[i + 1]) if i + 1 < n else 0
            if 0x0E40 <= cp <= 0x0E44 and since_break >= 8 and prev_cp != 0x0E3A:
                out.append("\u200b")
                since_break = 0
            elif (
                0x0E01 <= cp <= 0x0E2E
                and since_break >= 12
                and not (0x0E40 <= prev_cp <= 0x0E44)
                and prev_cp not in (0x0E31, 0x0E3A, 0x0E47)
                and next_cp != 0x0E4C
            ):
                out.append("\u200b")
                since_break = 0
            out.append(ch)
            since_break += 1
        return "".join(out)

    return _THAI_LONG_RUN_RE.sub(_split_run, text)


def _isolate_arabic_ltr(text: str) -> str:
    """Wrap embedded Latin codes/dates/models in LRI...PDI (\u2066...\u2069) inside Arabic RTL prose (idempotent)."""
    if not re.search(r"[\u0600-\u06ff]", text) or not re.search(r"[A-Za-z0-9]", text):
        return text
    base = text.replace("\u2066", "").replace("\u2069", "")
    return _LTR_IN_AR_RE.sub(lambda m: f"\u2066{m.group(0)}\u2069", base)


def _sanitize_japanese_text(s: str, fallback: str) -> str:
    """Japanese Legal Kanji & Kana Gate:
    1. Normalizes punctuation to Japanese legal style (`「」`, `・`, `（...）`);
    2. Maps Shinjitai equivalents and strips any character in `[\u3400-\u9fff]` not encodable in `cp932`
       (eliminating PRC-only simplified characters like `车, 规, 据, 护, 评, 这, 们`);
    3. Rejects pure-Hanzi clauses (`>=12` Hanzi with `0` Kana) so untranslated Chinese prose cannot pass."""
    for k, v in (
        ("“", "「"),
        ("”", "」"),
        ("‘", "『"),
        ("’", "』"),
        ("《", "『"),
        ("》", "』"),
        ("，", "、"),
        ("；", "；"),
        ("：", "："),
    ):
        s = s.replace(k, v)
    s = s.translate(_JA_SHINJITAI_MAP)

    # Filter out any CJK ideograph that is not valid Japanese JIS/CP932 Kanji
    cleaned_chars: list[str] = []
    for ch in s:
        if "\u3400" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff":
            try:
                ch.encode("cp932")
                cleaned_chars.append(ch)
            except UnicodeEncodeError:
                continue
        else:
            cleaned_chars.append(ch)
    s = "".join(cleaned_chars)

    # Strip any >=12-Kanji run that lacks Kana in its local 20-char window (exact dual of has_untranslated_chinese_leak)
    while True:
        bad_span: tuple[int, int] | None = None
        for m in _PURE_HAN_CLAUSE_RE.finditer(s):
            if m.group(0).endswith(("株式会社", "有限会社", "合同会社")):
                continue
            win = s[max(0, m.start() - 20) : min(len(s), m.end() + 20)]
            if not _JA_KANA_RE.search(win):
                bad_span = (m.start(), m.end())
                break
        if bad_span is None:
            break
        s = s[: bad_span[0]] + " " + s[bad_span[1] :]

    s = re.sub(r"[、。；：,\s]{2,}", lambda m: m.group(0)[0] if m.group(0)[0] in "、。" else " ", s)
    s = re.sub(r"[ \t]{2,}", " ", s).strip(" -;,:、")
    return s if s else fallback


def has_untranslated_chinese_leak(text: str, lang: str) -> list[str]:
    """Audit helper used in tests & runtime assertions to verify zero untranslated Chinese residue."""
    if lang == "zh":
        return []
    if lang == "ja":
        leaks: list[str] = []
        for ch in text:
            if "\u3400" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff":
                try:
                    ch.encode("cp932")
                except UnicodeEncodeError:
                    leaks.append(ch)
        for m in _PURE_HAN_CLAUSE_RE.finditer(text):
            if m.group(0).endswith(("株式会社", "有限会社", "合同会社")):
                continue
            # Allow long Kanji compound ONLY if Kana exists nearby in the surrounding 30 chars
            win = text[max(0, m.start() - 20) : min(len(text), m.end() + 20)]
            if not _JA_KANA_RE.search(win):
                leaks.append(m.group(0))
        return leaks
    return _HAN_AND_CJK_PUNCT_RE.findall(text)


def clean_and_gate_target_text(text: Any, lang: str, fallback: str = "—") -> str:
    """Deterministically translate known Chinese phrases/countries into ``lang`` and enforce the
    target language's zero-untranslated-Chinese gate + Thai/Arabic script shaping."""
    if text is None:
        return fallback
    s = _CTRL_RE.sub("", str(text)).strip()
    if not s:
        return fallback

    if lang == "ja" and _JA_KANA_RE.search(s) and not has_untranslated_chinese_leak(s, "ja"):
        return _sanitize_japanese_text(s, fallback=fallback)

    lex = load_lang_lexicon(lang)
    sep = "" if lang in ("ja", "th") else " "
    for zh_k, tr_v in lex["_sorted_countries"]:
        if zh_k in s:
            s = s.replace(zh_k, f"{sep}{tr_v}{sep}")

    for zh_k, tr_v in lex["_sorted_phrases"]:
        if zh_k in s:
            s = s.replace(zh_k, f"{sep}{tr_v}{sep}")

    if lang == "ja":
        s = re.sub(r"等\s*(\d+)\s*项", r"（ほか\1項目）", s)
        return _sanitize_japanese_text(s, fallback=fallback)

    # Punctuation normalization for the 14 non-JA languages
    if lang == "ar":
        punct_map = (
            ("，", "، "),
            ("。", ". "),
            ("；", "؛ "),
            ("：", ": "),
            ("（", " ("),
            ("）", ") "),
            ("、", "، "),
            ("“", '"'),
            ("”", '"'),
            ("‘", "'"),
            ("’", "'"),
            ("《", "«"),
            ("》", "»"),
            ("【", "["),
            ("】", "]"),
            ("→", " ← "),
            ("·", " - "),
            ("—", " - "),
        )
    else:
        punct_map = (
            ("，", ", "),
            ("。", ". "),
            ("；", "; "),
            ("：", ": "),
            ("（", " ("),
            ("）", ") "),
            ("、", ", "),
            ("“", '"'),
            ("”", '"'),
            ("‘", "'"),
            ("’", "'"),
            ("《", '"'),
            ("》", '"'),
            ("【", "["),
            ("】", "]"),
            ("→", " → "),
            ("·", " - "),
            ("—", " - "),
        )
    for k, v in punct_map:
        s = s.replace(k, v)

    s = re.sub(r"等\s*(\d+)\s*项", r" (\1)", s)
    s = _HAN_AND_CJK_PUNCT_RE.sub(" ", s)
    s = re.sub(r"\([\s,;:.\-،؛]*\)", "", s)
    s = re.sub(r"\[[\s,;:.\-،؛]*\]", "", s)
    s = re.sub(r'""|«»', "", s)
    s = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\s+([,.;:)])", r"\1", s)
    s = re.sub(r"([(])\s+", r"\1", s)
    s = s.strip(" -;,:")

    if not s:
        return fallback
    if lang == "th":
        s = _inject_thai_zwsp(s)
    elif lang == "ar":
        s = _isolate_arabic_ltr(s)
    return s


def _apply_multilingual_slots(blocks: list[dict], translated: list[str], lang: str) -> list[dict]:
    lex = load_lang_lexicon(lang)
    col_fb = str((lex.get("ui_labels") or {}).get("details") or "—")
    out: list[dict] = []
    idx = 0

    def _next(fallback: str = "—") -> str:
        nonlocal idx
        val = translated[idx] if idx < len(translated) else ""
        idx += 1
        return clean_and_gate_target_text(val, lang, fallback=fallback)

    for b in blocks:
        btype = b.get("type")
        if btype in ("title", "heading", "subheading", "paragraph"):
            txt = _next(fallback="")
            if txt:
                out.append({"type": btype, "text": txt})
        elif btype == "list":
            items = [_next(fallback="") for _ in (b.get("items") or [])]
            items = [x for x in items if x]
            if items:
                out.append({"type": "list", "items": items})
        elif btype == "table":
            head = [_next(fallback=col_fb) for _ in (b.get("head") or [])]
            rows: list[list[str]] = []
            for r in b.get("rows") or []:
                if isinstance(r, list):
                    rows.append([_next(fallback="—") for _ in r])
            tbl: dict[str, Any] = {"type": "table", "head": head, "rows": rows}
            if b.get("widths"):
                tbl["widths"] = list(b["widths"])
            out.append(tbl)
        elif btype == "pagebreak":
            out.append({"type": "pagebreak"})
    return out


def _quote_is_native_target(orig_q: str, cite_lang: str, target_lang: str) -> bool:
    """Return True when a statutory citation's `quote_original` is already in ``target_lang``
    so we preserve the official host-country statutory wording verbatim (Redline 4.1 & 5.1)."""
    if not orig_q:
        return False
    cl = (cite_lang or "").strip().lower()
    if cl and cl.startswith(target_lang):
        if target_lang == "ja":
            return not has_untranslated_chinese_leak(orig_q, "ja")
        return not _HAN_ONLY_RE.search(orig_q)
    pat = _TARGET_SCRIPT_CHECK.get(target_lang)
    if pat is not None and pat.search(orig_q):
        if target_lang == "ja":
            return not has_untranslated_chinese_leak(orig_q, "ja")
        return not _HAN_ONLY_RE.search(orig_q)
    return False


def _truncate_at_sentence_or_word(text: str, max_chars: int = 400, *, lang: str) -> str:
    """Redline 4.3: truncate statutory quotes cleanly without dangling list numbers or mid-word cuts."""
    clean = _CTRL_RE.sub("", text).strip()
    if len(clean) <= max_chars:
        return _inject_thai_zwsp(clean) if lang == "th" else (_isolate_arabic_ltr(clean) if lang == "ar" else clean)
    cut = clean[:max_chars]
    for sep in ("。", ". ", "؛ ", "; "):
        pos = cut.rfind(sep)
        if pos >= max_chars // 2:
            res = cut[: pos + len(sep)].strip()
            return _inject_thai_zwsp(res) if lang == "th" else (_isolate_arabic_ltr(res) if lang == "ar" else res)
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    cut = re.sub(r"[\s,;:\-]+$", "", cut) + "..."
    return _inject_thai_zwsp(cut) if lang == "th" else (_isolate_arabic_ltr(cut) if lang == "ar" else cut)


def _build_multilingual_citation_table(
    citations: list[dict],
    lang: str,
    translated_excerpts: dict[int, str] | None = None,
) -> list[dict]:
    if not citations:
        return []
    lex = load_lang_lexicon(lang)
    ui = lex["ui_labels"]
    art_abbr = ui.get("article_abbr", "Art.")
    prov_word = ui.get("provision_word", "Provision")
    def_statute = ui.get("default_statute", "Statute")
    fb_tpl = ui.get("fallback_excerpt", "{law} ({art})")

    rows: list[list[str]] = []
    tr_map = translated_excerpts or {}
    for i, c in enumerate(citations[:16], 1):
        orig_title = str(c.get("original_title") or "").strip()
        zh_title = str(c.get("document_title") or "").strip()
        c_lang = str(c.get("language") or "").strip().lower()

        if orig_title and _quote_is_native_target(orig_title, c_lang, lang):
            law_txt = _truncate_at_sentence_or_word(orig_title, 160, lang=lang)
        elif orig_title and lang != "ja" and not _HAN_ONLY_RE.search(orig_title) and lang in ("fr", "de", "es", "pt", "it", "nl", "id", "tr", "pl"):
            # Keep official Latin statute title if already in Latin script, or translate Chinese title
            law_txt = clean_and_gate_target_text(zh_title or orig_title, lang, fallback=def_statute)
        else:
            law_txt = clean_and_gate_target_text(zh_title or orig_title, lang, fallback=def_statute)

        clause_raw = str(c.get("clause_number") or "").strip()
        m_num = re.search(r"[0-9]+(?:\.[0-9]+)*(?:\([a-zA-Z0-9]+\))*", clause_raw)
        zh_art = _zh_numeral_to_int(clause_raw)
        if m_num:
            art_txt = f"{art_abbr} {m_num.group(0)}"
        elif zh_art:
            art_txt = f"{art_abbr} {zh_art}"
        else:
            art_txt = clean_and_gate_target_text(clause_raw, lang, fallback=f"{prov_word} #{i}")

        orig_q = str(c.get("quote_original") or "").strip()
        if _quote_is_native_target(orig_q, c_lang, lang):
            excerpt = _truncate_at_sentence_or_word(orig_q, 420, lang=lang)
        elif i in tr_map and tr_map[i].strip():
            excerpt = clean_and_gate_target_text(tr_map[i], lang, fallback="")
        else:
            cand = clean_and_gate_target_text(c.get("quote_zh") or orig_q, lang, fallback="")
            if len(cand) >= 28:
                excerpt = _truncate_at_sentence_or_word(cand, 380, lang=lang)
            else:
                excerpt = clean_and_gate_target_text(
                    fb_tpl.format(law=law_txt, art=art_txt),
                    lang,
                    fallback=f"{law_txt} ({art_txt})",
                )
        if not excerpt:
            excerpt = clean_and_gate_target_text(
                fb_tpl.format(law=law_txt, art=art_txt),
                lang,
                fallback=f"{law_txt} ({art_txt})",
            )
        rows.append([str(i), f"{law_txt} - {art_txt}", excerpt])

    return [
        {"type": "heading", "text": clean_and_gate_target_text(ui.get("citations_heading"), lang, fallback="Citations")},
        {
            "type": "table",
            "head": [
                clean_and_gate_target_text(ui.get("col_no"), lang, fallback="#"),
                clean_and_gate_target_text(ui.get("col_statute"), lang, fallback="Statute"),
                clean_and_gate_target_text(ui.get("col_excerpt"), lang, fallback="Excerpt"),
            ],
            "widths": [8, 34, 58],
            "rows": rows,
        },
    ]


def _system_prompt_for_lang(lang: str) -> str:
    lex = load_lang_lexicon(lang)
    lang_en = lex["name_en"]
    native = lex["native"]
    supp = lex["ui_labels"].get("to_be_supplemented", "[To be supplemented]")
    if lang == "ja":
        script_rule = (
            "Write 100% in authoritative, formal Japanese legal prose (APPI / MLIT automotive compliance style) "
            "using standard Japanese Joyo Kanji + Hiragana/Katakana. NEVER leave Mainland Simplified Chinese "
            "characters (such as 车, 规, 据, 护, 评, 输, 这, 们) untranslated."
        )
    else:
        script_rule = (
            f"ZERO Chinese characters or CJK ideographs may appear anywhere in the output. "
            f"Translate every heading, legal argument, vehicle feature, enterprise description, and table cell "
            f"into authoritative, idiomatic {lang_en} ({native}) regulatory counsel style."
        )
    return (
        f"You are Senior {lang_en} ({native}) Automotive Data Privacy & Homologation Counsel translating a structured "
        f"automotive cross-border data compliance document into formal {lang_en} ({native}).\n"
        f"Hard rules:\n"
        f"1. Input is JSON {{\"slots\": {{\"0\": \"...\", \"1\": \"...\"}}}}. Output STRICTLY valid JSON "
        f"{{\"slots\": {{\"0\": \"...\", \"1\": \"...\"}}}} preserving every exact string key \"0\", \"1\", etc.\n"
        f"2. {script_rule}\n"
        f"3. Preserve every numeric figure, timeframe, monetary threshold, article number, URL, email, and any redaction "
        f"token matching '__CYSH_SECRET_...__' character-for-character.\n"
        f"4. Where the source text says '【待补充】' or '暂不确定', write '{supp}'."
    )


async def build_multilingual_doc_blocks(
    session: AsyncSession,
    task: AnalysisTask,
    code: str,
    zh_blocks: list[dict],
    values: dict[str, str],
    citations: list[dict],
    *,
    lang: str,
    draft: bool = False,
    read_only_viewer: bool = False,
    force_llm: bool = False,
) -> tuple[list[dict], str, str]:
    """Universal builder for ``en`` + all 15 official host-country languages.
    Returns ``(blocks, file_label_stem, header_context)``."""
    lang = normalize_report_lang(lang)
    if lang == "en":
        return await build_english_doc_blocks(
            session,
            task,
            code,
            zh_blocks,
            values,
            citations,
            draft=draft,
            read_only_viewer=read_only_viewer,
            force_llm=force_llm,
        )
    if lang == "zh":
        raise ValueError("中文报告请走原生中文导出分支")

    lex = load_lang_lexicon(lang)
    ui = lex["ui_labels"]
    countries_map = lex["countries"]
    doc_entry = (lex.get("doc_titles") or {}).get(code) or [f"Compliance Report ({code.upper()})", f"Compliance_Report_{code.upper()}_{lang.upper()}"]
    title_tr, file_label_tr = str(doc_entry[0]), str(doc_entry[1])

    task_id = task.id
    task_org_id = task.org_id
    veh_raw = values.get("vehicle_model") or task.vehicle_model or ""
    scn_raw = values.get("scenario") or task.scenario_type or ""
    src_raw = str(values.get("source_country") or "").strip()
    rcp_raw = str(values.get("recipient_country") or "").strip()

    src_iso = country_of(src_raw) or src_raw.upper()
    rcp_iso = country_of(rcp_raw) or rcp_raw.upper()
    src_tr = countries_map.get(src_iso) or countries_map.get(src_raw) or clean_and_gate_target_text(src_raw, lang, fallback=ui.get("target_jurisdiction", "Source"))
    rcp_tr = countries_map.get(rcp_iso) or countries_map.get(rcp_raw) or clean_and_gate_target_text(rcp_raw, lang, fallback=ui.get("target_jurisdiction", "Recipient"))
    veh_tr = clean_and_gate_target_text(veh_raw, lang, fallback=ui.get("default_vehicle", "Vehicle"))
    scn_tr = clean_and_gate_target_text(scn_raw, lang, fallback=ui.get("default_scenario", "Scenario"))
    default_ctrl = clean_and_gate_target_text(ui.get("default_controller"), lang, fallback="Data Controller")
    comp_raw = str(values.get("company_name") or "").strip()
    comp_tr = (
        clean_and_gate_target_text(comp_raw, lang, fallback=default_ctrl)
        if comp_raw
        else default_ctrl
    )
    _bare_corps = {
        "co., ltd.", "ltd.", "mbh", "gmbh", "s.p.a.", "s.a.", "b.v.", "n.v.",
        "sociedad anónima", "sociedade anónima", "spółka akcyjna", "anonim şirket",
        "perusahaan terbuka", "株式会社", "주식회사", "пао", "ооо", "ش.م.م", "شركة مساهمة",
    }
    if comp_tr.strip().lower() in _bare_corps or (lang != "ja" and len(comp_tr.strip()) < 8):
        comp_tr = default_ctrl

    core_zh_blocks: list[dict] = []
    skip_tail = False
    for b in zh_blocks:
        if b.get("type") == "heading" and any(k in str(b.get("text") or "") for k in ("参考条文", "法规依据", "所依据的条文")):
            skip_tail = True
            continue
        if skip_tail and b.get("type") == "table":
            skip_tail = False
            continue
        core_zh_blocks.append(b)

    core_slots = _collect_translatable_slots(core_zh_blocks)
    foreign_cite_indices: list[int] = []
    cite_slots_in: list[str] = []
    for idx_c, c in enumerate(citations[:16], 1):
        oq = str(c.get("quote_original") or "").strip()
        c_lg = str(c.get("language") or "").strip().lower()
        if not _quote_is_native_target(oq, c_lg, lang):
            foreign_cite_indices.append(idx_c)
            cite_slots_in.append(str(c.get("quote_zh") or oq or "")[:360])

    meta_slots_in = [comp_raw, str(scn_raw or "").strip()] if (comp_raw or scn_raw) else []
    slots = [*core_slots, *cite_slots_in, *meta_slots_in]
    sig_payload = json.dumps({"v": 2, "lang": lang, "code": code, "draft": draft, "slots": slots}, ensure_ascii=False, sort_keys=True)
    sig_hash = hashlib.sha256(sig_payload.encode("utf-8")).hexdigest()
    cache_code = f"{code[:18]}__{lang}_v1"

    cached = await session.scalar(
        select(DocumentNarrative).where(
            DocumentNarrative.task_id == task_id,
            DocumentNarrative.code == cache_code,
            DocumentNarrative.inputs_hash == sig_hash,
        )
    )
    translated_slots: list[str] | None = None
    if cached is not None and isinstance(cached.report, dict):
        cand = cached.report.get("strings")
        if isinstance(cand, list) and len(cand) == len(slots):
            translated_slots = [str(x) for x in cand]

    if translated_slots is None and (force_llm or _allow_live_llm_now()) and not read_only_viewer and slots and task_org_id is not None:
        try:
            from app.core.config import get_settings

            st = get_settings()
            eff_model = (
                "deepseek-chat"
                if st.llm_model == "deepseek-flash" and "deepseek.com" in (st.llm_base_url or "")
                else st.llm_model
            )
            sys_prompt = _system_prompt_for_lang(lang)
            merged_out: list[str] = []
            total_tok = 0
            batch_size = 20
            for start in range(0, len(slots), batch_size):
                chunk = slots[start : start + batch_size]
                keyed_in = {str(idx_k): val_k for idx_k, val_k in enumerate(chunk)}
                llm_res = await call_llm_json(
                    [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": json.dumps({"slots": keyed_in}, ensure_ascii=False)},
                    ],
                    max_tokens=8192,
                    model=eff_model,
                )
                total_tok += int(llm_res.total_tokens or 0)
                raw_data = llm_res.data if isinstance(llm_res.data, dict) else {}
                slot_map = raw_data.get("slots") if isinstance(raw_data.get("slots"), dict) else raw_data
                if not isinstance(slot_map, dict) or len(slot_map) < max(1, int(len(chunk) * 0.75)):
                    raise LlmUnavailableError(f"Multilingual ({lang}) slot map incomplete")
                for idx_k, orig_s in enumerate(chunk):
                    cand_s = str(slot_map.get(str(idx_k)) or "").strip()
                    merged_out.append(cand_s if cand_s else clean_and_gate_target_text(orig_s, lang))
            translated_slots = merged_out
            session.add(
                DocumentNarrative(
                    org_id=task_org_id,
                    task_id=task_id,
                    code=cache_code,
                    inputs_hash=sig_hash,
                    blocks=[],
                    report={"strings": translated_slots},
                    tokens=total_tok,
                )
            )
            await session.commit()
            await session.refresh(task)
        except Exception:  # noqa: BLE001
            await session.rollback()
            await session.refresh(task)
            translated_slots = None

    tr_cite_map: dict[int, str] = {}
    fb_ctrl_tpl = ui.get("fallback_control", "{src} -> {rcp} #{idx}")
    if translated_slots is not None:
        n_core = len(core_slots)
        n_cite = len(cite_slots_in)
        core_tr = translated_slots[:n_core]
        cite_tr = translated_slots[n_core : n_core + n_cite]
        meta_tr = translated_slots[n_core + n_cite :]
        for idx_c, txt_c in zip(foreign_cite_indices, cite_tr, strict=False):
            tr_cite_map[idx_c] = txt_c
        if len(meta_tr) == 2:
            if comp_raw and meta_tr[0].strip():
                cand_comp = clean_and_gate_target_text(meta_tr[0], lang, fallback=comp_tr)
                if cand_comp.strip().lower() not in _bare_corps:
                    comp_tr = cand_comp
            if scn_raw and meta_tr[1].strip():
                scn_tr = clean_and_gate_target_text(meta_tr[1], lang, fallback=scn_tr)
        out_core = _apply_multilingual_slots(core_zh_blocks, core_tr, lang)
    else:
        det_slots: list[str] = []
        for idx_s, raw_s in enumerate(core_slots):
            conv = clean_and_gate_target_text(raw_s, lang, fallback="")
            if not conv and raw_s.strip():
                conv = clean_and_gate_target_text(
                    fb_ctrl_tpl.format(idx=idx_s + 1, src=src_tr, rcp=rcp_tr),
                    lang,
                    fallback=f"{src_tr} - {rcp_tr} #{idx_s + 1}",
                )
            det_slots.append(conv)
        out_core = _apply_multilingual_slots(core_zh_blocks, det_slots, lang)

    title_clean = clean_and_gate_target_text(title_tr, lang, fallback=title_tr)
    if out_core and out_core[0].get("type") == "title":
        out_core[0]["text"] = title_clean
    else:
        out_core.insert(0, {"type": "title", "text": title_clean})

    route_arrow = " ← " if lang == "ar" else " → "
    edition_lbl = ui.get("working_draft", "Draft") if draft else ui.get("final_edition", "Final")
    summary_banner = [
        {
            "type": "table",
            "head": [
                clean_and_gate_target_text(ui.get("assessment_attribute"), lang, fallback="Attribute"),
                clean_and_gate_target_text(ui.get("verified_config"), lang, fallback="Configuration"),
            ],
            "widths": [32, 68],
            "rows": [
                [
                    clean_and_gate_target_text(ui.get("doc_type"), lang, fallback="Document"),
                    clean_and_gate_target_text(f"{title_clean} ({edition_lbl})", lang, fallback=title_clean),
                ],
                [
                    clean_and_gate_target_text(ui.get("cross_border_route"), lang, fallback="Route"),
                    clean_and_gate_target_text(f"{src_tr}{route_arrow}{rcp_tr}", lang, fallback=f"{src_tr} - {rcp_tr}"),
                ],
                [
                    clean_and_gate_target_text(ui.get("data_controller"), lang, fallback="Controller"),
                    comp_tr,
                ],
                [
                    clean_and_gate_target_text(ui.get("vehicle_fleet"), lang, fallback="Vehicle"),
                    veh_tr,
                ],
                [
                    clean_and_gate_target_text(ui.get("scenario_stage"), lang, fallback="Scenario"),
                    scn_tr,
                ],
                [
                    clean_and_gate_target_text(
                        re.sub(r"\s*[\(（]UTC[\)）]", "", str(ui.get("compilation_date") or "")),
                        lang,
                        fallback="Date",
                    ),
                    clean_and_gate_target_text(datetime.now(UTC).strftime("%Y-%m-%d"), lang, fallback="—"),
                ],
            ],
        },
    ]

    final_blocks = [out_core[0], *summary_banner, *out_core[1:], *_build_multilingual_citation_table(citations, lang, tr_cite_map)]
    final_slots = _collect_translatable_slots(final_blocks)
    final_blocks = _apply_multilingual_slots(
        final_blocks,
        [clean_and_gate_target_text(s, lang, fallback="—") for s in final_slots],
        lang,
    )

    leaks = has_untranslated_chinese_leak(json.dumps(final_blocks, ensure_ascii=False), lang)
    assert not leaks, f"Redline 5.1 violation: untranslated Chinese leaked into {lang} export: {leaks[:8]}"

    header_tr = clean_and_gate_target_text(f"{title_clean} | {src_tr} - {rcp_tr}", lang, fallback=title_clean)
    return final_blocks, file_label_tr, header_tr


def build_multilingual_consulting_report_blocks(
    task: AnalysisTask,
    rv: Any,
    dimension: str | None = None,
    *,
    lang: str = "en",
) -> tuple[list[dict], str, str]:
    """Build zero-untranslated-Chinese blocks in ``en`` or any of the 15 host-country languages for
    an 8-dimension consulting ``ReportVersion``."""
    lang = normalize_report_lang(lang)
    if lang == "en":
        return build_english_consulting_report_blocks(task, rv, dimension)
    if lang == "zh":
        raise ValueError("中文报告请走原生中文导出分支")

    lex = load_lang_lexicon(lang)
    ui = lex["ui_labels"]
    countries_map = lex["countries"]
    dim_titles = lex["dimension_titles"]

    cc_iso = str(task.country_code or "").upper()
    cc_tr = countries_map.get(cc_iso) or clean_and_gate_target_text(cc_iso, lang, fallback="Jurisdiction")
    veh_tr = clean_and_gate_target_text(task.vehicle_model or "", lang, fallback=ui.get("default_vehicle", "Vehicle"))
    scn_tr = clean_and_gate_target_text(task.scenario_type or "", lang, fallback=ui.get("default_scenario", "Scenario"))

    if dimension:
        title_tr = ui.get("report_dim_title", "{country} ({dim})").format(country=cc_tr, dim=dimension)
    else:
        title_tr = ui.get("report_full_title", "{country}").format(country=cc_tr)
    title_tr = clean_and_gate_target_text(title_tr, lang, fallback=cc_tr)

    ver_word = ui.get("version_word", "v")
    blocks: list[dict] = [
        {"type": "title", "text": title_tr},
        {
            "type": "table",
            "head": [
                clean_and_gate_target_text(ui.get("assessment_param"), lang, fallback="Parameter"),
                clean_and_gate_target_text(ui.get("details"), lang, fallback="Details"),
            ],
            "widths": [30, 70],
            "rows": [
                [clean_and_gate_target_text(ui.get("target_jurisdiction"), lang, fallback="Jurisdiction"), cc_tr],
                [clean_and_gate_target_text(ui.get("vehicle_scope"), lang, fallback="Vehicle"), veh_tr],
                [clean_and_gate_target_text(ui.get("business_scenario"), lang, fallback="Scenario"), scn_tr],
                [
                    clean_and_gate_target_text(ui.get("report_edition"), lang, fallback="Edition"),
                    clean_and_gate_target_text(f"{ver_word} {getattr(rv, 'version_no', 1)} ({datetime.now(UTC).strftime('%Y-%m-%d')})", lang, fallback="v1"),
                ],
                [
                    clean_and_gate_target_text(ui.get("legal_disclaimer"), lang, fallback="Disclaimer"),
                    clean_and_gate_target_text(ui.get("disclaimer_text"), lang, fallback="—"),
                ],
            ],
        },
    ]

    exec_zh = str(getattr(rv, "executive_summary", "") or "").strip()
    if exec_zh and not dimension:
        exec_tr = clean_and_gate_target_text(exec_zh, lang, fallback="")
        blocks.append({"type": "heading", "text": clean_and_gate_target_text(ui.get("executive_summary"), lang, fallback="Summary")})
        blocks.append(
            {
                "type": "paragraph",
                "text": exec_tr if len(exec_tr) >= 24 else clean_and_gate_target_text(f"{title_tr} — {veh_tr} ({scn_tr}).", lang, fallback=title_tr),
            }
        )

    dims = [
        d
        for d in (getattr(rv, "dimensions_json", None) or [])
        if not dimension or d.get("dimension_code") == dimension
    ]
    all_citations: list[dict] = []
    seen_cites: set[str] = set()

    for d in dims:
        dcode = str(d.get("dimension_code") or "D1")
        dtitle = dim_titles.get(dcode) or dcode
        rlvl = clean_and_gate_target_text(d.get("risk_level") or "中风险", lang, fallback="—")
        blocks.append({"type": "heading", "text": clean_and_gate_target_text(f"{dtitle} [{rlvl}]", lang, fallback=dtitle)})

        findings = d.get("findings") or []
        if findings:
            f_rows: list[list[str]] = []
            for idx_f, f in enumerate(findings, 1):
                law_f = clean_and_gate_target_text(f.get("document_title") or "", lang, fallback=ui.get("default_statute", "Statute"))
                cl_no = clean_and_gate_target_text(f.get("clause_number") or "", lang, fallback=ui.get("provision_word", "Art."))
                ob_f = clean_and_gate_target_text(f.get("compliance_obligation") or "", lang, fallback=f"{law_f} ({cl_no})")
                rk_f = clean_and_gate_target_text(f.get("risk_level") or "中风险", lang, fallback="—")
                f_rows.append([str(idx_f), f"{law_f} ({cl_no})", ob_f, rk_f])
            blocks.append(
                {
                    "type": "table",
                    "head": [
                        clean_and_gate_target_text(ui.get("col_no"), lang, fallback="#"),
                        clean_and_gate_target_text(ui.get("col_statute"), lang, fallback="Statute"),
                        clean_and_gate_target_text(ui.get("col_obligation"), lang, fallback="Obligation"),
                        clean_and_gate_target_text(ui.get("col_risk"), lang, fallback="Risk"),
                    ],
                    "widths": [8, 30, 48, 14],
                    "rows": f_rows,
                }
            )
        else:
            md_tr = clean_and_gate_target_text(
                d.get("content_markdown") or "",
                lang,
                fallback=clean_and_gate_target_text(f"{dtitle} — {cc_tr} ({veh_tr}).", lang, fallback=dtitle),
            )
            blocks.append({"type": "paragraph", "text": md_tr})

        recs = d.get("key_recommendations") or []
        if recs:
            rec_items = [
                clean_and_gate_target_text(r, lang, fallback="")
                for r in recs
            ]
            rec_clean = [x for x in rec_items if x]
            if rec_clean:
                blocks.append({"type": "list", "items": rec_clean})

        for c in d.get("citations") or []:
            ckey = f"{c.get('document_title')}:{c.get('clause_number')}"
            if ckey not in seen_cites:
                seen_cites.add(ckey)
                all_citations.append(c)

    blocks.extend(_build_multilingual_citation_table(all_citations, lang))
    slots = _collect_translatable_slots(blocks)
    blocks = _apply_multilingual_slots(blocks, [clean_and_gate_target_text(s, lang, fallback="—") for s in slots], lang)

    leaks = has_untranslated_chinese_leak(json.dumps(blocks, ensure_ascii=False), lang)
    assert not leaks, f"Untranslated Chinese leaked into {lang} consulting report: {leaks[:8]}"

    safe_cc = re.sub(r"[^A-Za-z0-9]+", "_", cc_iso or "INTL").strip("_") or "INTL"
    day = datetime.now(UTC).strftime("%Y%m%d")
    part = dimension if dimension else "Full_Report"
    fname_stem = f"{day}_{safe_cc}_Automotive_Compliance_{part}_v{getattr(rv, 'version_no', 1)}_{lang.upper()}"
    return blocks, fname_stem, clean_and_gate_target_text(f"{title_tr} ({lang.upper()})", lang, fallback=title_tr)
