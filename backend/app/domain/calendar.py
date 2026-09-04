"""Calendar arithmetic in the company's timezone.

NFR-03: all dates are handled in Asia/Kolkata, and a booking's `date` is a
calendar date rather than an instant. The distinction is the whole reason this
module exists — `datetime.date.today()` on a server in UTC returns the previous
day for five and a half hours out of every twenty-four, which would silently
close the edit window early for anyone booking in the evening.

Nothing here touches the database or the network. Every function is pure, so
the rules can be tested without standing anything up.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# The single timezone the product operates in. Mirrored in app_settings so an
# admin can see it, but not intended to vary at runtime: changing it changes
# when days close for everyone at once.
COMPANY_TZ = ZoneInfo("Asia/Kolkata")

# A-13: the source spec says "non-working days (weekends)" without defining
# them. Saturday and Sunday.
WEEKEND_WEEKDAYS = frozenset({5, 6})  # Monday is 0


def now_in_company_tz() -> datetime:
    """The current instant, expressed in the company's timezone."""
    return datetime.now(tz=COMPANY_TZ)


def today_in_company_tz() -> date:
    """Today's calendar date in Asia/Kolkata.

    Use this everywhere instead of `date.today()`. A UTC-hosted process asked
    for `date.today()` at 02:00 IST answers with yesterday.
    """
    return now_in_company_tz().date()


def is_weekend(day: date) -> bool:
    """FR-CAL-05 — weekends are shown as non-bookable."""
    return day.weekday() in WEEKEND_WEEKDAYS


def period_of(day: date) -> str:
    """The `YYYY-MM` allowance period a date falls in (FR-BAL-01)."""
    return f"{day.year:04d}-{day.month:02d}"


def period_bounds(period: str) -> tuple[date, date]:
    """First and last calendar date of a `YYYY-MM` period, inclusive."""
    year, month = _parse_period(period)
    first = date(year, month, 1)
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return first, last


def previous_period(period: str) -> str:
    """The `YYYY-MM` period immediately before this one."""
    year, month = _parse_period(period)
    return f"{year - 1:04d}-12" if month == 1 else f"{year:04d}-{month - 1:02d}"


def periods_between(start: str, end: str) -> list[str]:
    """Every `YYYY-MM` from start to end inclusive.

    Returns an empty list when start is after end, which is what the carry
    forward calculation wants for a person's very first period — there is
    nothing before it to carry.
    """
    if start > end:
        return []
    year, month = _parse_period(start)
    out: list[str] = []
    while (current := f"{year:04d}-{month:02d}") <= end:
        out.append(current)
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def month_matrix(period: str) -> list[list[date | None]]:
    """A month laid out as weeks of seven, Monday first, padded with None.

    FR-CAL-01 renders one month at a time. Building the grid here rather than
    in the browser keeps the week-start convention in one place.
    """
    first, last = period_bounds(period)
    cells: list[date | None] = [None] * first.weekday()
    cells += [date(first.year, first.month, d) for d in range(1, last.day + 1)]
    while len(cells) % 7:
        cells.append(None)
    return [cells[i : i + 7] for i in range(0, len(cells), 7)]


def _parse_period(period: str) -> tuple[int, int]:
    try:
        year_text, month_text = period.split("-")
        year, month = int(year_text), int(month_text)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"period must look like YYYY-MM, got {period!r}") from exc
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range in period {period!r}")
    return year, month
