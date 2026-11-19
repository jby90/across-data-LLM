"""咨询版报告的导出内容：先结论，再八个方面的短文，路线图在第八个方面，细表放附录。

这里只生成与格式无关的内容块；Word 与 PDF 各自负责把内容块画出来。
文字里的 `**重点**` 由各格式自行解析成粗体（见 rich_text）。
面向业务负责人：不出现维度编号，也不带“待核对/机翻”这类内部状态提示。

版式（参考成熟咨询报告的分层做法）：
  第一层 决策页：判定 + 一句话 + 先做的事（只列标题、责任人、时限）+ 风险排序
  第二层 七个方面的短文（每条发现只写“差距与原因”和建议的指向）+ 第八个方面的分阶段路线图
  第三层 附录：逐项差距登记表（你们的现状只在这里出现）、参考文献

同一件事只在一个地方完整写一次：
  - 每项整改动作的做法和完成标准，只在第八个方面的路线图里完整出现；
    决策页和各方面里只用短标题指向它。
  - “你们的现状”只在附录登记表里逐条列出；正文里每个方面的结论段已经概括了现状。
  - 同一份官方文件的链接只在第一次出现处写全，后面写“同 [n]”。
"""

from __future__ import annotations

import re
from typing import Any

from app.analysis.citation_policy import review_detail, review_label
from app.analysis.consulting import (
    CATEGORY_LABEL,
    LEVEL_LABEL,
    PHASE_LABELS,
    ROADMAP_DIMENSION,
    STATUS_LABEL,
    TOPIC_NAMES,
    build_conclusive_matrix,
)
from app.core.config import get_settings

# 各表的列宽（百分比，合计 100）。列宽必须写明：自动列宽在“时限”“先完成”这类窄列里会被长文字
# 挤成一字一行，Word 和 PDF 都是如此。
WIDTHS_ROADMAP = [50, 14, 9, 27]
WIDTHS_RISK = [9, 79, 12]
WIDTHS_REGISTER = [22, 28, 13, 37]
ROADMAP_HEAD = ["要做的事", "谁来做", "时限", "完成标准"]
REGISTER_HEAD = ["法律要求", "你们的现状", "达到情况", "判断理由"]

POINT_LIMIT = 90  # 参考文献的“要点”只是条目说明，不是条款的转述；全文在页面“查看原文”里
MIN_SENTENCE = 12  # 截断后至少要保留一个完整短句


def reference_point(points: list[str]) -> str:
    """参考文献条目的一句话要点：取第一条要点的开头，按句子边界截断（只截取，不改写）。

    参考文献是“引用项”，不能把整条义务的译文搬到文末，那等于把法条罗列换了个位置。"""
    text = " ".join((points or [""])[0].split())
    if len(text) <= POINT_LIMIT:
        return text
    cut = text[:POINT_LIMIT]
    # 优先在句末（。；）截断，其次逗号；只要能保留一个完整的短句（≥12 字）就在边界处截断，
    # 不留半句；整段都没有标点时才加省略号
    for marks in (("。", "；", ";"), ("，", ",")):
        i = max(cut.rfind(m) for m in marks)
        if i >= MIN_SENTENCE:
            return cut[: i + 1]
    return cut + "…"


def _ask_text(text: str) -> str:
    """去掉“请补充：”前缀（旧报告里模型自带的），展示处已有“需要补充”标签。"""
    return re.sub(r"^\s*(请|需要)?补充[：:]?\s*", "", text or "")


def _refs(nums: list[int]) -> str:
    return "".join(f"[{n}]" for n in nums)


def usable(consulting: dict | None) -> bool:
    return bool(consulting) and consulting.get("status") in ("ok", "partial")


def _actions_for(roadmap: list[dict], assessment_id: str) -> list[dict]:
    return [a for a in roadmap if assessment_id in a["addresses"]]


def _finding(a: dict, roadmap: list[dict]) -> dict:
    """正文里的一条发现：标题、状态，然后只写“差距与原因”，建议只给短标题。"""
    gap = a["status"] in ("partial", "non_compliant")
    rows = [("差距与原因" if gap else "判断", a["rationale"])]
    todo = _actions_for(roadmap, a["id"])
    if todo:
        rows.append(("建议", "；".join(x["title"] for x in todo)))
    return {
        "t": "finding",
        "title": a["requirement"],
        "refs": _refs(a["ref_numbers"]),
        "chips": f"{STATUS_LABEL[a['status']]} · 风险{LEVEL_LABEL[a['level']]}",
        "rows": [(k, v) for k, v in rows if v],
    }


