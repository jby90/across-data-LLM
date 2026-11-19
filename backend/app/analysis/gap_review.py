# ruff: noqa: E501  提示词是长句数据
"""Set an uploaded company document against the target country's requirements.

For each dimension that is a requirement on the company (data classification, cross-border
transfer, consent and notice, storage and security, market admission) the model is given the
country's clauses (numbered) and the passages of the document that look relevant, and lists the
requirements the clauses state, whether the document covers each, what is missing, how to fill it.

What the model says is checked by the program before the user sees it:
* “covered” or “partly covered” needs a quotation from the document, word for word; a quotation that
  is not in the document turns the requirement into “not covered”;
* the clause numbers it cites must be ones it was given; the clause table is printed by the program;
* a figure in a requirement or suggestion must be in the cited clauses or the document passages;
* the counts in the summary are counted by the program from the table, not written by the model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.analysis import document_narrative as nar
from app.analysis.llm import LlmUnavailableError, call_llm_json
from app.analysis.workflow_engine import EIGHT_DIMENSIONS_SPEC
from app.knowledge import whitepaper as wp

REVIEW_DIMENSIONS = ("D2", "D3", "D4", "D5", "D7")
PASSAGE_CHARS = 700
PASSAGES_PER_DIM = 8
STATUS = {"covered": "已涵盖", "partial": "部分涵盖", "missing": "未涵盖"}

SYSTEM = (
    "你是企业数据合规的评审员。用户把企业现有的一份制度文件交给你，你要对照目标国法规条文，指出这份文件对该方面“涵盖了什么、缺了什么、怎么补”。"
    "只能使用 clauses 里编号的条文和 passages 里的文件原文片段；不得补充条文以外的法律要求、数字或机构。"
    "先从 clauses 中归纳出这一方面对企业的具体要求（最多 6 条，每条一句话，不超过 60 字），再逐条判断文件的情况："
    "status 取 covered（文件里有明确对应的内容）/ partial（有但不完整）/ missing（文件里没有）。"
    "判为 covered 或 partial 时，evidence 必须逐字摘录 passages 里能证明的一句或一小段原文（不超过 120 字，不得改写、翻译或拼接）；判为 missing 时 evidence 写空字符串。"
    "gap 写明还缺什么（covered 时写空字符串），suggestion 写一句具体可执行的补充办法（不超过 80 字）。"
    "每条要求在 cites 里写出所依据条文的编号；没有条文依据的不要写成要求。"
    "文字用中文，正文里不得出现“条文6”“clauses”“passages”这类内部编号或字段名；需要提法规时用简称加条号。"
    "只输出 JSON：{\"items\":[{\"requirement\":\"\",\"cites\":[1],\"status\":\"missing\",\"evidence\":\"\",\"gap\":\"\",\"suggestion\":\"\"}]}。"
)


@dataclass
class Review:
    blocks: list[dict] = field(default_factory=list)
    tokens: int = 0
    counts: dict[str, int] = field(default_factory=lambda: {"covered": 0, "partial": 0, "missing": 0})
    dropped: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)  # 最突出的几项缺口（每个方面先取一条），直接告诉用户


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _grams(text: str) -> set[str]:
    t = re.sub(r"\W+", "", text)
    return {t[i : i + 2] for i in range(len(t) - 1)}


def passages_for(document: str, clauses: list[dict], brief: str) -> list[str]:
    """The passages of the document that look most like what the clauses talk about."""
    paragraphs = [p.strip() for p in re.split(r"\n{2,}|\n(?=[0-9一二三四五六七八九十（(第]+[、.．)）])", document) if p.strip()]
    chunks: list[str] = []
    for p in paragraphs:
        while len(p) > PASSAGE_CHARS:
            cut = max(p.rfind("。", 0, PASSAGE_CHARS), p.rfind("；", 0, PASSAGE_CHARS), p.rfind(".", 0, PASSAGE_CHARS))
            cut = cut if cut > PASSAGE_CHARS // 2 else PASSAGE_CHARS
            chunks.append(p[: cut + 1])
            p = p[cut + 1 :].strip()
        if p:
            chunks.append(p)
    target = _grams(brief + " " + " ".join(str(c.get("quote_zh") or c.get("quote_original") or "")[:300] for c in clauses))
    scored = sorted(((len(_grams(c) & target) / (len(_grams(c)) ** 0.5 + 1), i) for i, c in enumerate(chunks)), reverse=True)
    keep = sorted(i for _, i in scored[:PASSAGES_PER_DIM])
    return [chunks[i] for i in keep]


def _check_item(item: object, offered: dict[int, dict], doc_squashed: str, allowed: set[str]) -> tuple[dict | None, str]:
    if not isinstance(item, dict):
        return None, "格式不对"
    requirement = str(item.get("requirement") or "").strip()
    if not requirement:
        return None, "空要求"
    cites = [c for c in (item.get("cites") or []) if isinstance(c, int)]
    if not cites or any(c not in offered for c in cites):
        return None, "要求没有可核对的条文依据"
    status = str(item.get("status") or "")
    if status not in STATUS:
        return None, "状态不在 covered/partial/missing 内"
    evidence = " ".join(str(item.get("evidence") or "").split())
    note = ""
    if status in ("covered", "partial"):
        if not evidence or _squash(evidence) not in doc_squashed:
            status, evidence, note = "missing", "", "（模型引用的原文在文件中找不到，按未涵盖处理）"
    else:
        evidence = ""
    text_out = [requirement, str(item.get("gap") or ""), str(item.get("suggestion") or "")]
    for t in text_out:
        if re.search(r"条文\s*\d|\bclauses?\b|\bpassages?\b|\bcites?\b", t, re.I):
            return None, "正文里出现内部编号或字段名"
        if nar._numbers(t) - allowed:
            return None, "出现文件和条文里没有的数字"
    return {
        "requirement": requirement,
        "cites": sorted(set(cites)),
        "status": status,
        "evidence": evidence,
        "gap": (str(item.get("gap") or "").strip() + note).strip(),
        "suggestion": str(item.get("suggestion") or "").strip(),
    }, ""


def _evidence_cell(evidence: str, sources: list[tuple[str, str]]) -> str:
    if not evidence:
        return "—"
    quote = "“" + evidence + "”"
    if len(sources) > 1:
        key = _squash(evidence)
        name = next((n for n, t in sources if key in t), "")
        if name:
            return f"{quote}\n——《{name}》"
    return quote


async def review_dimension(
    code: str, title: str, country: str, clauses: list[dict], document: str, refs: dict,
    sources: list[tuple[str, str]] | None = None,
) -> tuple[list[list[str]], int, list[str], dict[str, int]]:
    offered_list = nar._offered(clauses, None)[: wp.PER_DIMENSION]
    offered = {c["n"]: c for c in offered_list}
    counts = {"covered": 0, "partial": 0, "missing": 0}
    if not offered_list:
        return [], 0, [f"{code}: 库里没有该国与此方面相关的条文"], counts
    passages = passages_for(document, clauses, wp.DIMENSION_BRIEF[code])
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": json.dumps(
                {"country": country, "aspect": title, "means": wp.DIMENSION_BRIEF[code], "clauses": offered_list, "passages": passages},
                ensure_ascii=False,
            ),
        },
    ]
    try:
        res = await call_llm_json(messages, max_tokens=9000, temperature=0.1)
    except LlmUnavailableError as exc:
        raise nar.NarrativeError(str(exc)) from exc
    data = res.data if isinstance(res.data, dict) else {}
    tokens = int(getattr(res, "prompt_tokens", 0) or 0) + int(getattr(res, "completion_tokens", 0) or 0)
    allowed: set[str] = set()
    allowed |= nar.offered_numbers(clauses, offered_list)
    for p in passages:
        allowed |= nar.source_numbers(p)
    doc_squashed = _squash(document)
    squashed_sources = [(n, _squash(t)) for n, t in (sources or [])]
    rows: list[list[str]] = []
    dropped: list[str] = []
    for item in data.get("items") or []:
        ok, why = _check_item(item, offered, doc_squashed, allowed)
        if ok is None:
            dropped.append(f"{code}: {why}")
            continue
        counts[ok["status"]] += 1
        marks = nar._cite_line(ok["cites"], offered, refs)
        rows.append(
            [
                ok["requirement"] + marks,
                STATUS[ok["status"]],
                _evidence_cell(ok["evidence"], squashed_sources),
                " ".join(x for x in (ok["gap"], ("建议：" + ok["suggestion"]) if ok["suggestion"] else "") if x) or "—",
            ]
        )
    return rows, tokens, dropped, counts


async def review_documents(session, files: list[tuple[str, str]], country_code: str) -> Review:
    """One comparison over several uploaded files: together they are the company's current state."""
    if len(files) == 1:
        return await review_document(session, files[0][0], files[0][1], country_code)
    document = "\n\n".join(f"【{n}】\n{t}" for n, t in files)
    names = "、".join(f"《{n}》" for n, _ in files[:4]) + ("等" if len(files) > 4 else "")
    return await review_document(session, f"{len(files)} 份文件（{names}）", document, country_code, sources=files)


