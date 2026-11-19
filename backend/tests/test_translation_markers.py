"""翻译时的隐私占位符：同一个电话、邮箱在一段里出现几次，占位符就出现几次；按原文次数核对，原样还原，
不因“出现了两次”把整条译文判为缺失（埃及 NTRA 实验室名单曾因此一直译不过）。少了一处仍然判缺失。"""

import re

import pytest

from app.core.config import get_settings
from app.knowledge import translator


class _Resp:
    status_code = 200

    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._content}}], "usage": {}, "model": "fake"}


def _fake_model(drop_one=False):
    async def post_model(payload, **kw):
        prompt = payload["messages"][-1]["content"]
        # 按完整性清单输出：每个标记一行，写了“共 N 次”的输出 N 次
        markers = []
        for m, n in re.findall(r"^(__CYSH_PRIVATE_[A-Z]+__)(?:（共 (\d+) 次）)?$", prompt, re.M):
            markers += [m] * int(n or 1)
        if drop_one:
            markers = markers[1:]
        return _Resp("联系人电话：" + "；".join(markers))

    return post_model


@pytest.mark.parametrize("drop_one", [False, True])
async def test_repeated_private_values_restore_by_source_count(monkeypatch, drop_one):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_redaction", "on")
    from app.analysis import llm

    monkeypatch.setattr(llm, "post_model", _fake_model(drop_one))
    text = "Lab A contact: +91-11-41595580. Lab B contact: +91-11-41595580, email lab@example.com."
    out = await translator._call_llm_translate(text, "en", [], [])
    content = out[0]
    if drop_one:
        assert "__CYSH_RESTORE_MISSING__" in content
    else:
        assert "__CYSH_" not in content
        assert content.count("+91-11-41595580") == 2 and "lab@example.com" in content
