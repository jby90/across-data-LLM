"""Deadlines and expiry dates, counted the way Chinese law counts periods.

A period of months or years ends on the corresponding day of its last month; when that month has no
such day, on the month's last day (《民法典》第二百零二条). The state words are the ones the
approval ledger and the project page share: 有效 / 即将到期 (within 90 days) / 已过期.
"""

from __future__ import annotations

import calendar
from datetime import date

SOON_DAYS = 90  # 90 天内到期算“即将到期”（准入批文台账与项目页同一口径）


def parse(value: str | None) -> date | None:
    """An ISO date (YYYY-MM-DD), or None when empty or not a date."""
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except ValueError:
        return None


def add_months(start: date, months: int) -> date:
    y, m = divmod(start.month - 1 + months, 12)
    year, month = start.year + y, m + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def days_left(until: date, today: date | None = None) -> int:
    return (until - (today or date.today())).days


def expiry_state(days: int | None) -> str:
    """有效 / 即将到期 / 已过期; “待填有效期” when the date is unknown."""
    if days is None:
        return "待填有效期"
    return "已过期" if days < 0 else "即将到期" if days <= SOON_DAYS else "有效"
