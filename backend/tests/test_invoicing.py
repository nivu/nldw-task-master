"""Client invoicing tracker — spec 006 FR-MILE-05..07."""

from datetime import date
from decimal import Decimal as D

import pytest

from app.domain import invoicing

TODAY = date(2026, 10, 15)
TERMS = 30


def m(due_on="2026-12-01", invoiced_on=None, paid_on=None, amount="1000"):
    return {"due_on": due_on, "invoiced_on": invoiced_on, "paid_on": paid_on, "amount": amount}


class TestInvoiceStatus:
    def test_far_off_is_upcoming(self):
        assert invoicing.invoice_status(m("2026-10-23"), TODAY, TERMS) == "upcoming"

    @pytest.mark.parametrize("due_on", ["2026-10-15", "2026-10-18", "2026-10-22"])
    def test_today_or_within_seven_days_is_due(self, due_on):
        assert invoicing.invoice_status(m(due_on), TODAY, TERMS) == "due"

    def test_past_due_and_not_invoiced_is_overdue(self):
        assert invoicing.invoice_status(m("2026-10-14"), TODAY, TERMS) == "overdue"

    def test_invoiced_within_terms_is_invoiced(self):
        # Paid by 2026-10-15, which is today: not yet late.
        row = m("2026-09-10", invoiced_on="2026-09-15")
        assert invoicing.invoice_status(row, TODAY, TERMS) == "invoiced"

    def test_invoiced_past_terms_is_payment_overdue(self):
        row = m("2026-09-01", invoiced_on="2026-09-14")
        assert invoicing.invoice_status(row, TODAY, TERMS) == "payment_overdue"

    def test_terms_are_the_setting(self):
        row = m("2026-09-01", invoiced_on="2026-09-14")
        assert invoicing.invoice_status(row, TODAY, 45) == "invoiced"

    def test_paid_wins_over_everything(self):
        row = m("2026-01-01", invoiced_on="2026-01-02", paid_on="2026-06-01")
        assert invoicing.invoice_status(row, TODAY, TERMS) == "paid"

    def test_accepts_dates_as_well_as_strings(self):
        row = m(date(2026, 10, 1), invoiced_on=date(2026, 10, 1))
        assert invoicing.invoice_status(row, TODAY, TERMS) == "invoiced"


class TestDaysOverdue:
    def test_counts_from_the_due_date_when_not_invoiced(self):
        assert invoicing.days_overdue(m("2026-10-05"), TODAY, TERMS) == 10

    def test_counts_from_the_end_of_terms_when_invoiced(self):
        row = m("2026-09-01", invoiced_on="2026-09-10")
        assert invoicing.days_overdue(row, TODAY, TERMS) == 5

    @pytest.mark.parametrize(
        "row",
        [
            m("2026-10-20"),
            m("2026-11-30"),
            m("2026-10-01", invoiced_on="2026-10-01"),
            m("2026-01-01", invoiced_on="2026-01-01", paid_on="2026-03-01"),
        ],
    )
    def test_zero_otherwise(self, row):
        assert invoicing.days_overdue(row, TODAY, TERMS) == 0


class TestCheckPayment:
    def test_not_paid_is_fine(self):
        assert invoicing.check_payment(None, None) is None

    def test_paid_needs_an_invoice(self):
        assert "invoiced" in invoicing.check_payment(None, "2026-10-01")

    def test_paid_before_invoiced_is_refused(self):
        assert invoicing.check_payment("2026-10-02", "2026-10-01") is not None

    def test_paid_on_or_after_invoice_is_fine(self):
        assert invoicing.check_payment("2026-10-01", "2026-10-01") is None
        assert invoicing.check_payment(date(2026, 10, 1), date(2026, 10, 9)) is None


class TestTotals:
    def test_each_total(self):
        rows = [
            m("2026-10-20", amount="100"),  # due — next 30 days
            m("2026-11-14", amount="200"),  # upcoming, inside 30 days
            m("2026-11-15", amount="400"),  # upcoming, past 30 days
            m("2026-10-01", amount="800"),  # overdue, not invoiced — in none
            m("2026-10-01", invoiced_on="2026-10-01", amount="1600"),  # receivable
            m("2026-08-01", invoiced_on="2026-08-01", amount="3200"),  # overdue receivable
            m("2026-08-01", invoiced_on="2026-08-01", paid_on="2026-10-02", amount="6400"),
            m("2026-08-01", invoiced_on="2026-08-01", paid_on="2026-09-30", amount="9999"),
        ]
        assert invoicing.totals(rows, TODAY, TERMS) == {
            "receivable": D("4800"),
            "overdue_receivable": D("3200"),
            "due_next_30_days": D("300"),
            "paid_this_month": D("6400"),
        }

    def test_nothing_is_all_zero(self):
        assert set(invoicing.totals([], TODAY, TERMS).values()) == {D("0")}
