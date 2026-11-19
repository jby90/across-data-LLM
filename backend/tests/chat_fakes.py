"""A stand-in for the model behind the conversation: reads the message by keywords the way a good
model would, so the flow (what each intent does) is tested without a model."""

import json
import types

from app.analysis import chat_intent, chat_law, chat_reply
from app.analysis.llm import LlmResult

# keyword in the user's message -> the structured reading the model would give
DEFAULT_RULES: list[tuple[tuple[str, ...], dict]] = [
    (("到哪一步", "还差什么", "还要回答几个", "填了多少"), {"intents": ["ask_status"], "topic": "collection"}),
    (("还在分析", "结果在哪", "好了没", "什么结论"), {"intents": ["ask_status"], "topic": "files"}),
    (("要满足什么条件", "有什么规定", "什么意思"), {"intents": ["ask_law"], "law_topic": "cross_border"}),
    (("出报告", "生成文件", "生成出来"), {"intents": ["generate"]}),
    (("白皮书", "海外准入", "官方表格"), {"intents": ["ask_platform"]}),
    (("分析一下", "分析下", "对照", "重新对照"), {"intents": ["file_request"]}),
    (("你能帮我做什么", "什么叫", "不太懂", "不会答", "业务不太懂", "两眼一抹黑"), {"intents": ["help"]}),
    (("谢谢", "好的"), {"intents": ["smalltalk"]}),
    (("回答的不对", "什么质量"), {"intents": ["feedback"]}),
    (("天气", "写首诗", "的诗", "忽略之前"), {"intents": ["off_topic"]}),
    (("asdf",), {"intents": ["unclear"]}),
]


def install(monkeypatch, rules=None, *, help_reply=None, law_reply=None, default=None):
    """Patch the model calls of the conversation. ``rules`` extend/override DEFAULT_RULES; an
    unmatched message is read as an answer to the question on screen (or as ``default``)."""
    all_rules = list(rules or []) + DEFAULT_RULES
    calls: list[str] = []

    async def fake(messages, **_kw):
        system = messages[0]["content"]
        if "对话理解模块" in system:
            payload = json.loads(messages[1]["content"])
            text = payload["message"]
            calls.append(text)
            for words, reading in all_rules:
                if any(w in text for w in words):
                    return _result(reading)
            return _result(default or {"intents": ["provide_info"], "answer_kind": "answer"})
        if "正在回应用户的一句话" in system:
            return _result({"reply": help_reply or ""})  # 默认不写回复：走程序的固定回复
        return _result({"paragraphs": []})

    async def law(messages, **_kw):
        if law_reply is None:
            return _result({"paragraphs": []})
        return _result(law_reply)

    monkeypatch.setattr(chat_intent, "call_llm_json", fake)
    monkeypatch.setattr(chat_reply, "call_llm_json", fake)
    monkeypatch.setattr(chat_law, "call_llm_json", law)
    monkeypatch.setattr(chat_reply, "get_settings", lambda: types.SimpleNamespace(llm_api_key="x"))
    return calls


def _result(data):
    return LlmResult(data=data, prompt_tokens=1, completion_tokens=1, duration_ms=1, model="fake")
