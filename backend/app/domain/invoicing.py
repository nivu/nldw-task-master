"""Client invoicing — spec 006 FR-MILE-05..07.

Where each milestone stands between "agreed" and "banked", and what is owed.
Pure: hand it the milestone, today and the payment terms, get the verdict.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

UPCOMING, DUE, OVERDUE = "upcoming", "due", "overdue"
INVOICED, PAYMENT_OVERDUE, PAID = "invoiced", "payment_overdue", "paid"
STATUSES = (UPCOMING, DUE, OVERDUE, INVOICED, PAYMENT_OVERDUE, PAID)

#: A milestone due within this many days is "due" rather than "upcoming".
DUE_SOON_DAYS = 7
#: The "due next 30 days" total looks this far ahead.
RECEIVABLE_HORIZON_DAYS = 30
ZERO = Decimal("0")


def _day(value: date | str | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(value)


def payment_due_on(milestone: dict, terms_days: int) -> date | None:
    """The day the client should have paid by: invoice date plus terms."""
    invoiced_on = _day(milestone.get("invoiced_on"))
    return None if invoiced_on is None else invoiced_on + timedelta(days=terms_days)


def invoice_status(milestone: dict, today: date, terms_days: int) -> str:
    """FR-MILE-06. Paid wins; then invoiced (overdue once past its terms);
    otherwise judged by its due date."""
    if _day(milestone.get("paid_on")) is not None:
        return PAID
    pay_by = payment_due_on(milestone, terms_days)
    if pay_by is not None:
        return PAYMENT_OVERDUE if pay_by < today else INVOICED
    due_on = _day(milestone["due_on"])
    if due_on < today:
        return OVERDUE
    if due_on <= today + timedelta(days=DUE_SOON_DAYS):
        return DUE
    return UPCOMING


def days_overdue(milestone: dict, today: date, terms_days: int) -> int:
    """Days past the due date (not yet invoiced) or past the payment terms
    (invoiced, not paid). Zero for everything else."""
    status = invoice_status(milestone, today, terms_days)
    if status == OVERDUE:
        return (today - _day(milestone["due_on"])).days
    if status == PAYMENT_OVERDUE:
        return (today - payment_due_on(milestone, terms_days)).days
    return 0


def check_payment(invoiced_on: date | str | None, paid_on: date | str | None) -> str | None:
    """FR-MILE-05 — a milestone is paid only once invoiced, and not before it."""
    invoiced_on, paid_on = _day(invoiced_on), _day(paid_on)
    if paid_on is None:
        return None
    if invoiced_on is None:
        return "A milestone can be marked paid only once it is invoiced."
    if paid_on < invoiced_on:
        return "A milestone cannot be paid before the day it was invoiced."
    return None


def totals(milestones: list[dict], today: date, terms_days: int) -> dict[str, Decimal]:
    """FR-MILE-07 — receivable (invoiced, not paid), the overdue part of it,
    what falls due in the next 30 days and is not yet invoiced, and what was
    paid in today's month."""
    out = {
        "receivable": ZERO,
        "overdue_receivable": ZERO,
        "due_next_30_days": ZERO,
        "paid_this_month": ZERO,
    }
    horizon = today + timedelta(days=RECEIVABLE_HORIZON_DAYS)
    for m in milestones:
        amount = Decimal(str(m["amount"]))
        status = invoice_status(m, today, terms_days)
        if status in (INVOICED, PAYMENT_OVERDUE):
            out["receivable"] += amount
        if status == PAYMENT_OVERDUE:
            out["overdue_receivable"] += amount
        if status in (UPCOMING, DUE) and _day(m["due_on"]) <= horizon:
            out["due_next_30_days"] += amount
        paid_on = _day(m.get("paid_on"))
        if paid_on is not None and (paid_on.year, paid_on.month) == (today.year, today.month):
            out["paid_this_month"] += amount
    return out
