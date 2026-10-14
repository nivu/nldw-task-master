"""Viewer redaction — spec 007 FR-CRM-ACC-11, FR-CRM-AUD-05, FR-CRM-MCP-07;
plan §5.5; spec §11 AC "Viewers receive no amount field" (so far: every deal
read route and `crm_get`).

A recursive walk over each answer a Viewer gets, looking for any amount key,
and for any timeline change naming an amount field. The denylist is written
out here and never imported from `objects.AMOUNT_FIELDS`, so an omission in
production cannot hide itself (plan §5.5). Later slices add their routes
(lists, search, Today, exports) to the walk.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from crm_fake import OWNER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.mcp import crm as tools
from app.services.crm import deals

ACADEMY = "?workspace=academy"

#: Plan §5.5, written out.
AMOUNTS = {
    "amount",
    "amount_in_inr",
    "fx_rate_used",
    "won_fx_rate",
    "weighted_amount",
    "budget",
    "budget_in_inr",
    "budget_fx_rate",
    "pipeline_created",
    "won_value",
    "cost_per_converted",
    "revenue",
    "suggested_inr",
    "existing_tva_revenue",
}
AGGREGATES = {f"{fn}_{name}" for fn in ("sum", "avg", "min", "max") for name in AMOUNTS}


def _walk(value, path="$"):
    """Every amount key, and every timeline change of an amount field."""
    found = []
    if isinstance(value, dict):
        for key, inner in value.items():
            if key in AMOUNTS or key in AGGREGATES:
                found.append(f"{path}.{key}")
            if key == "field" and inner in AMOUNTS:
                found.append(f"{path}.field={inner}")
            found += _walk(inner, f"{path}.{key}")
    elif isinstance(value, list):
        for n, inner in enumerate(value):
            found += _walk(inner, f"{path}[{n}]")
    return found


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


@pytest.fixture
def crm(monkeypatch):
    """An Academy deal with an amount, moved and edited, so its timeline has
    amount changes; Devansh is a Viewer there."""
    fake = FakeCrmStore().install(monkeypatch)
    _, academy = two_workspaces(fake)
    fake.add_deal_lists(academy)
    fake.add_member(academy, VIEWER["id"], "viewer")
    fake.add(
        "crm_fx_rates", workspace_id=academy["id"], currency="USD", month="2026-10-01", rate=84
    )
    monkeypatch.setattr(deals, "today_in_company_tz", lambda: date(2026, 10, 14))
    http = crm_client(monkeypatch, fake=fake)
    me = as_(OWNER["id"])
    company = http.post(
        f"/api/v1/crm/companies{ACADEMY}", json={"name": "College"}, headers=me
    ).json()
    http.patch(
        f"/api/v1/crm/companies/{company['id']}{ACADEMY}",
        json={"lead_stage": "qualified"},
        headers=me,
    )
    person = http.post(
        f"/api/v1/crm/people{ACADEMY}",
        json={"first_name": "Priya", "company_id": company["id"]},
        headers=me,
    ).json()
    deal = http.post(
        f"/api/v1/crm/opportunities{ACADEMY}",
        json={
            "name": "College - AI course",
            "company_id": company["id"],
            "point_of_contact_id": person["id"],
            "amount": "1000",
            "currency": "USD",
            "next_step": "Call",
            "next_step_on": "2026-10-20",
        },
        headers=me,
    ).json()
    http.patch(
        f"/api/v1/crm/opportunities/{deal['id']}{ACADEMY}", json={"amount": "1200"}, headers=me
    )
    r = http.post(
        f"/api/v1/crm/opportunities/{deal['id']}/stage{ACADEMY}",
        json={"stage": "proposal", "changes": {"expected_close_on": "2026-12-01"}},
        headers=me,
    )
    assert r.status_code == 200, r.text
    return http, {"company": company, "person": person, "deal": deal}


def _reads(made):
    deal, company, person = made["deal"]["id"], made["company"]["id"], made["person"]["id"]
    return [
        f"/api/v1/crm/opportunities/{deal}{ACADEMY}",
        f"/api/v1/crm/opportunities{ACADEMY}",
        f"/api/v1/crm/opportunities/{deal}/timeline{ACADEMY}",
        f"/api/v1/crm/companies/{company}{ACADEMY}",
        f"/api/v1/crm/people/{person}{ACADEMY}",
    ]


class TestTheWalkFindsAmounts:
    """The walk itself works: an admin's answers do carry them."""

    def test_an_admin_sees_them(self, crm):
        http, made = crm
        found = []
        for url in _reads(made):
            found += _walk(http.get(url, headers=as_(OWNER["id"])).json())
        assert any(f.endswith(".amount_in_inr") for f in found)
        assert any(f.endswith(".weighted_amount") for f in found)
        assert any("field=amount" in f for f in found)


class TestAViewerGetsNone:
    @pytest.mark.parametrize("index", range(5))
    def test_every_deal_read_route(self, crm, index):
        http, made = crm
        url = _reads(made)[index]
        r = http.get(url, headers=as_(VIEWER["id"]))
        assert r.status_code == 200, r.text
        assert _walk(r.json()) == [], url

    def test_the_currency_stays(self, crm):
        """A currency code is not an amount (plan §5.5)."""
        http, made = crm
        page = http.get(_reads(made)[0], headers=as_(VIEWER["id"])).json()
        assert page["record"]["currency"] == "USD"

    def test_crm_get(self, crm):
        http, made = crm
        for record_type, key in (
            ("opportunity", "deal"),
            ("company", "company"),
            ("person", "person"),
        ):
            got = asyncio.run(
                tools.crm_get(Ctx(VIEWER["id"]), record_type, made[key]["id"], workspace="academy")
            )
            assert _walk(got) == [], record_type

    def test_a_viewers_move_preview_is_refused(self, crm):
        http, made = crm
        r = http.post(
            f"/api/v1/crm/opportunities/{made['deal']['id']}/stage/preview{ACADEMY}",
            json={"stage": "negotiation"},
            headers=as_(VIEWER["id"]),
        )
        assert r.status_code == 403
