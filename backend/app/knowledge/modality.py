"""否定词一致性校验：法律译文最危险的错误是把“不得”译丢，或凭空译出“不得”。

只抓高把握的错误，宁可少报也不乱报：
  1. 原文是禁止性规定，译文里一个否定词都没有；
  2. 译文出现“不得 / 禁止 / 严禁”，原文却没有任何否定或限制性表达。
“应当”与“可以”的译反不在这里查：shall 在法律英语里大量只是陈述语气
（shall have jurisdiction → 有权），用关键词判断误报极多，改由回译对照这类语义检查处理。
覆盖英语、法语、阿拉伯语原文；其他语言不检查（返回空）。

阿拉伯语注意：本库的阿语文本层常把连字“لا”存成“ال”（如“ال يجوز”实为“لا يجوز”），
所以下面的阿语规则同时接受两种写法。
"""

from __future__ import annotations

import re

_AR_LETTER = "؀-ۿ"
_AR = re.compile(f"[{_AR_LETTER}]")

_ZH_NEG = re.compile(r"[不无禁未勿非否没]")
# 只认明确的禁止性表述。“不可”（不可行、不可抗力）、“不准确”、“不准备”都是描述性用法，不当作禁止
_ZH_PROHIBIT = re.compile(r"不得(?!不)|禁止|严禁|不应|不准(?!确|备)|不允许|不许|无权")

# ---- 英语
_EN_NEGATION_ANY = re.compile(
    r"\b(?:not|no|never|neither|nor|cannot|without|unless|except|prohibit\w*|forbid\w*|ban\w*|"
    r"bar\w*|restrict\w*|prevent\w*|refus\w*|deny|denies|denied|void|invalid|illegal|unlawful|"
    r"fail\w*|refrain\w*|exclud\w*|exempt\w*|non-\w+|un\w{4,}|in(?:accura|practica|complet|valid|effect)\w*|"
    r"offence|offense|penalt\w+|otherwise)\b|n't",
    re.IGNORECASE,
)
_EN_PROHIBIT = re.compile(
    r"\b(?:shall|must|may|should|can|will|is|are|be)\s+not\b|\bno\s+(?:person|organi[sz]ation|one|party|"
    r"individual)\b[^.;]{0,100}?\b(?:shall|may|must|can)\b|\bcannot\b|\bprohibit(?:ed|s)?\b|"
    r"\bnot\s+(?:be\s+)?(?:permitted|allowed)\b|\bforbid(?:den|s)?\b",
    re.IGNORECASE,
)
_EN_NOT_REQUIRED = re.compile(
    r"\bnot\s+(?:be\s+)?(?:required|necessary|obliged|obligated)\b|\bneed\s+not\b|\bnot\s+be\s+liable\b",
    re.IGNORECASE,
)

# ---- 法语（ne … que 是“仅”，不是否定；只认 ne … pas/plus/jamais 和 interdit）
_FR_NEGATION_ANY = re.compile(
    r"\bne\b|\bn['’]|\binterdi\w*\b|\bsans\b|\bà\s+moins\b|\bsauf\b|\bnon\b|\bnul\w*\b|\binvalide\b|"
    r"\billégal\w*\b|\bdéfaut\b|\bsanction\w*\b|\bamende\b|\bpeine\b|\baucun\w*\b",
    re.IGNORECASE,
)
_FR_PROHIBIT = re.compile(
    r"\bne\s+(?:\w+\s+){0,2}(?:pas|plus|jamais)\b|\bn['’]\s*\w+\s+(?:\w+\s+){0,2}(?:pas|plus|jamais)\b|"
    r"\best\s+interdit\w*|\bsont\s+interdit\w*|\binterdit\s+de\b|\bil\s+est\s+défendu\b",
    re.IGNORECASE,
)
_FR_ONLY = re.compile(r"\bne\b[^.;]{0,120}?\bque\b", re.IGNORECASE)

