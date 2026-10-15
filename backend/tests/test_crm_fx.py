"""Deal currency — spec 007 FR-CRM-FX-01, -03, -04; Q-59; spec §11 AC "A
non-INR amount stores amount in INR…".

The conversion is pure (`domain/crm/fx.py`); the routes store it on every
save that sets an amount or currency, at the save month's rate or the latest
earlier one, refuse a save with no rate, and never recompute when a rate is
edited later. Totals and weighted figures use the stored INR value.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from crm_fake import OWNER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.domain.crm import fx
from app.services.crm import deals

ACADEMY = "?workspace=academy"
RATES = [
    {"currency": "USD", "month": "2026-08-01", "rate": "83.10"},
    {"currency": "USD", "month": "2026-10-01", "rate": "84.12"},
    {"currency": "AUD", "month": "2026-09-01", "rate": 55.5},
]


class TestRateFor:
    def test_inr_is_one(self):
        assert fx.rate_for([], "INR", date(2026, 10, 14)) == fx.Rate(Decimal(1), date(2026, 10, 1))

    def test_the_save_months_rate(self):
        assert fx.rate_for(RATES, "USD", date(2026, 10, 31)).rate == Decimal("84.12")

    def test_else_the_latest_earlier_month(self):
        found = fx.rate_for(RATES, "USD", date(2026, 9, 15))
        assert (found.rate, found.month) == (Decimal("83.10"), date(2026, 8, 1))
        assert fx.rate_for(RATES, "AUD", date(2027, 1, 2)).month == date(2026, 9, 1)

    def test_a_later_months_rate_does_not_count(self):
        assert fx.rate_for(RATES, "USD", date(2026, 7, 31)) is None

    def test_no_rate_at_all(self):
        assert fx.rate_for(RATES, "GBP", date(2026, 10, 1)) is None

    def test_the_refusal_names_currency_month_and_workspace(self):
        assert fx.missing_rate("USD", date(2026, 7, 3), "Nunnari Labs") == (
            "No USD rate for 2026-07 or any earlier month in Nunnari Labs; a workspace admin "
            "sets it in Settings."
        )


class TestToInr:
    """`to_inr` equals Postgres `round(amount * rate, 2)` (030's CHECK)."""

    @pytest.mark.parametrize(
        ("amount", "rate", "inr"),
        [
            ("1.00", "1", "1.00"),
            ("0.01", "0.5", "0.01"),  # 0.005 rounds up, half away from zero
            ("0.01", "0.499999", "0.00"),
            ("100.05", "84.125", "8416.71"),  # 8416.70625
            ("2.50", "0.333333", "0.83"),  # 0.8333325
            ("999999999999.99", "999999.999999", "999999999998990000.00"),  # exact product
            ("123.45", "83.335", "10287.71"),  # 10287.70575
        ],
    )
    def test_edge_cases(self, amount, rate, inr):
        assert fx.to_inr(amount, rate) == Decimal(inr)

    def test_converted_columns(self):
        assert fx.converted(RATES, "1000", "USD", date(2026, 10, 14), "Labs") == {
            "amount": "1000.00",
            "currency": "USD",
            "amount_in_inr": "84120.00",
            "fx_rate_used": "84.12",
            "fx_month": "2026-10-01",
        }

    def test_inr_at_one(self):
        out = fx.converted([], "500.5", "INR", date(2026, 10, 14), "Labs")
        assert (out["amount_in_inr"], out["fx_rate_used"]) == ("500.50", "1")


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    _, academy = two_workspaces(fake)
    fake.add_deal_lists(academy)
    monkeypatch.setattr(deals, "today_in_company_tz", lambda: date(2026, 10, 14))
    http = crm_client(monkeypatch, fake=fake)
    company = http.post(
        f"/api/v1/crm/companies{ACADEMY}", json={"name": "College"}, headers=as_(OWNER["id"])
    ).json()
    http.patch(
        f"/api/v1/crm/companies/{company['id']}{ACADEMY}",
        json={"lead_stage": "qualified"},
        headers=as_(OWNER["id"]),
    )
    return http, fake, academy, company


def _deal(http, company, **fields):
    body = {
        "name": "College - AI course",
        "company_id": company["id"],
        "next_step": "Call",
        "next_step_on": "2026-10-20",
        **fields,
    }
    return http.post(f"/api/v1/crm/opportunities{ACADEMY}", json=body, headers=as_(OWNER["id"]))


class TestStoredOnSave:
    """FR-CRM-FX-03, AC "A non-INR amount stores amount in INR"."""

    def test_inr_at_one(self, crm):
        http, *_, company = crm
        body = _deal(http, company, amount="250000", currency="INR").json()
        assert (body["amount_in_inr"], body["fx_rate_used"], body["fx_month"]) == (
            "250000.00",
            "1",
            "2026-10-01",
        )

    def test_usd_at_the_months_rate(self, crm):
        http, fake, academy, company = crm
        fake.add(
            "crm_fx_rates",
            workspace_id=academy["id"],
            currency="USD",
            month="2026-10-01",
            rate=84.12,
        )
        body = _deal(http, company, amount="1000", currency="USD").json()
        assert (body["amount"], body["currency"], body["amount_in_inr"]) == (
            "1000.00",
            "USD",
            "84120.00",
        )

    def test_else_the_latest_earlier_month(self, crm):
        http, fake, academy, company = crm
        fake.add(
            "crm_fx_rates", workspace_id=academy["id"], currency="AUD", month="2026-06-01", rate=55
        )
        body = _deal(http, company, amount="10", currency="AUD").json()
        assert (body["amount_in_inr"], body["fx_month"]) == ("550.00", "2026-06-01")

    def test_no_rate_is_refused_naming_it(self, crm):
        http, fake, *_ = crm
        r = _deal(http, crm[3], amount="10", currency="USD")
        assert r.status_code == 422
        assert r.json()["detail"] == (
            "No USD rate for 2026-10 or any earlier month in Nunnari Academy B2B; a workspace "
            "admin sets it in Settings."
        )
        assert fake.rows["crm_opportunities"] == {}

    def test_an_amount_needs_a_currency(self, crm):
        http, *_, company = crm
        r = _deal(http, company, amount="10")
        assert (r.status_code, r.json()["detail"]) == (422, "An amount needs a currency.")

    def test_a_currency_change_converts_again(self, crm):
        http, fake, academy, company = crm
        fake.add(
            "crm_fx_rates", workspace_id=academy["id"], currency="USD", month="2026-10-01", rate=84
        )
        deal = _deal(http, company, amount="100", currency="INR").json()
        r = http.patch(
            f"/api/v1/crm/opportunities/{deal['id']}{ACADEMY}",
            json={"currency": "USD"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert (r.json()["amount_in_inr"], r.json()["fx_rate_used"]) == ("8400.00", "84")

    def test_clearing_the_amount_clears_the_conversion(self, crm):
        http, *_, company = crm
        deal = _deal(http, company, amount="100", currency="INR").json()
        r = http.patch(
            f"/api/v1/crm/opportunities/{deal['id']}{ACADEMY}",
            json={"amount": None},
            headers=as_(OWNER["id"]),
        )
        body = r.json()
        assert (body["amount"], body["currency"], body["amount_in_inr"], body["fx_rate_used"]) == (
            None,
            None,
            None,
            None,
        )

    def test_editing_a_rate_does_not_recompute(self, crm):
        """Q-59."""
        http, fake, academy, company = crm
        rate = fake.add(
            "crm_fx_rates", workspace_id=academy["id"], currency="USD", month="2026-10-01", rate=84
        )
        deal = _deal(http, company, amount="100", currency="USD").json()
        r = http.put(
            f"/api/v1/crm/fx-rates/USD/2026-10{ACADEMY}",
            json={"rate": "90"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert fake.rows["crm_fx_rates"][rate["id"]]["rate"] == "90"
        again = http.get(
            f"/api/v1/crm/opportunities/{deal['id']}{ACADEMY}", headers=as_(OWNER["id"])
        ).json()
        assert again["record"]["amount_in_inr"] == "8400.00"

    def test_a_move_that_sets_an_amount_converts_it(self, crm):
        http, fake, academy, company = crm
        fake.add(
            "crm_fx_rates", workspace_id=academy["id"], currency="USD", month="2026-09-01", rate=80
        )
        person = http.post(
            f"/api/v1/crm/people{ACADEMY}", json={"first_name": "Priya"}, headers=as_(OWNER["id"])
        ).json()
        deal = _deal(http, company, point_of_contact_id=person["id"]).json()
        r = http.post(
            f"/api/v1/crm/opportunities/{deal['id']}/stage{ACADEMY}",
            json={
                "stage": "proposal",
                "changes": {"amount": "5", "currency": "USD", "expected_close_on": "2026-12-01"},
            },
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["opportunity"]["amount_in_inr"] == "400.00"


class TestTotalsUseInr:
    """FR-CRM-FX-04 — the weighted amount is INR × probability."""

    def test_weighted_amount(self, crm):
        http, fake, academy, company = crm
        fake.add(
            "crm_fx_rates",
            workspace_id=academy["id"],
            currency="USD",
            month="2026-10-01",
            rate=84.12,
        )
        body = _deal(http, company, amount="1000", currency="USD", probability=15).json()
        assert body["weighted_amount"] == "12618.00"
