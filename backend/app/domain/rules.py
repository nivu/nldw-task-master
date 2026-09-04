"""Booking rules — §6.1, §6.3, and the FR-BOOK family.

Pure decision functions. Each returns either None (allowed) or a short reason
string that is safe to show the person who tripped it. They take the facts they
need as arguments and read nothing, so the rules can be tested exhaustively
without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.calendar import is_weekend, today_in_company_tz

# ---------------------------------------------------------------------------
# Categories and states — §6.1, §6.4
# ---------------------------------------------------------------------------

CATEGORIES = ("wfh", "casual", "sick")
CATEGORY_LABELS = {"wfh": "Work from home", "casual": "Casual leave", "sick": "Sick leave"}

FULL_DAY = Decimal("1.0")
HALF_DAY = Decimal("0.5")
VALID_DURATIONS = (HALF_DAY, FULL_DAY)

#: Q-07 — a reason is required for casual and sick leave, optional for work
#: from home. Sick and casual are absences someone else has to plan around;
#: work from home is not.
REASON_REQUIRED = {"wfh": False, "casual": True, "sick": True}

#: §6.1 — casual leave is planned (a family event, an appointment) and may not
#: be filed for today. Sick leave is by nature unplanned and must be. The
#: system must not force the same notice rules on both.
ALLOWS_SAME_DAY = {"wfh": True, "casual": False, "sick": True}

#: §6.1 — sick leave cannot be booked in advance; nobody knows they will be ill
#: next Tuesday. A future-dated sick day is almost always a mis-tap on casual.
ALLOWS_FUTURE = {"wfh": True, "casual": True, "sick": False}

#: §6.4 — the states that draw down an allowance. Everything else (rejected,
#: withdrawn, released, unrecognised) returns it.
CONSUMING_STATES = frozenset({"pending", "approved"})

#: Wider than CONSUMING_STATES: an unrecognised day costs nothing but still
#: occupies the date, so a real booking may not be filed alongside it. Mirrors
#: the partial unique index `bookings_one_per_day`.
OCCUPYING_STATES = frozenset({"pending", "approved", "unrecognised"})

#: §6.4 — which transitions the state machine permits. A booking that has
#: already been rejected, withdrawn or released is terminal; re-deciding it
#: would silently resurrect an allowance charge.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"approved", "rejected", "withdrawn", "released"}),
    "approved": frozenset({"withdrawn", "released"}),
    "rejected": frozenset(),
    "withdrawn": frozenset(),
    "released": frozenset(),
    "unrecognised": frozenset(),
}


@dataclass(frozen=True)
class BookingRequest:
    """What someone is asking for. Deliberately not the database row."""

    day: date
    category: str
    duration: Decimal
    reason: str | None


# ---------------------------------------------------------------------------
# The lock window — §6.3, FR-BOOK-08, NFR-04
# ---------------------------------------------------------------------------


def is_locked(booking_day: date, today: date | None = None) -> bool:
    """Has this booking's own date passed?

    §6.3, and the rule the product's integrity rests on. A booking is editable
    throughout the day it applies to and locked from the next day onward, so
    the boundary is the end of `booking_day` in Asia/Kolkata — 23:59:59, as
    Q-03 settles it.

    Expressed as a date comparison rather than a timestamp comparison on
    purpose: "has the calendar rolled past this day" is exactly the question,
    and phrasing it that way makes the +05:30 offset impossible to get wrong.

    `today` is injectable so tests can pin it. Callers in the application MUST
    NOT pass a client-supplied value (NFR-04) — the default is the server's own
    clock, which is the only one that may decide this.
    """
    return booking_day < (today or today_in_company_tz())


def approval_locks_booking() -> bool:
    """Q-03, second half: does approval close the window early?

    No. A lead approving a request does not shorten the requester's own
    correction window — if Devansh marks WFH, gets approved at 10:00, and then
    comes into the office, he can still withdraw it that evening. Approval
    records agreement; it does not freeze the day.
    """
    return False


# ---------------------------------------------------------------------------
# Individual checks. Each returns None when it passes.
# ---------------------------------------------------------------------------


def check_category(category: str) -> str | None:
    if category not in CATEGORIES:
        return f"Unknown category {category!r}."
    return None


def check_duration(duration: Decimal) -> str | None:
    """FR-BOOK-02 / §6.2 — full day or half day, nothing else."""
    if duration not in VALID_DURATIONS:
        return "A booking must be a full day (1.0) or a half day (0.5)."
    return None


def check_reason(category: str, reason: str | None) -> str | None:
    """FR-BOOK-03, Q-07."""
    if REASON_REQUIRED.get(category) and not (reason or "").strip():
        return f"{CATEGORY_LABELS.get(category, category)} needs a reason."
    return None


def check_bookable_day(
    day: date,
    *,
    holiday_name: str | None,
) -> str | None:
    """FR-BOOK-04 — no bookings on a holiday or a weekend.

    Both cost nothing (§6.2), so booking one would draw down an allowance for a
    day the person was never expected to work.
    """
    if holiday_name:
        return f"{day.isoformat()} is {holiday_name}, a company holiday."
    if is_weekend(day):
        return f"{day.isoformat()} is a weekend."
    return None


def check_timing(
    day: date,
    category: str,
    *,
    today: date,
    max_future_days: int,
) -> str | None:
    """FR-BOOK-08/09/10 and §6.1 — when each category may be filed for."""
    if is_locked(day, today):
        return (
            f"{day.isoformat()} has passed and is locked. "
            "Bookings can only be changed on or before the day they apply to."
        )

    if day == today and not ALLOWS_SAME_DAY[category]:
        return (
            f"{CATEGORY_LABELS[category]} must be requested before the day itself. "
            "If you are unwell today, mark it as sick leave."
        )

    if day > today:
        if not ALLOWS_FUTURE[category]:
            return f"{CATEGORY_LABELS[category]} can only be marked for today."
        if (day - today).days > max_future_days:
            # A-14. Unbounded future booking consumes allowance for periods no
            # admin has configured yet.
            return f"Bookings cannot be made more than {max_future_days} days ahead."

    return None


def check_allowance(
    remaining: Decimal,
    duration: Decimal,
    category: str,
    *,
    allow_excess: bool,
) -> str | None:
    """FR-BOOK-05, Q-08 — do not let a balance go below zero.

    `remaining` must already exclude any booking being replaced, otherwise
    changing a full day to a half day on the same date would be refused for
    lack of allowance the person is about to give back.
    """
    if allow_excess:
        return None
    if duration > remaining:
        label = CATEGORY_LABELS.get(category, category)
        return (
            f"Not enough {label.lower()} left: {_days(remaining)} remaining, "
            f"{_days(duration)} requested."
        )
    return None


def check_backfill(
    day: date,
    category: str,
    reason: str | None,
    *,
    holiday_name: str | None,
    today: date,
) -> str | None:
    """Rules for an admin recording leave that was already taken — spec A-21.

    A deliberately separate function from `validate_booking`, not a flag on it.
    This is the one sanctioned way past the lock in §6.3, and it must be
    impossible to reach by accident from the ordinary booking path — a shared
    code path with a `skip_lock=True` argument is exactly how such an exception
    stops being an exception.

    What still holds:

    * **Past dates only.** Today and the future are ordinary bookings the
      person makes themselves; there is no reason for an admin to enter one on
      their behalf, and allowing it would turn this into a general
      book-for-anyone power.
    * Weekends and holidays are still refused — they cost nothing (§6.2), so a
      backfill on one records a charge that never existed.
    * A reason is still required for casual and sick leave (FR-BOOK-03).

    What deliberately does NOT hold: the allowance check. A backfill records
    what already happened, and refusing to record it because the allowance is
    short would leave the ledger describing a month that did not occur. The
    resulting balance may go negative, and the ledger reports that honestly.
    """
    for failure in (
        check_category(category),
        check_reason(category, reason),
        check_bookable_day(day, holiday_name=holiday_name),
    ):
        if failure:
            return failure

    if day >= today:
        return (
            f"{day.isoformat()} has not passed yet. A backfill records leave that was "
            "already taken; anything from today onwards is booked by the person themselves."
        )

    return None


def check_transition(current: str, target: str) -> str | None:
    """§6.4 — is this state change one the machine permits?"""
    allowed = ALLOWED_TRANSITIONS.get(current)
    if allowed is None:
        return f"Unknown booking state {current!r}."
    if target not in allowed:
        return f"{_article(current).capitalize()} {current} booking cannot become {target}."
    return None


def _article(word: str) -> str:
    """ "a" or "an". Only ever applied to the six state names in §6.4."""
    return "an" if word[0] in "aeiou" else "a"


# ---------------------------------------------------------------------------


def validate_booking(
    request: BookingRequest,
    *,
    holiday_name: str | None,
    remaining: Decimal,
    today: date,
    max_future_days: int,
    allow_excess: bool,
) -> str | None:
    """Every rule that governs creating or changing a booking, in order.

    Returns the first failure, or None if the booking is allowed. Order is
    deliberate: shape errors first, then the calendar, then timing, and only
    then the allowance — so someone booking a Sunday is told it is a Sunday
    rather than being told their balance is short.
    """
    for failure in (
        check_category(request.category),
        check_duration(request.duration),
        check_reason(request.category, request.reason),
        check_bookable_day(request.day, holiday_name=holiday_name),
        check_timing(
            request.day,
            request.category,
            today=today,
            max_future_days=max_future_days,
        ),
        check_allowance(remaining, request.duration, request.category, allow_excess=allow_excess),
    ):
        if failure:
            return failure
    return None


def _days(value: Decimal) -> str:
    """Format a day count the way the product talks about them: 0.5, 1, 2.5."""
    normalised = value.normalize()
    text = format(normalised, "f")
    return f"{text} day" if normalised == 1 else f"{text} days"
