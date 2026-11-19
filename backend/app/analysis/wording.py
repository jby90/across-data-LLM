# ruff: noqa: E501
"""The platform's own words in the assistants' replies: what the user fills in is a “填写项”, never a
“题” (the user, 11.10: “我们不是做题”). The prompts say so; this catches a reply that slips."""

from __future__ import annotations

import re

_SAME = [
    (re.compile(r"上一道题|上一题|上道题"), "上一项"),
    (re.compile(r"下一道题|下一题|下道题"), "下一项"),
    (re.compile(r"这一道题|这一题|这道题|这题|本题|此题|该题"), "这一项"),
    (re.compile(r"那一道题|那一题|那道题|那题"), "那一项"),
    (re.compile(r"每一道题|每道题|每一题|每题|各题"), "每一项"),
    (re.compile(r"题旁"), "填写项旁"),
    (re.compile(r"(?<![标主问议课难专话命])题目"), "填写项"),
    (re.compile(r"([未已待])填填写项"), r"\1填项"),
]


def items_not_questions(text: str) -> str:
    for pattern, word in _SAME:
        text = pattern.sub(word, text)
    return text