# ---- 阿拉伯语（“لا”常被存成“ال”）
_AR_MARKS = re.compile("[ً-ْٰـ]")  # 元音符号与拉长符
_NO = r"(?:لا|ال)"
_AR_PROHIBIT = re.compile(
    rf"(?<![{_AR_LETTER}]){_NO}\s+(?:يجوز|تجوز|يحق|يُسمح|يسمح|يجب|يمكن)|يحظر|يُحظر|محظور|ممنوع|يمتنع"
)
_AR_NEGATION_ANY = re.compile(
    # “لاو / لاأ”是 OCR 把“لا”与后一个字母粘在一起；منع / عدم 可带前缀（بعدم、ومنع），所以不加词边界
    rf"(?<![{_AR_LETTER}])(?:لا|ال|لم|لن|غير|بدون|دون|إلا|إال|الا)(?![{_AR_LETTER}])|(?<![{_AR_LETTER}])لا[وأإ]|"
    r"عدم|منع|حظر|محظور|ممنوع|باطل|عقوبة|غرامة|استثناء|يستثنى|مخالفة|الإخلال|الاخلال|اإلخالل|القاهرة|"
    r"ألا|أال|إلا|إال|يحتج|الاحتجاج"
)


def _lang(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "other"
    if sum(1 for c in letters if "一" <= c <= "鿿") / len(letters) > 0.3:
        return "other"  # 中文原文不在检查范围
    if sum(1 for c in letters if _AR.match(c)) / len(letters) > 0.3:
        return "ar"
    # 法语特有的功能词；短句（如 “On ne peut préalablement renoncer au droit d’accès.”）也要能认出来
    fr = len(
        re.findall(
            r"\b(?:le|la|les|des|du|au|aux|ne|pas|que|qui|une|est|sont|doit|peut|peuvent|pour|dans|par|sur|avec)\b",
            text,
            re.IGNORECASE,
        )
    )
    # 英文文件里零星的法语词（地址、专名）不能让整条被当成法语：法语功能词要比英语的多
    en = len(
        re.findall(
            r"\b(?:the|of|and|to|in|is|are|shall|must|be|for|with|that|this|by|or|not|should|may)\b",
            text,
            re.IGNORECASE,
        )
    )
    return (
        "fr"
        if fr >= 2
        and fr > en
        and re.search(r"[àâçéèêëîïôùûü]|\b(?:ne|des|du|au|aux|les)\b", text, re.IGNORECASE)
        else "en"
    )


def modality_issues(original: str, translated: str) -> list[str]:
    """返回否定不一致问题清单；空表示没发现高把握的错误。"""
    orig, trans = (original or "").strip(), (translated or "").strip()
    if not orig or not trans:
        return []
    lang = _lang(orig)
    if lang == "other":
        return []
    if lang == "en":
        prohibited = bool(_EN_PROHIBIT.search(orig)) and not _EN_NOT_REQUIRED.search(orig)
        negated = bool(_EN_NEGATION_ANY.search(orig))
    elif lang == "fr":
        # “ne … que”（仅可）整句都不当作禁止
        prohibited = bool(_FR_PROHIBIT.search(orig)) and not _FR_ONLY.search(orig)
        negated = bool(_FR_NEGATION_ANY.search(orig))
    else:
        # 官方公报的阿语常带元音符号（يحظَّر、ُ يحظّر ُ），会把“يحظر”从中间打断；匹配前先去掉
        plain = _AR_MARKS.sub("", orig)
        prohibited = bool(_AR_PROHIBIT.search(plain))
        negated = bool(_AR_NEGATION_ANY.search(plain))

    issues: list[str] = []
    if prohibited and not _ZH_NEG.search(trans):
        issues.append("原文含禁止性表述，译文里没有任何否定词，可能把“不得”译丢了")
    if _ZH_PROHIBIT.search(trans) and not negated:
        issues.append("译文出现“不得/禁止”等禁止性表述，原文没有对应的否定或限制性表达")
    return issues