def _roadmap_blocks(roadmap: list[dict], risk_refs: dict[str, list[int]]) -> list[dict]:
    """分阶段路线图。“先完成”并进“要做的事”那一格，不单占一列（窄列会被挤成一字一行）。"""
    title_of = {a["id"]: a["title"] for a in roadmap}
    out: list[dict] = []
    for phase in (1, 2, 3):
        acts = [a for a in roadmap if a.get("phase") == phase]
        if not acts:
            continue
        out.append({"t": "h3", "text": PHASE_LABELS[phase]})
        rows = []
        for a in acts:
            refs = sorted({n for t in a["addresses"] for n in risk_refs.get(t, [])})
            tag = CATEGORY_LABEL.get(a["category"], a["category"])
            before = "、".join(title_of[d] for d in a.get("depends_on", []) if d in title_of)
            text = f"【{tag}】**{a['title']}**：{a['action']}{_refs(refs)}"
            if before:
                text += f"（需先完成：{before}）"
            rows.append([text, a["owner_role"], f"{a['due_in_days']} 天内", a["done_criteria"]])
        out.append({"t": "table", "head": ROADMAP_HEAD, "rows": rows, "widths": WIDTHS_ROADMAP})
    return out


def _register_blocks(dims: list[dict]) -> list[dict]:
    """附录：按方面分组的差距登记表；方面名只作小标题，不在每一行重复。"""
    out: list[dict] = []
    for d in dims:
        if not d["assessments"]:
            continue
        rows = []
        for a in d["assessments"]:
            ask = a["status"] == "insufficient_info"
            rows.append(
                [
                    f"{a['requirement']}{_refs(a['ref_numbers'])}",
                    "尚不清楚" if ask else a["business_state"],
                    f"{STATUS_LABEL[a['status']]}\n风险{LEVEL_LABEL[a['level']]}",
                    f"需要补充：{_ask_text(a['needs_fact'])}" if ask else a["rationale"],
                ]
            )
        out.append({"t": "h3", "text": TOPIC_NAMES.get(d["code"], d["title"])})
        out.append({"t": "table", "head": REGISTER_HEAD, "rows": rows, "widths": WIDTHS_REGISTER})
    return out


