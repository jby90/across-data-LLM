"""文书性质判定：一份文件是不是法律文书。

相关性（与车联网数据合规是否相关）和文书性质（是不是法律文书）是两件事：
一份讲个人数据保护的白皮书或咨询报告“主题相关”，却不是法律，不能进入法律库、不能被报告当作法律依据引用。
这里只回答第二个问题，结果写入 Document.instrument_kind 之外的独立判定，不改动相关性结论。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.analysis.llm import post_model
from app.core.config import get_settings

# 立法 / 配套法规 / 监管指南与问答 / 标准 / 非法律资料
NATURES = ("legislation", "subsidiary", "guidance", "standard", "admission_material", "non_legal")
NATURE_LABEL = {
    "legislation": "法律（立法文本）",
    "subsidiary": "配套法规（条例、规章、通知）",
    "guidance": "监管指南与问答",
    "standard": "标准",
    "admission_material": "准入申报材料（官方发布的申请、申报要求与表格）",
    "non_legal": "非法律资料",
}
# 进入法律库的性质；准入申报材料不是法律但对车企合规有用，保留并单独归类（不排除）；
# 只有 non_legal 才会被挡在法律库之外
LEGAL_NATURES = frozenset({"legislation", "subsidiary", "guidance", "standard"})
KEEP_NATURES = LEGAL_NATURES | {"admission_material"}

_HINT_NON_LEGAL = re.compile(
    r"white\s*paper|whitepaper|白皮书|annual\s+report|年度报告|survey|调研|研究报告|"
    r"consult(?:ing|ation)\s+(?:paper|report)|press\s+release|新闻|newsletter|brochure|"
    r"faq\s+for\s+consumers|case\s+study|blog",
    re.IGNORECASE,
)

SYSTEM_PROMPT = (
    "你是法律文献编目专家。请判断给定文件的“文书性质”，只看它是什么类型的文件，不判断是否与某个主题相关。\n"
    "分类：\n"
    "- legislation：立法机关或政府颁布的法律、法令、基本法（含修订版、修正案）；\n"
    "- subsidiary：依据法律制定的条例、规章、实施细则、部门规定、生效通知等配套法规；\n"
    "- guidance：监管机构发布的指南、问答、解释性文件、执法指引（官方发布，不具有立法效力）；\n"
    "- standard：技术或管理标准；\n"
    "- admission_material：监管机构官方发布的、企业办理准入、许可、测试批准所需遵循的申请要求、"
    "申报材料清单和申请表（例如自动驾驶车辆道路试验的申请须知与表格）。它不是立法，但车企做合规准入要用，"
    "和下面的 non_legal 必须区分开；\n"
    "- non_legal：白皮书、研究报告、咨询报告、行业分析、新闻、宣传册、与准入合规无关的一般办事表格"
    "（如查阅信息申请）、门户首页或导航页、非官方机构发布的解读文章等。\n"
    "判断依据是标题、来源网址和正文摘录；只看标题不看正文会误判，正文摘录与标题不符时以正文为准。"
    "拿不准时 confidence 填 low，不要硬猜。\n"
    '严格输出 JSON：{"nature":'
    '"legislation|subsidiary|guidance|standard|admission_material|non_legal",'
    '"confidence":"high|medium|low","reason":"一句中文理由"}'
)


@dataclass(frozen=True)
class NatureVerdict:
    nature: str
    confidence: str
    reason: str

    @property
    def is_legal(self) -> bool:
        return self.nature in LEGAL_NATURES

    @property
    def needs_review(self) -> bool:
        """低置信度，或判为非法律资料（要把一份文件挡在法律库外，宁可多一次确认）。"""
        return self.confidence == "low" or self.nature == "non_legal"


def _parse(raw: str) -> dict[str, Any]:
    text = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1])


async def classify_legal_nature(
    *,
    title: str,
    source_url: str,
    document_type: str,
    country_code: str,
    sample_text: str,
    pages: int | None = None,
) -> NatureVerdict | None:
    """调用大模型判定文书性质；模型不可用或输出不合规时返回 None（调用方保持原状，不猜）。"""
    settings = get_settings()
    if not settings.llm_api_key or not settings.legal_nature_screening_enabled:
        return None
    hint = (
        "标题含白皮书/报告/新闻类字样" if _HINT_NON_LEGAL.search(f"{title} {source_url}") else "无"
    )
    user = (
        f"国家: {country_code}\n标题: {title}\n来源网址: {source_url}\n"
        f"系统记录的文件类型: {document_type}\n页数: {pages or '未知'}\n"
        f"规则提示: {hint}\n正文摘录:\n{sample_text[:1800]}"
    )
    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "temperature": 0.0,
    }
    if "deepseek" in settings.llm_model.lower():
        payload["reasoning_effort"] = "low"
    try:
        resp = await post_model(payload, timeout=60.0, purpose="document_legal_nature")
        if resp.status_code != 200:
            return None
        data = _parse(resp.json()["choices"][0]["message"]["content"])
    except Exception:
        return None
    nature = str(data.get("nature", "")).strip()
    confidence = str(data.get("confidence", "")).strip()
    if nature not in NATURES:
        return None
    if confidence not in ("high", "medium", "low"):
        confidence = "low"
    return NatureVerdict(nature, confidence, str(data.get("reason") or "").strip()[:300])
