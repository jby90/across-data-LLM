# ruff: noqa: E501
"""Official-language formal application letter (外文正式稿) for overseas admission filings.

Translates ONLY Part 1 (对外申请文书) of an ``AdmissionDraft`` into the receiving authority's
official submission language, then enforces programmatic reverse verification:
1. Exact block structure and table row/column parity with Part 1;
2. Zero residual Chinese characters or Chinese fullwidth brackets;
3. Character-for-character preservation of all filled numbers, model/registration codes,
   phone numbers, and email addresses;
4. Target-script sanity check (e.g. Arabic script for ``ar``, Latin script for ``en``/``fr``).
"""

from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admission.models import AdmissionDraft, AdmissionEntry
from app.analysis.llm import LlmUnavailableError, call_llm_json
from app.core.redaction import egress_context
from app.ingestion.models import Clause, Document, DocumentVersion

_LANG_BY_CODE: dict[str, tuple[str, str, str]] = {
    "en": ("en", "英文", "English"),
    "fr": ("fr", "法文", "French"),
    "ar": ("ar", "阿拉伯文", "Arabic"),
    "de": ("de", "德文", "German"),
    "ja": ("ja", "日文", "Japanese"),
    "ko": ("ko", "韩文", "Korean"),
    "es": ("es", "西班牙文", "Spanish"),
    "pt": ("pt", "葡萄牙文", "Portuguese"),
    "ru": ("ru", "俄文", "Russian"),
    "it": ("it", "意大利文", "Italian"),
    "nl": ("nl", "荷兰文", "Dutch"),
    "th": ("th", "泰文", "Thai"),
    "vi": ("vi", "越南文", "Vietnamese"),
    "id": ("id", "印尼文", "Indonesian"),
    "ms": ("ms", "马来文", "Malay"),
    "tr": ("tr", "土耳其文", "Turkish"),
}

_LANG_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"英语|英文|\benglish\b", re.I), "en"),
    (re.compile(r"法语|法文|\bfrench\b|\bfrançais\b", re.I), "fr"),
    (re.compile(r"阿拉伯语|阿拉伯文|阿语|阿文|\barabic\b", re.I), "ar"),
    (re.compile(r"德语|德文|\bgerman\b", re.I), "de"),
    (re.compile(r"日语|日文|\bjapanese\b", re.I), "ja"),
    (re.compile(r"韩语|韩文|朝鲜语|\bkorean\b", re.I), "ko"),
    (re.compile(r"西班牙语|西班牙文|\bspanish\b", re.I), "es"),
    (re.compile(r"葡萄牙语|葡萄牙文|\bportuguese\b", re.I), "pt"),
    (re.compile(r"俄语|俄文|\brussian\b", re.I), "ru"),
    (re.compile(r"意大利语|意大利文|\bitalian\b", re.I), "it"),
    (re.compile(r"荷兰语|荷兰文|\bdutch\b", re.I), "nl"),
    (re.compile(r"泰语|泰文|\bthai\b", re.I), "th"),
    (re.compile(r"越南语|越南文|\bvietnamese\b", re.I), "vi"),
    (re.compile(r"印尼语|印尼文|印度尼西亚语|\bindonesian\b", re.I), "id"),
    (re.compile(r"马来语|马来文|\bmalay\b", re.I), "ms"),
    (re.compile(r"土耳其语|土耳其文|\bturkish\b", re.I), "tr"),
]

_PLACEHOLDER_BY_LANG: dict[str, str] = {
    "en": "[Official Foreign-Language Name of Applicant]",
    "fr": "[Dénomination officielle du demandeur]",
    "ar": "[الاسم الرسمي لمقدم الطلب]",
    "de": "[Offizieller Firmenname des Antragstellers]",
    "es": "[Denominación oficial del solicitante]",
    "pt": "[Denominação oficial do requerente]",
}

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_CJK_BRACKET_RE = re.compile(r"[【】《》]")
_JA_CHINESE_LEAK_RE = re.compile(r"待补充|待企业填写|中文工作稿|受理机构要求以|本申请书及随附材料|愿意承担相应法律责任")
_WORKING_NOTICE_RE = re.compile(r"^受理机构要求以.+提交：本稿供企业核对内容")
_WORKING_SUFFIX_RE = re.compile(r"[（(]中文工作稿[^）)]*[）)]")
_EASTERN_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_PLACEHOLDER_CELL_RE = re.compile(r"待补充|待企业填写|^\s*$|[　\s]+年[　\s]*月[　\s]*日")
_EMAIL_TOKEN_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_CODE_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9./_+-]*\d[A-Za-z0-9./_+-]*")