def consulting_blocks(
    *,
    country: str,
    task: Any,
    rv: Any,
    consulting: dict,
    dimension: str | None,
    notices: list[str],
) -> list[dict]:
    edited = consulting.get("edited") or {"dimensions": [], "executive_summary": False}
    verdict = consulting["verdict"]
    roadmap = consulting.get("roadmap", [])
    show_review = get_settings().report_review_notices
    risk_refs = {
        a["id"]: a["ref_numbers"] for d in consulting["dimensions"] for a in d["assessments"]
    }
    dim_text = {
        d["dimension_code"]: d.get("content_markdown", "") for d in rv.dimensions_json or []
    }
    title_of = {d["code"]: d["title"] for d in consulting["dimensions"]}
    heading = f"{country} 汽车数据跨境合规评估报告" + (
        f"：{title_of.get(dimension, '')}" if dimension else ""
    )
    b: list[dict] = [
        {"t": "h1", "text": heading},
        {
            "t": "meta",
            "text": f"任务：{task.title}｜版本 v{rv.version_no}｜车型：{task.vehicle_model}",
        },
        *([{"t": "notice", "text": n} for n in notices] if show_review else []),
    ]

    # 第一层：决策页——先回答“能不能做、卡在哪、先做什么”
    if not dimension:
        b.append({"t": "verdict", "label": verdict["label"], "headline": verdict["headline"]})
        if edited.get("executive_summary"):
            b.extend(
                {"t": "p", "text": ln} for ln in rv.executive_summary.splitlines() if ln.strip()
            )
        else:
            b.append({"t": "p", "text": verdict["summary"]})
        has_active_body = bool(roadmap) or any(
            d.get("status") != "no_evidence" and d.get("code") != ROADMAP_DIMENSION
            for d in consulting.get("dimensions", [])
        )
        if has_active_body:
            cm = consulting.get("conclusive_matrix") or build_conclusive_matrix(consulting)
            b.append({"t": "h2", "text": "结论性研判与放行条件核定"})
            b.append({"t": "p", "text": cm["closure_rule"]})
            b.append(
                {
                    "t": "table",
                    "head": ["裁断决定类别", "核定结果", "合规研判依据与刚性执行要求"],
                    "widths": [24, 18, 58],
                    "rows": [[t["option"], t["mark"], t["basis"]] for t in cm["tiers"]],
                }
            )
        must = [x for x in roadmap if x["id"] in verdict["must_do_action_ids"]]
        if must:
            b.append({"t": "h2", "text": "先做这几件事"})
            b.append({"t": "meta", "text": "每一项的具体做法和完成标准，见最后一章的整改路线图。"})
            for i, x in enumerate(must, 1):
                refs = sorted({n for t in x["addresses"] for n in risk_refs.get(t, [])})
                b.append(
                    {
                        "t": "item",
                        "n": i,
                        "text": f"**{x['title']}**（{x['owner_role']}，{x['due_in_days']} 天内）"
                        f"{_refs(refs)}",
                    }
                )
        if consulting["top_risks"]:
            b.append({"t": "h2", "text": "风险排序"})
            b.append(
                {
                    "t": "table",
                    "head": ["排名", "问题", "风险"],
                    "widths": WIDTHS_RISK,
                    "rows": [
                        [str(i), f"{t['title']}{_refs(t['ref_numbers'])}", LEVEL_LABEL[t["level"]]]
                        for i, t in enumerate(consulting["top_risks"], 1)
                    ],
                }
            )

    # 第二层：各方面的短文。没有依据的方面合并成一段；落地方面单独处理
    dims = consulting["dimensions"]
    empty = [
        d
        for d in dims
        if d["status"] == "no_evidence"
        and d["code"] not in edited["dimensions"]
        and d["code"] != ROADMAP_DIMENSION
    ]
    for d in dims:
        if d in empty or d["code"] == ROADMAP_DIMENSION:
            continue
        b.append({"t": "h2", "text": d["title"]})
        if d["code"] in edited["dimensions"]:
            b.extend(
                {"t": "p", "text": ln}
                for ln in dim_text.get(d["code"], "").splitlines()
                if ln.strip()
            )
            continue
        if d["status"] == "failed":
            b.append({"t": "p", "text": "这个方面的咨询意见暂时没有生成，请重新评估。"})
            continue
        b.append({"t": "p", "text": d["conclusion"]})
        if d.get("overview"):
            b.append({"t": "p", "text": d["overview"]})
        b.extend(
            _finding(a, roadmap) for a in d["assessments"] if a["status"] != "insufficient_info"
        )
        asks = [a for a in d["assessments"] if a["status"] == "insufficient_info"]
        if asks:
            b.append({"t": "h3", "text": "需要你们补充"})
            b.extend(
                {
                    "t": "p",
                    "text": f"• {_ask_text(a['needs_fact'])}"
                    f"（{a['requirement']}{_refs(a['ref_numbers'])}）",
                }
                for a in asks
            )

    # 没有依据的方面不在报告里出现：有什么依据就写什么，不列“暂无”“待核查”这类条目。
    # 只有一个方面都写不出来时，才用一句话说明，避免出一份空报告。
    shown = [d for d in dims if d not in empty and d["code"] != ROADMAP_DIMENSION]
    if not shown and not consulting["roadmap"]:
        b.append(
            {
                "t": "p",
                "text": "目前法规库里还没有与这个场景直接对应的依据，补充相关法规后可重新评估。",
            }
        )

    # 落地方面：整改路线图（分三个阶段，标出先后依赖）
    road = next((d for d in dims if d["code"] == ROADMAP_DIMENSION), None)
    if road is not None:
        b.append({"t": "h2", "text": road["title"]})
        if ROADMAP_DIMENSION in edited["dimensions"]:
            b.extend(
                {"t": "p", "text": ln}
                for ln in dim_text.get(ROADMAP_DIMENSION, "").splitlines()
                if ln.strip()
            )
        else:
            intro = (
                road["conclusion"] if road["status"] == "ok" else consulting.get("roadmap_intro")
            )
            if intro:
                b.append({"t": "p", "text": intro})
            if road.get("overview"):
                b.append({"t": "p", "text": road["overview"]})
            if roadmap:
                b.extend(_roadmap_blocks(roadmap, risk_refs))

    # 第三层：附录
    register = _register_blocks(dims)
    if register:
        b.append({"t": "h2", "text": "附录：差距登记表"})
        b.append(
            {
                "t": "p",
                "text": "逐项列出每条法律要求、你们的现状和判断理由，供法务和合规人员逐条核对。",
            }
        )
        b.extend(register)

    b.append({"t": "h2", "text": "参考文献"})
    seen_url: dict[str, int] = {}
    seen_point: dict[str, int] = {}
    for r in consulting["references"]:
        # 优先用咨询层写的一句话；没有时（旧数据）才从条款义务转述里截取
        point = r.get("summary") or reference_point(r.get("points") or [])
        # 同一条要求引用了几条条款时，要点只写一遍，其余指向第一条
        if point in seen_point:
            point = f"同 [{seen_point[point]}]"
        elif point:
            seen_point[point] = r["n"]
        url = r.get("source_url") or ""
        # 同一份官方文件被多条条款引用时，链接只在第一次写全
        source = f"来源：同 [{seen_url[url]}]" if url in seen_url else f"来源：{url or '未提供'}"
        if url and url not in seen_url:
            seen_url[url] = r["n"]
        sub = [point and f"要点：{point}", source]
        if show_review:
            sub += [r.get("source_notice"), f"人工复核：{review_label(r)}；{review_detail(r)}"]
        b.append(
            {
                "t": "ref",
                "n": r["n"],
                "text": f"{r['document_title']} {r['clause_number']}（v{r['version_no']}）",
                "sub": [s for s in sub if s],
            }
        )
    return b
