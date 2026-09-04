"""The balance ledger — FR-BAL, §7.3, and both sides of the open Q-02.

§7.3 requires balances to be derived rather than stored, and §6.2 warns that
integer-day arithmetic will be wrong. Both claims are tested here.

Q-02 (rolling vs pooling carry-forward) is still formally open. These tests
exercise both policies so that answering the question is a settings change with
known behaviour, not a discovery.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.ledger import (
    POOLING,
    ROLLING,
    Consumption,
    Grant,
    balance_for,
    consumed,
    resolve_allowance,
    year_history,
)


def grant(period, category="casual", days="2.0", user_id=None):
    return Grant(period=period, category=category, days=Decimal(days), user_id=user_id)


def used(period, category="casual", duration="1.0"):
    return Consumption(period=period, category=category, duration=Decimal(duration))


class TestHalfDays:
    """§6.2 — balances are stored and displayed to one decimal place."""

    def test_half_days_sum_exactly(self):
        """The reason Decimal is used rather than float.

        Ten halves are five days. In binary floating point 0.1-style errors
        accumulate; with Decimal the assertion below is exact, which is what a
        leave balance has to be.
        """
        entries = [used("2026-09", duration="0.5") for _ in range(10)]
        assert consumed(entries, "2026-09", "casual") == Decimal("5.0")

    def test_a_half_day_is_not_rounded_away(self):
        result = balance_for(
            [grant("2026-09", days="1.5")], [used("2026-09", duration="0.5")], "2026-09", "casual"
        )
        assert result.remaining == Decimal("1.0")
        assert result.used == Decimal("0.5")


class TestAllowanceResolution:
    """A-12 — the fallback chain, and why its third step exists."""

    def test_personal_override_beats_the_org_default(self):
        grants = [grant("2026-09", days="2.0"), grant("2026-09", days="5.0", user_id="u1")]
        assert resolve_allowance(grants, "2026-09", "casual") == Decimal("5.0")

    def test_org_default_for_the_exact_period_is_used(self):
        assert resolve_allowance([grant("2026-09", days="2.0")], "2026-09", "casual") == Decimal(
            "2.0"
        )

    def test_a_standing_policy_carries_into_later_months(self):
        """A-12 step 3, and the reason it is not optional.

        Without it, an admin who set allowances in July would find that nobody
        could book anything in August until they set them again — including
        booking *ahead* into September, which is exactly what casual leave is
        for.
        """
        grants = [grant("2026-07", days="2.0")]
        assert resolve_allowance(grants, "2026-11", "casual") == Decimal("2.0")

    def test_the_most_recent_earlier_grant_wins(self):
        grants = [grant("2026-01", days="1.0"), grant("2026-07", days="3.0")]
        assert resolve_allowance(grants, "2026-11", "casual") == Decimal("3.0")

    def test_a_later_grant_never_leaks_backwards(self):
        """FR-BAL-07 — changing an allowance must not disturb a closed period."""
        grants = [grant("2026-09", days="9.0")]
        assert resolve_allowance(grants, "2026-08", "casual") == Decimal("0.0")

    def test_unknown_category_is_zero_not_an_error(self):
        assert resolve_allowance([grant("2026-09")], "2026-09", "sick") == Decimal("0.0")


class TestCarryForward:
    """FR-BAL-05 — unused allowance carries forward rather than being forfeited."""

    def test_unused_days_are_not_forfeited_at_month_end(self):
        grants = [grant("2026-08", days="2.0"), grant("2026-09", days="2.0")]
        entries: list[Consumption] = []  # August entirely unused

        result = balance_for(grants, entries, "2026-09", "casual", policy=ROLLING)
        assert result.opening == Decimal("2.0")
        assert result.remaining == Decimal("4.0")

    def test_days_used_in_an_earlier_month_reduce_what_carries(self):
        grants = [grant("2026-08", days="2.0"), grant("2026-09", days="2.0")]
        entries = [used("2026-08", duration="1.5")]

        result = balance_for(grants, entries, "2026-09", "casual", policy=ROLLING)
        assert result.opening == Decimal("0.5")
        assert result.remaining == Decimal("2.5")

    def test_the_first_tracked_period_opens_at_zero(self):
        """A new joiner does not inherit a balance from before they existed."""
        result = balance_for([grant("2026-09", days="2.0")], [], "2026-09", "casual")
        assert result.opening == Decimal("0.0")
        assert result.remaining == Decimal("2.0")


class TestQ02BothPolicies:
    """Q-02 — rolling and pooling differ only in where accumulation starts.

    The scenario is the same in both: 1 day granted every month across a year
    boundary, none of it used.
    """

    GRANTS = [
        grant("2025-11", days="1.0"),
        grant("2025-12", days="1.0"),
        grant("2026-01", days="1.0"),
        grant("2026-02", days="1.0"),
    ]

    def test_rolling_carries_across_the_year_boundary(self):
        result = balance_for(self.GRANTS, [], "2026-02", "casual", policy=ROLLING)
        # Nov + Dec + Jan carried in, plus February's own grant.
        assert result.opening == Decimal("3.0")
        assert result.remaining == Decimal("4.0")

    def test_pooling_resets_each_january(self):
        result = balance_for(self.GRANTS, [], "2026-02", "casual", policy=POOLING)
        # Only January carries in; 2025 was left behind at the year boundary.
        assert result.opening == Decimal("1.0")
        assert result.remaining == Decimal("2.0")

    def test_the_two_policies_agree_within_a_single_year(self):
        """Which is why the question can stay open without blocking the build."""
        grants = [grant("2026-03", days="1.0"), grant("2026-04", days="1.0")]
        rolling = balance_for(grants, [], "2026-04", "casual", policy=ROLLING)
        pooling = balance_for(grants, [], "2026-04", "casual", policy=POOLING)
        assert rolling.remaining == pooling.remaining == Decimal("2.0")


class TestOnlyConsumingStatesCount:
    """§6.4 — only pending and approved consume; the rest return the days.

    The service layer filters before building Consumption entries, so what is
    asserted here is that the ledger counts everything it is given. The
    filtering itself is covered in test_booking_rules.py.
    """

    def test_every_entry_supplied_is_counted(self):
        entries = [used("2026-09", duration="0.5"), used("2026-09", duration="1.0")]
        assert consumed(entries, "2026-09", "casual") == Decimal("1.5")

    def test_other_categories_are_not_mixed_in(self):
        entries = [used("2026-09", category="sick"), used("2026-09", category="casual")]
        assert consumed(entries, "2026-09", "casual") == Decimal("1.0")
        assert consumed(entries, "2026-09", "sick") == Decimal("1.0")


class TestOverdraft:
    """A negative remaining is representable — it is just never reachable.

    FR-BOOK-05 blocks a booking that would take a balance below zero, so this
    state cannot be created by booking. It CAN arise if an admin lowers an
    allowance after days were taken, and the ledger must report that honestly
    rather than clamping to zero and hiding it.
    """

    def test_a_reduced_allowance_can_show_a_negative_balance(self):
        result = balance_for(
            [grant("2026-09", days="1.0")],
            [used("2026-09", duration="1.0"), used("2026-09", duration="0.5")],
            "2026-09",
            "casual",
        )
        assert result.remaining == Decimal("-0.5")


class TestYearHistory:
    """FR-BAL-08 — consumption history for the current year."""

    def test_every_month_is_present_even_when_empty(self):
        history = year_history([used("2026-03")], "2026", ["casual", "sick", "wfh"])
        assert len(history) == 12
        assert history["2026-03"]["casual"] == Decimal("1.0")
        assert history["2026-07"]["casual"] == Decimal("0.0")

    def test_other_years_are_excluded(self):
        history = year_history([used("2025-03")], "2026", ["casual"])
        assert all(value["casual"] == Decimal("0.0") for value in history.values())


@pytest.mark.parametrize("policy", [ROLLING, POOLING])
def test_balance_is_always_opening_plus_allowance_minus_used(policy):
    """The identity from §7.3, asserted directly under both policies."""
    grants = [grant("2026-08", days="2.0"), grant("2026-09", days="3.0")]
    entries = [used("2026-08", duration="0.5"), used("2026-09", duration="1.0")]

    result = balance_for(grants, entries, "2026-09", "casual", policy=policy)
    assert result.remaining == result.opening + result.allowance - result.used
