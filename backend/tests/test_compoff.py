from datetime import date
from decimal import Decimal as D

from app.domain import compoff

TODAY = date(2026, 9, 12)


class TestClaim:
    def test_weekend_work_is_claimable(self):
        assert (
            compoff.check_claim(
                date(2026, 9, 6), today=TODAY, is_weekend=True, holiday_name=None, days=D("1")
            )
            is None
        )

    def test_a_working_day_is_not(self):
        assert "working day" in compoff.check_claim(
            date(2026, 9, 8), today=TODAY, is_weekend=False, holiday_name=None, days=D("1")
        )

    def test_a_holiday_is(self):
        assert (
            compoff.check_claim(
                date(2026, 9, 8), today=TODAY, is_weekend=False, holiday_name="Onam", days=D("0.5")
            )
            is None
        )

    def test_the_future_is_not(self):
        assert "still to come" in compoff.check_claim(
            date(2026, 9, 13), today=TODAY, is_weekend=True, holiday_name=None, days=D("1")
        )


class TestCredits:
    def credit(self, days, expires, status="approved"):
        return {"days": days, "expires_on": expires, "status": status}

    def test_expiry_is_valid_days_after_approval(self):
        assert compoff.expiry(date(2026, 9, 12), 90) == date(2026, 12, 11)

    def test_lapsed_and_pending_are_not_usable(self):
        assert not compoff.usable(self.credit("1", "2026-09-11"), TODAY)
        assert not compoff.usable(self.credit("1", "2026-12-01", "pending"), TODAY)
        assert compoff.usable(self.credit("1", "2026-09-12"), TODAY)

    def test_oldest_credits_are_consumed_first(self):
        credits = [
            self.credit("0.5", "2026-12-01"),
            self.credit("1", "2026-10-01"),
            self.credit("1", "2026-09-01"),
        ]
        chosen = compoff.pick_credits(credits, D("1"), TODAY)
        assert [c["expires_on"] for c in chosen] == ["2026-10-01"]

    def test_short_is_none_not_partial(self):
        assert compoff.pick_credits([self.credit("0.5", "2026-12-01")], D("1"), TODAY) is None
