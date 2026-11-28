# ruff: noqa: E501
"""Where the enterprise's filing stands (requirement 2.6 “跟踪申报进度”).

The enterprise files with the authority itself; the platform does not talk to any official system.
The enterprise moves its project along by hand:

    准备中 → 已递交（递交日期） → 补正中（受理机构提的问题） → 已递交（补正后再次递交）
                               → 已取得批文（登记编号、日期，到期提醒）
                               → 未通过（原因） → 准备中（重新准备）

Kept in ``admission_projects.milestones`` (the column the old milestone ledger used):
``{"state", "submitted_on", "correction_on", "result_on", "reason", "history": [...]}``.
A project whose approval is registered counts as 已取得批文 even if it never went through the steps.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

STATES = ("preparing", "submitted", "correcting", "approved", "rejected")
STATE_ZH = {
    "preparing": "准备中",
    "submitted": "已递交",
    "correcting": "补正中",
    "approved": "已取得批文",
    "rejected": "未通过",
}
# 每个状态下一步能去哪（按钮），“approved” 只走批文登记
NEXT = {
    "preparing": ("submitted",),
    "submitted": ("correcting", "approved", "rejected", "preparing"),
    "correcting": ("submitted", "approved", "rejected"),
    "approved": (),
    "rejected": ("preparing",),
}


class FilingError(ValueError):
    pass


def state_of(project) -> str:
    m = project.milestones or {}
    s = m.get("state")
    if s in STATES:
        return s
    return "approved" if (project.approval or {}).get("number") else "preparing"


def filing_out(project, open_authority: int = 0) -> dict[str, Any]:
    m = project.milestones or {}
    s = state_of(project)
    return {
        "state": s,
        "state_zh": STATE_ZH[s],
        "submitted_on": m.get("submitted_on", ""),
        "correction_on": m.get("correction_on", ""),
        "result_on": m.get("result_on", ""),
        "reason": m.get("reason", "") if s == "rejected" else "",
        "rounds": sum(1 for h in m.get("history", []) if h.get("state") == "correcting"),
        "open_questions": open_authority,
        "history": m.get("history", []),
        "submitted_draft": m.get("submitted_draft", "") if s != "preparing" else "",
        "renewal": bool(m.get("renewal")),
        "next": list(NEXT[s]),
    }


def _day(value: str | None, field: str) -> str:
    if not value:
        return date.today().isoformat()
    try:
        d = date.fromisoformat(value)
    except ValueError as exc:
        raise FilingError(f"{field}的格式应为 年-月-日") from exc
    if d > date.today():
        raise FilingError(f"{field}不能晚于今天")
    return d.isoformat()


def move(project, to: str, *, on: str = "", reason: str = "", by: str = "", open_authority: int = 0) -> dict:
    """Move the project to ``to``; returns the new milestones (not committed)."""
    now = state_of(project)
    if to not in STATES:
        raise FilingError("没有这个申报状态")
    if to == now:
        raise FilingError(f"已经是“{STATE_ZH[to]}”")
    if to not in NEXT[now]:
        raise FilingError(f"“{STATE_ZH[now]}”之后不能直接改为“{STATE_ZH[to]}”")
    m = dict(project.milestones or {})
    m.setdefault("history", [])
    entry: dict[str, Any] = {"state": to, "at": datetime.now(UTC).isoformat(), "by": by}
    if to == "submitted":
        if open_authority:
            raise FilingError(f"受理机构提的问题还有 {open_authority} 条没处理完，处理完再递交")
        m["submitted_on"] = entry["on"] = _day(on, "递交日期")
    elif to == "correcting":
        m["correction_on"] = entry["on"] = _day(on, "收到补正通知的日期")
    elif to in ("approved", "rejected"):
        if to == "approved" and open_authority:
            raise FilingError(f"受理机构提的问题还有 {open_authority} 条没处理完，处理完再登记批文")
        m["result_on"] = entry["on"] = _day(on, "结果日期")
        if to == "rejected":
            if not reason.strip():
                raise FilingError("请写一句没通过的原因，重新准备时对照着改")
            m["reason"] = entry["reason"] = reason.strip()[:500]
    elif to == "preparing":
        m.pop("submitted_on", None)
    m["state"] = to
    m["history"] = [*m["history"], entry][-50:]
    return m