async def review_document(
    session, filename: str, document: str, country_code: str, sources: list[tuple[str, str]] | None = None
) -> Review:
    """Blocks of the whole comparison, ready for a page or an export."""
    from app.analysis.workflow_engine import country_name_zh

    name = country_name_zh(country_code)
    per_dim = await wp.gather(session, country_code)
    titles = dict(EIGHT_DIMENSIONS_SPEC)
    review = Review()
    refs: dict[tuple[str, str], tuple[int, str]] = {}
    body: list[dict] = []
    for code in REVIEW_DIMENSIONS:
        chosen, picked_tokens = await wp.select_clauses(code, titles[code], per_dim[code])
        rows, used, dropped, counts = await review_dimension(code, titles[code], name, chosen, document, refs, sources)
        review.tokens += used + picked_tokens
        review.dropped += dropped
        first = next((r[0] for r in rows if r[1] == STATUS["missing"]), None)
        if first:
            review.gaps.append(re.sub(r"(\[\d+\])+\s*$", "", first).strip().rstrip("。；;，,") + "")
        for k, v in counts.items():
            review.counts[k] += v
        body.append({"type": "heading", "text": titles[code]})
        if rows:
            body.append({"type": "table", "head": ["要求", "现状", "文件里的依据（原文）", "差距与建议"], "widths": [30, 10, 28, 32], "rows": rows})
        else:
            body.append({"type": "paragraph", "text": "本库目前没有收录该国与这一方面直接相关的条文，暂无法逐项对照。"})
    total = sum(review.counts.values())
    head = [
        {"type": "title", "text": (f"{filename}合规现状对照（对照{name}）" if sources else f"《{filename}》合规现状对照（对照{name}）")},
        {
            "type": "paragraph",
            "text": (
                f"本次从{name}法规中归纳出 {total} 项与{'这些文件合起来' if sources else '这份文件'}相关的要求：已涵盖 {review.counts['covered']} 项，"
                f"部分涵盖 {review.counts['partial']} 项，未涵盖 {review.counts['missing']} 项。"
                "“已涵盖”和“部分涵盖”都附有文件里的原文摘录，可自行核对；没有摘录的一律按未涵盖处理。"
                "本对照依据本平台收录的该国法规，不是法律意见。"
            ),
        },
    ]
    tail: list[dict] = []
    if refs:
        tail = [
            {"type": "heading", "text": "参考条文"},
            {
                "type": "table",
                "head": ["编号", "法规与条款", "条文摘录"],
                "widths": [8, 30, 62],
                "rows": [[f"[{n}]", f"{doc} {nar._clause_label(clause)}".strip(), text[:220]] for (doc, clause), (n, text) in refs.items()],
            },
        ]
    review.gaps = review.gaps[:4]
    review.blocks = head + body + tail
    return review
