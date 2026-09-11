"""Marketplace month boundaries for Daraz performance aggregation.

Phase 2.5A live probes returned order timestamps with +0800 offsets
(Lazada-style), even for Pakistan sellers. Monthly windows therefore use
UTC+08:00 so created_after / created_before align with upstream timestamps.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

# Daraz Open Platform order timestamps observed as +0800 (not PK +05:00).
MARKETPLACE_TZ = timezone(timedelta(hours=8))
MARKETPLACE_TZ_LABEL = "UTC+08:00 (Daraz/Lazada API timestamp convention)"


def month_window(year: int, month: int) -> tuple[datetime, datetime]:
    """Inclusive start / exclusive end for a calendar month in marketplace TZ."""
    if month < 1 or month > 12:
        raise ValueError("month must be 1..12")
    start = datetime(year, month, 1, 0, 0, 0, tzinfo=MARKETPLACE_TZ)
    if month == 12:
        end = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=MARKETPLACE_TZ)
    else:
        end = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=MARKETPLACE_TZ)
    return start, end


def month_window_iso(year: int, month: int) -> tuple[str, str]:
    start, end = month_window(year, month)
    return start.isoformat(), end.isoformat()


def current_marketplace_month(now: datetime | None = None) -> tuple[int, int]:
    now = now or datetime.now(MARKETPLACE_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=MARKETPLACE_TZ)
    else:
        now = now.astimezone(MARKETPLACE_TZ)
    return now.year, now.month


def previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def growth_pct(current: float | int | None, previous: float | int | None) -> float | None:
    """MoM percent change. Null when previous is 0/None (no divide-by-zero / fake +100%)."""
    if current is None or previous is None:
        return None
    prev = float(previous)
    if prev == 0:
        return None
    return round((float(current) - prev) / prev * 100.0, 4)


def iter_recent_months(count: int = 6, *, now: datetime | None = None) -> Iterable[tuple[int, int]]:
    y, m = current_marketplace_month(now)
    for _ in range(max(1, count)):
        yield y, m
        y, m = previous_month(y, m)
