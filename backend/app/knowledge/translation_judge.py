"""译文语义核验：模型只做“对齐与标注”，是否算错由程序裁决。

为什么不让模型直接评判好坏：实测让模型自由评判，会在自己的说明里写“基本对应、不影响含义”，
同时把严重度标成 critical（严重度虚高，对真实好译文误报三成），
而对“应当”被改成“可以”这类明显错误反而漏掉。
所以改成和整个系统一致的分工——模型提议，规则裁决：

1. 模型把原文切成句子或条款项，逐个找出译文里对应的片段，并标注两边各自的“规范类型”：
   义务 / 禁止 / 许可 / 陈述。模型只做标注，不评价好坏。
2. 程序核对：原文片段必须逐字出现在原文里，译文片段必须逐字出现在译文里，对不上的标注丢弃；
3. 程序比较两边类型：
   - 义务、禁止、许可三者之间互相不一致 → 情态译错（critical）；
   - 原文是禁止，译文却是陈述 → 禁止被译丢（critical）；
   - 原文里有实质内容的句子，译文里找不到对应 → 漏译（major）。
   原文是陈述性的 shall（定义、组成、管辖），译文没有“应当”不算错，
   所以“原文义务、译文陈述”不报。
模型不可用时返回 None，调用方不因此拒绝译文。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.analysis.llm import post_model
from app.core.config import get_settings

CATEGORIES = {
    "negation_modality": "否定或情态译错（不得/应当/可以弄反或丢失）",
    "omission": "漏译（原文的实质内容在译文里找不到）",
}
BLOCKING = frozenset({"critical", "major"})
NORMATIVE = frozenset({"obligation", "prohibition", "permission"})
TYPES = NORMATIVE | {"declarative"}
MIN_UNIT_LETTERS = 30  # 太短的片段（标题、条号、残句）不做漏译判断

SYSTEM_PROMPT = (
    "你是法律文本的对齐标注员。任务：把外文原文逐句对齐到中文译文，"
    "并标注每一对的“规范类型”。只标注，不评价译得好不好。\n"
    "做法：把原文切成句子或条款项（如 (a)(b) 各算一项），保持原文原样不改写；"
    "逐项在译文中找到对应的片段。\n"
    "规范类型四选一，只看这一项的主要谓语：\n"
    "- obligation：要求必须做某事"
    "（shall / must / is required to / doit / يجب / 应当 / 必须）；\n"
    "- prohibition：禁止做某事"
    "（shall not / must not / may not / il est interdit / لا يجوز / 不得 / 禁止）；\n"
    "- permission：允许或赋予权利（may / is entitled to / peut / يجوز / 可以 / 有权）；\n"
    "- declarative：定义、陈述事实、列举目录、标题、条件从句、修订指令、程序说明，"
    "没有规范性要求。\n"
    "注意：as the case may be、may be prescribed、shall have jurisdiction、"
    "shall consist of 这类不是规范，标 declarative。\n"
    "src 必须逐字摘自原文，tgt 必须逐字摘自译文，各自不超过 360 字；"
    "译文里确实找不到对应内容时 tgt 填 null，tgt_type 填 null。\n"
    "原文有乱码或识别噪声时照样逐字摘录。覆盖原文的全部实质内容，不要只挑一部分。\n"
    '严格输出 JSON：{"pairs":[{"src":"...",'
    '"src_type":"obligation|prohibition|permission|declarative",'
    '"tgt":"..."或null,'
    '"tgt_type":"obligation|prohibition|permission|declarative"或null}]}'
)


@dataclass(frozen=True)
class JudgeError:
    category: str
    severity: str
    source_span: str
    translation_span: str
    explanation: str

    @property
    def blocking(self) -> bool:
        return self.severity in BLOCKING


@dataclass(frozen=True)
class JudgeVerdict:
    errors: tuple[JudgeError, ...]
    discarded: int = 0  # 因片段对不上而被丢弃的标注数，用来观察模型标注的可靠度
    pairs: int = 0

    @property
    def passed(self) -> bool:
        return not any(e.blocking for e in self.errors)

    def issues(self) -> list[str]:
        """给重译用的问题清单：只含会挡住译文的错误，写明证据片段。"""
        return [
            f"语义对照核验：{CATEGORIES.get(e.category, e.category)}——{e.explanation}"
            f"［原文：{e.source_span[:80]}］"
            f"［译文：{e.translation_span[:80] or '（找不到对应内容）'}］"
            for e in self.errors
            if e.blocking
        ]


_LABEL = {"obligation": "义务", "prohibition": "禁止", "permission": "许可"}
_GARBLED = re.compile(r"(?:^|\s)[ً-ٟ]")  # 前面是空白的孤立变音符号：文本层抽取损坏的痕迹


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "").casefold()


def _letters(text: str) -> int:
    return sum(1 for c in text if c.isalpha())


def _parse(raw: str) -> dict[str, Any]:
    text = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1])


def build_verdict(data: dict[str, Any], original: str, translated: str) -> JudgeVerdict:
    """程序裁决：先核对片段真实存在，再比较两边的规范类型。"""
    src_all, trn_all = _squash(original), _squash(translated)
    errors: list[JudgeError] = []
    discarded = used = 0
    for item in (data.get("pairs") or [])[:60]:
        if not isinstance(item, dict):
            continue
        src = str(item.get("src") or "").strip()
        src_type = str(item.get("src_type") or "").strip().lower()
        tgt = item.get("tgt")
        tgt = str(tgt).strip() if tgt else ""
        tgt_type = str(item.get("tgt_type") or "").strip().lower()
        if not src or _squash(src) not in src_all or src_type not in TYPES:
            discarded += 1  # 原文片段对不上：模型在编造或改写，整条丢弃
            continue
        used += 1
        if tgt and _squash(tgt) not in trn_all:
            tgt = ""  # 译文片段对不上：当作没找到对应内容
        if not tgt:
            # 原文本身是被 PDF 抽取弄乱的文字（阿拉伯语常见孤立的变音符号），对不上不是译文的错
            if _letters(src) >= MIN_UNIT_LETTERS and not _GARBLED.search(src):
                errors.append(
                    JudgeError(
                        "omission", "major", src, "", "原文这一句在译文里找不到对应内容，可能漏译"
                    )
                )
            continue
        if tgt_type not in TYPES:
            continue
        if src_type in NORMATIVE and tgt_type in NORMATIVE and src_type != tgt_type:
            errors.append(
                JudgeError(
                    "negation_modality",
                    "critical",
                    src,
                    tgt,
                    f"原文是{_LABEL[src_type]}性规定，译文却是{_LABEL[tgt_type]}性表述",
                )
            )
        elif src_type == "prohibition" and tgt_type == "declarative":
            errors.append(
                JudgeError(
                    "negation_modality",
                    "critical",
                    src,
                    tgt,
                    "原文是禁止性规定，译文里禁止的含义丢了",
                )
            )
    return JudgeVerdict(tuple(errors), discarded, used)


CONFIRM_PROMPT = (
    "下面是若干原文句子和一份中文译文。请逐句在译文中找出表达同一内容的连续片段，"
    "片段必须逐字摘自译文，不超过 200 字；译文里确实没有对应内容时填 null。"
    "只做查找，不评价译得好不好。"
    '严格输出 JSON：{"found":[{"i":句子序号,"tgt":"译文片段"或null}]}'
)


async def _confirm_omissions(verdict: JudgeVerdict, translated: str) -> JudgeVerdict:
    """对“疑似漏译”再问一次：整段译文里找得到对应片段的，撤销这条漏译。

    第一次是整篇逐句对齐，句子一多模型容易把找得到的也标成“没有”；单独针对这几句再找，
    结果由程序核对：片段必须真的在译文里，原文里的数字也必须出现在该片段中。
    """
    from app.knowledge.numeric_integrity import tokens

    suspects = [e for e in verdict.errors if e.category == "omission"]
    if not suspects:
        return verdict
    settings = get_settings()
    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": CONFIRM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"sentences": [e.source_span for e in suspects], "translation": translated},
                    ensure_ascii=False,
                ),
            },
        ],
    }
    if "deepseek" in (settings.llm_model or "").lower():
        payload["thinking"] = {"type": settings.llm_judge_thinking}
    try:
        resp = await post_model(
            payload, timeout=settings.llm_timeout_seconds, purpose="translation_judge"
        )
        if resp.status_code != 200:
            return verdict
        found = _parse(resp.json()["choices"][0]["message"]["content"]).get("found") or []
    except Exception:
        return verdict  # 复核失败：保持原结论，不因此放行
    trn_all = _squash(translated)
    cleared: set[int] = set()
    for item in found:
        if not isinstance(item, dict) or not isinstance(item.get("i"), int):
            continue
        i, tgt = item["i"], str(item.get("tgt") or "").strip()
        if not (0 <= i < len(suspects)) or len(tgt) < 4 or _squash(tgt) not in trn_all:
            continue
        if not set(tokens(suspects[i].source_span)) <= set(tokens(tgt)):
            continue  # 原文里的数字在该片段里找不到，不能算覆盖
        cleared.add(i)
    kept = tuple(e for e in verdict.errors if e.category != "omission")
    kept += tuple(e for i, e in enumerate(suspects) if i not in cleared)
    return JudgeVerdict(kept, verdict.discarded, verdict.pairs)


async def judge_translation(original: str, translated: str, language: str) -> JudgeVerdict | None:
    """语义核验。模型不可用、输出不合规时返回 None（调用方保持原结论，不因此拒绝译文）。"""
    settings = get_settings()
    if not settings.llm_api_key or not (original or "").strip() or not (translated or "").strip():
        return None
    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"原文语言: {language}\n\n【原文】\n{original}\n\n【中文译文】\n{translated}"
                ),
            },
        ],
    }
    if "deepseek" in (settings.llm_model or "").lower():
        payload["thinking"] = {"type": settings.llm_judge_thinking}
        if settings.llm_judge_thinking == "enabled":
            payload["reasoning_effort"] = "low"
    for _ in range(2):
        try:
            resp = await post_model(
                payload, timeout=settings.llm_timeout_seconds, purpose="translation_judge"
            )
            if resp.status_code != 200:
                continue
            data = _parse(resp.json()["choices"][0]["message"]["content"])
            return await _confirm_omissions(build_verdict(data, original, translated), translated)
        except Exception:
            continue
    return None
