"""Which monitored sites need the platform administrator's attention, and why.

A blocked or failing site is retried automatically (see the scheduler's back-off); a reminder is
raised only when retrying has not helped, so people are asked to act only when the system cannot.
The rule is pure so every country and site type is judged the same way.
"""

from __future__ import annotations

from datetime import datetime, timedelta

FAILURES_BEFORE_REMINDER = 3  # 自动退避重试这么多次仍失败，才提醒管理员


def attention_of(
    *,
    enabled: bool,
    terms_status: str,
    consecutive_failures: int,
    last_run_at: datetime | None,
    interval_hours: int,
    now: datetime,
) -> str | None:
    """A short sentence for the administrator, or None when nothing needs doing."""
    if terms_status == "rejected":
        return None  # 已明确不采集
    if terms_status != "approved":
        return "已登记，待审核该站点的使用条款后启用定期监测"
    if not enabled:
        return None  # 管理员主动停用
    if consecutive_failures >= FAILURES_BEFORE_REMINDER:
        return (
            f"已自动重试 {consecutive_failures} 次仍无法读取，"
            "请检查站点是否改版，或在可访问该站点的网络环境下重试"
        )
    if last_run_at is not None and now - last_run_at > timedelta(hours=2 * interval_hours):
        return "超过两个监测周期没有成功更新"
    return None