def _match_lang_text(raw: str) -> tuple[str, str, str] | None:
    cleaned = (raw or "").removeprefix("按官方表格：").strip()
    if not cleaned or cleaned in ("待核实", "条文未写明", "不适用"):
        return None
    has_foreign = any(pat.search(cleaned) for pat, _ in _LANG_PATTERNS)
    if re.search(r"中文|汉语", cleaned) and not has_foreign:
        return None
    primary = re.split(r"[；;，,/、]|或|和|及", cleaned)[0].strip()
    for target in (primary, cleaned):
        for pat, code in _LANG_PATTERNS:
            if pat.search(target):
                return _LANG_BY_CODE[code]
    return None


async def resolve_submission_language(
    session: AsyncSession, entry: AdmissionEntry | None, profile_lang: str = ""
) -> tuple[str, str, str] | None:
    """Resolve ``(lang_code, lang_zh, lang_en)`` dynamically from item profile, basis clause,
    or target country's statutory corpus language. Returns ``None`` if Chinese is accepted."""
    if entry is None:
        return None
    raw = profile_lang or str((entry.profile or {}).get("language") or "")
    cleaned = raw.removeprefix("按官方表格：").strip()
    if re.search(r"中文|汉语", cleaned) and not any(pat.search(cleaned) for pat, _ in _LANG_PATTERNS):
        return None
    direct = _match_lang_text(raw)
    if direct is not None:
        return direct

    if entry.evidence_clause_id is not None:
        row = (
            await session.execute(
                select(Clause.language, Document.language)
                .join(DocumentVersion, DocumentVersion.id == Clause.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(Clause.id == entry.evidence_clause_id)
            )
        ).first()
        if row:
            for candidate in row:
                code = str(candidate or "").lower().split("-")[0].strip()
                if code in _LANG_BY_CODE:
                    return _LANG_BY_CODE[code]

    lang_rows = (
        await session.execute(
            select(Document.language, func.count())
            .where(
                Document.country_code == entry.country_code,
                Document.language.is_not(None),
                Document.language != "",
                Document.language != "zh",
            )
            .group_by(Document.language)
            .order_by(func.count().desc())
        )
    ).all()
    for lang_val, _cnt in lang_rows:
        code = str(lang_val or "").lower().split("-")[0].strip()
        if code in _LANG_BY_CODE:
            return _LANG_BY_CODE[code]
    return None


def extract_part1_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract ONLY Part 1 (对外申请文书) from ``AdmissionDraft.blocks``, stripping internal
    working-draft notices and suffixes."""
    out: list[dict[str, Any]] = []
    for blk in blocks or []:
        kind = blk.get("type")
        if kind == "pagebreak":
            break
        if kind == "heading" and "待补充事项" in str(blk.get("text") or ""):
            break
        item = copy.deepcopy(blk)
        if kind == "title":
            item["text"] = _WORKING_SUFFIX_RE.sub("", str(item.get("text") or "")).strip()
        elif kind == "paragraph" and _WORKING_NOTICE_RE.match(str(item.get("text") or "").strip()):
            continue
        out.append(item)
    return out


def _iter_block_strings(blk: dict[str, Any]) -> list[str]:
    kind = blk.get("type")
    if kind in ("title", "heading", "paragraph"):
        return [str(blk.get("text") or "")]
    if kind == "list":
        return [str(x or "") for x in (blk.get("items") or [])]
    if kind == "table":
        texts = [str(h or "") for h in (blk.get("head") or [])]
        for r in blk.get("rows") or []:
            texts.extend(str(c or "") for c in (r or []))
        return texts
    return []


def _replace_in_block(blk: dict[str, Any], old: str, new: str) -> bool:
    changed = False
    kind = blk.get("type")
    if kind in ("title", "heading", "paragraph") and isinstance(blk.get("text"), str):
        if old in blk["text"]:
            blk["text"] = blk["text"].replace(old, new)
            changed = True
    elif kind == "list" and isinstance(blk.get("items"), list):
        for idx, val in enumerate(blk["items"]):
            if isinstance(val, str) and old in val:
                blk["items"][idx] = val.replace(old, new)
                changed = True
    elif kind == "table":
        if isinstance(blk.get("head"), list):
            for idx, val in enumerate(blk["head"]):
                if isinstance(val, str) and old in val:
                    blk["head"][idx] = val.replace(old, new)
                    changed = True
        if isinstance(blk.get("rows"), list):
            for row in blk["rows"]:
                if isinstance(row, list):
                    for idx, val in enumerate(row):
                        if isinstance(val, str) and old in val:
                            row[idx] = val.replace(old, new)
                            changed = True
    return changed


def _extract_factual_tokens(source_blk: dict[str, Any]) -> list[str]:
    """Extract factual numbers, codes, emails, and model identifiers from filled value cells of
    tables so reverse verification guarantees zero alteration or omission."""
    if source_blk.get("type") != "table":
        return []
    head = [str(h or "").strip() for h in (source_blk.get("head") or [])]
    # Skip signature blank table (head == ["", ""]) and enclosed-materials ordinal table
    if head == ["", ""] or (len(head) == 2 and head[0] == "序号"):
        return []
    found: list[str] = []
    for row in source_blk.get("rows") or []:
        if not isinstance(row, list) or len(row) < 2:
            continue
        val = str(row[1] or "").strip()
        if not val or _PLACEHOLDER_CELL_RE.search(val):
            continue
        for m in _EMAIL_TOKEN_RE.finditer(val):
            found.append(m.group(0))
        scrubbed = _EMAIL_TOKEN_RE.sub(" ", val)
        for m in _CODE_TOKEN_RE.finditer(scrubbed):
            tok = m.group(0).strip(".,;:/-")
            if tok and tok not in found:
                found.append(tok)
    return list(dict.fromkeys(found))


def verify_foreign_blocks(
    source_blocks: list[dict[str, Any]],
    out_blocks: list[dict[str, Any]],
    lang_code: str,
) -> list[str]:
    """Programmatic reverse verification of the translated formal application blocks."""
    issues: list[str] = []
    if not isinstance(out_blocks, list) or len(out_blocks) != len(source_blocks):
        return [
            f"段落数量不一致：原稿 {len(source_blocks)} 块，译稿 {len(out_blocks) if isinstance(out_blocks, list) else 0} 块"
        ]

    all_out_texts: list[str] = []
    for idx, (sb, ob) in enumerate(zip(source_blocks, out_blocks, strict=True)):
        if not isinstance(ob, dict) or ob.get("type") != sb.get("type"):
            issues.append(f"第 {idx + 1} 块类型不匹配（应为 {sb.get('type')}）")
            continue
        kind = sb.get("type")
        if kind in ("title", "heading", "paragraph"):
            stxt = str(sb.get("text") or "").strip()
            otxt = ob.get("text")
            if not isinstance(otxt, str) or (stxt and not otxt.strip()):
                issues.append(f"第 {idx + 1} 块正文为空")
        elif kind == "list":
            sitems = sb.get("items") or []
            oitems = ob.get("items")
            if not isinstance(oitems, list) or len(oitems) != len(sitems):
                issues.append(f"第 {idx + 1} 块列表项数量不一致")
            elif any(str(s or "").strip() and not str(o or "").strip() for s, o in zip(sitems, oitems, strict=True)):
                issues.append(f"第 {idx + 1} 块存在空列表项")
        elif kind == "table":
            shead = sb.get("head") or []
            ohead = ob.get("head")
            srows = sb.get("rows") or []
            orows = ob.get("rows")
            if not isinstance(ohead, list) or len(ohead) != len(shead):
                issues.append(f"第 {idx + 1} 块表头列数不一致")
            if not isinstance(orows, list) or len(orows) != len(srows):
                issues.append(f"第 {idx + 1} 块表格行数不一致")
            else:
                for r_i, (sr, orow) in enumerate(zip(srows, orows, strict=True)):
                    if not isinstance(orow, list) or len(orow) != len(sr):
                        issues.append(f"第 {idx + 1} 块第 {r_i + 1} 行列数不一致")
                        break
                    for c_i, (sc, oc) in enumerate(zip(sr, orow, strict=True)):
                        sc_s = str(sc or "").strip()
                        oc_s = str(oc or "").strip()
                        if sc_s and not re.fullmatch(r"[　\s年月日]+", sc_s) and not oc_s:
                            issues.append(f"第 {idx + 1} 块表格第 {r_i + 1} 行第 {c_i + 1} 列漏译为空")
                            break

        blk_strings = _iter_block_strings(ob)
        all_out_texts.extend(blk_strings)
        joined_blk = " ".join(blk_strings)
        norm_blk = joined_blk.translate(_EASTERN_ARABIC_DIGITS)

        if lang_code == "ja":
            m_leak = _JA_CHINESE_LEAK_RE.search(joined_blk)
            if m_leak:
                issues.append(f"第 {idx + 1} 块残留中文工作稿字样“{m_leak.group(0)}”")
        else:
            m_cjk = _CJK_RE.search(joined_blk)
            if m_cjk:
                start = max(0, m_cjk.start() - 4)
                snippet = joined_blk[start : m_cjk.end() + 8]
                issues.append(f"第 {idx + 1} 块残留中文字符“{snippet}”")
            m_brk = _CJK_BRACKET_RE.search(joined_blk)
            if m_brk:
                issues.append(f"第 {idx + 1} 块残留中文括号“{m_brk.group(0)}”")

        for tok in _extract_factual_tokens(sb):
            if tok.lower() not in norm_blk.lower():
                issues.append(f"第 {idx + 1} 块缺失关键填写编号或数值“{tok}”")

    full_text = " ".join(all_out_texts)
    letters = [ch for ch in full_text if ch.isalpha()]
    if letters:
        if lang_code == "ar":
            ar_cnt = sum(1 for ch in letters if "\u0600" <= ch <= "\u06ff")
            if ar_cnt / len(letters) < 0.25:
                issues.append("译稿阿拉伯文字符占比过低，疑似未按阿拉伯文输出")
        elif lang_code in ("en", "fr", "de", "es", "pt", "it", "nl", "id", "ms", "tr", "vi"):
            lat_cnt = sum(1 for ch in letters if ("A" <= ch <= "Z") or ("a" <= ch <= "z") or ("\u00c0" <= ch <= "\u024f"))
            if lat_cnt / len(letters) < 0.50:
                issues.append("译稿拉丁字母占比过低，疑似未按目标语言输出")
        elif lang_code == "ru":
            cyr_cnt = sum(1 for ch in letters if "\u0400" <= ch <= "\u04ff")
            if cyr_cnt / len(letters) < 0.25:
                issues.append("译稿西里尔字母占比过低，疑似未按俄文输出")

    return issues


def _build_messages(
    source_blocks: list[dict[str, Any]],
    lang_code: str,
    lang_en: str,
    authority_hint: str,
    prior_issues: list[str] | None = None,
) -> list[dict[str, str]]:
    placeholder_example = {
        "en": "[To be completed: ...]",
        "fr": "[À compléter : ...]",
        "ar": "[يُستكمل: ...]",
    }.get(lang_code, f"[To be completed in {lang_en}]")

    sys_prompt = (
        f"You are a senior legal and regulatory filing translator preparing an official administrative "
        f"application letter in formal {lang_en} for direct submission to a government authority"
        f"{f' ({authority_hint})' if authority_hint else ''}.\n"
        f"Translate the structured JSON `blocks` from Chinese into formal, idiomatic administrative {lang_en}, "
        f"obeying EVERY rule below:\n"
        f"1. Output strictly a JSON object `{{\"blocks\": [...]}}` with the EXACT same number of blocks, "
        f"same order, same `type` values (`title`, `heading`, `paragraph`, `table`, `list`), and identical "
        f"table `head` column count and `rows` dimensions as the input.\n"
        f"2. Zero Chinese characters (`\\u4e00-\\u9fff`) and zero Chinese brackets (`【】《》`) may remain "
        f"anywhere in the output (including titles, headings, law names, attached document names, declaration, "
        f"and signature block).\n"
        f"3. Convert unfilled Chinese placeholders such as `【待补充：…】` or `【待企业填写】` into clean "
        f"square-bracketed {lang_en} placeholders like `{placeholder_example}`, and convert blank date "
        f"`年 月 日` into `____ / ____ / ________`.\n"
        f"4. Preserve ALL factual numbers, model codes, registration numbers, tax IDs, phone numbers, "
        f"email addresses, ISO standard codes, and any redaction token matching `__CYSH_SECRET_...__` "
        f"character-for-character (use Western digits 0-9 for codes and numbers).\n"
        f"5. Use authentic ministerial/regulatory filing register (e.g. for English: 'Pursuant to [Statute], the Applicant respectfully submits this application for prior approval/registration...'; "
        f"for French: 'Conformément aux dispositions de..., le demandeur a l’honneur de soumettre la présente demande...'; "
        f"for Arabic: 'وفقاً لأحكام... يتقدم مقدم الطلب بهذا الطلب الرسمي...'), precise automotive/telecom/data-protection terminology, and formal undertaking phrasing suitable for an executed corporate filing."
    )
    user_payload: dict[str, Any] = {
        "target_language": lang_en,
        "language_code": lang_code,
        "blocks": source_blocks,
    }
    if prior_issues:
        user_payload["fix_verification_errors"] = prior_issues
    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


async def _translate_block_slice(
    slice_blocks: list[dict[str, Any]],
    lang_code: str,
    lang_en: str,
    authority_hint: str,
    ctx_aliases: list[str],
    placeholder_org: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Translate a contiguous slice of blocks with automatic binary split on output truncation."""
    prior_issues: list[str] | None = None
    last_issues: list[str] = []
    replaced_org = False

    for _attempt in range(2):
        messages = _build_messages(slice_blocks, lang_code, lang_en, authority_hint, prior_issues)
        try:
            res = await call_llm_json(messages, temperature=0.1, max_tokens=8192)
        except LlmUnavailableError as exc:
            if "output_truncated" in str(exc) and len(slice_blocks) > 1:
                mid = len(slice_blocks) // 2
                left_b, left_r = await _translate_block_slice(
                    slice_blocks[:mid], lang_code, lang_en, authority_hint, ctx_aliases, placeholder_org
                )
                right_b, right_r = await _translate_block_slice(
                    slice_blocks[mid:], lang_code, lang_en, authority_hint, ctx_aliases, placeholder_org
                )
                return left_b + right_b, (left_r or right_r)
            raise HTTPException(503, f"外文正式稿生成暂时不可用：{exc}") from None

        raw_blocks = res.data.get("blocks")
        if not isinstance(raw_blocks, list):
            last_issues = ["模型未返回有效的申请书结构"]
            prior_issues = last_issues
            continue

        candidate = copy.deepcopy(raw_blocks)
        slice_replaced = False
        for alias in ctx_aliases:
            for blk in candidate:
                if isinstance(blk, dict) and _replace_in_block(blk, alias, placeholder_org):
                    slice_replaced = True

        last_issues = verify_foreign_blocks(slice_blocks, candidate, lang_code)
        if not last_issues:
            return candidate, slice_replaced
        prior_issues = last_issues
        replaced_org = replaced_org or slice_replaced

    raise HTTPException(502, "外文正式稿反向核验未通过，请重试：" + "；".join(last_issues[:3]))


async def generate_foreign_letter(
    session: AsyncSession,
    entry: AdmissionEntry,
    draft: AdmissionDraft,
    lang_info: tuple[str, str, str],
    authority_hint: str = "",
) -> dict[str, Any]:
    """Translate Part 1 of ``draft`` into ``lang_info`` and verify programmatically."""
    lang_code, lang_zh, lang_en = lang_info
    source_blocks = extract_part1_blocks(draft.blocks or [])
    if not source_blocks:
        raise HTTPException(422, "当前申请材料没有可译制的对外申请书内容")

    ctx_aliases = [a for a in egress_context.get().aliases if a and _CJK_RE.search(a)]
    placeholder_org = _PLACEHOLDER_BY_LANG.get(lang_code, "[Official Foreign-Language Name of Applicant]")

    out_blocks: list[dict[str, Any]] = []
    any_replaced = False
    chunk_size = 8 if (lang_code in ("ar", "ru", "th") and len(source_blocks) > 12) else 16
    if len(source_blocks) <= chunk_size:
        out_blocks, any_replaced = await _translate_block_slice(
            source_blocks, lang_code, lang_en, authority_hint, ctx_aliases, placeholder_org
        )
    else:
        for i in range(0, len(source_blocks), chunk_size):
            part_blocks, part_rep = await _translate_block_slice(
                source_blocks[i : i + chunk_size], lang_code, lang_en, authority_hint, ctx_aliases, placeholder_org
            )
            out_blocks.extend(part_blocks)
            any_replaced = any_replaced or part_rep

    final_issues = verify_foreign_blocks(source_blocks, out_blocks, lang_code)
    if final_issues:
        raise HTTPException(502, "外文正式稿反向核验未通过，请重试：" + "；".join(final_issues[:3]))

    notes: list[str] = []
    if any_replaced:
        notes.append("企业中文名称因隐私保护未外发模型，已在译稿中留出外文注册名称填写位，签章前请填入外文全称。")

    item_short = re.sub(r"(申请办理|申请|办理)$", "", entry.name_zh or "") or (entry.name_zh or "准入事项")
    foreign_title = next(
        (str(b.get("text") or "").strip() for b in out_blocks if b.get("type") == "title" and str(b.get("text") or "").strip()),
        f"Application ({lang_en})",
    )
    return {
        "lang_code": lang_code,
        "lang_zh": lang_zh,
        "lang_en": lang_en,
        "title": foreign_title,
        "name_zh": f"{item_short}申请书（{lang_zh}正式稿）",
        "blocks": out_blocks,
        "verified": True,
        "notes": notes,
        "generated_at": datetime.now(UTC).isoformat(),
    }
