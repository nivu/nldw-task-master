"""Viewer redaction — spec 007 FR-CRM-ACC-11, FR-CRM-AUD-05, FR-CRM-MCP-07;
plan §5.5; spec §11 AC "Viewers receive no amount field" (so far: every deal
read route, `crm_get`, and lists through `POST /crm/query` and `crm_list`
with their group and footer totals; from S9 the bell and the digests; from
S10 the Emails tab and `crm_get`'s emails; from S11a a campaign's budget
and figures, its list, timeline, search hits and member board).

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
from app.services.crm import digests as digest_service
from app.services.crm import views as view_service

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
    monkeypatch.setattr(view_service, "today_for", lambda user: date(2026, 10, 14))
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


#: Lists through POST /crm/query: the seeded kanban with its sum headers, a
#: table with footer totals, a grouped table.
QUERIES = [
    {"object": "opportunities", "view": "pipeline"},
    {"object": "opportunities", "view": "closing_this_month"},
    {"object": "opportunities", "spec": {"group_by": "stage", "aggregates": [{"fn": "count"}]}},
    {"object": "companies", "view": "companies_all"},
]


class TestListsAndTotals:
    """FR-CRM-VIEW-08 with FR-CRM-ACC-11 — group headers and footers carry
    no sum of an amount for a Viewer, whichever view asks for them."""

    def test_an_admin_gets_the_sums(self, crm):
        http, _ = crm
        r = http.post(f"/api/v1/crm/query{ACADEMY}", json=QUERIES[0], headers=as_(OWNER["id"]))
        found = _walk(r.json())
        assert any(f.endswith(".sum_amount_in_inr") for f in found)
        assert any(f.endswith(".sum_weighted_amount") for f in found)
        assert any(f.endswith(".amount") for f in found)

    @pytest.mark.parametrize("index", range(len(QUERIES)))
    def test_a_viewer_gets_none(self, crm, index):
        http, _ = crm
        r = http.post(f"/api/v1/crm/query{ACADEMY}", json=QUERIES[index], headers=as_(VIEWER["id"]))
        assert r.status_code == 200, r.text
        assert _walk(r.json()) == [], QUERIES[index]

    def test_the_view_list_a_viewer_reads_names_no_amount(self, crm):
        http, _ = crm
        r = http.get(f"/api/v1/crm/views{ACADEMY}", headers=as_(VIEWER["id"]))
        assert _walk(r.json()) == []

    def test_crm_list(self, crm):
        for view in ("pipeline", "Pipeline", None):
            got = asyncio.run(
                tools.crm_list(Ctx(VIEWER["id"]), "opportunity", view=view, workspace="academy")
            )
            assert got["rows"], view
            assert _walk(got) == [], view


class TestBellAndDigests:
    """FR-CRM-NOTIF-05 with FR-CRM-ACC-11 (S9) — the deal is $1,200 at 84,
    ₹1,00,800: neither figure reaches a Viewer through the bell or a digest."""

    FIGURES = ("1200", "1,200", "100800", "1,00,800")

    def _viewer_owns_the_deal(self, http, made):
        r = http.patch(
            f"/api/v1/crm/opportunities/{made['deal']['id']}{ACADEMY}",
            json={"owner_id": VIEWER["id"]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text

    def test_the_bell(self, crm):
        http, made = crm
        self._viewer_owns_the_deal(http, made)
        http.post(
            f"/api/v1/crm/opportunities/{made['deal']['id']}/stage{ACADEMY}",
            json={"stage": "negotiation", "changes": {"proposal_sent_on": "2026-10-12"}},
            headers=as_(OWNER["id"]),
        )
        r = http.get("/api/v1/crm/notifications", headers=as_(VIEWER["id"]))
        assert r.status_code == 200, r.text
        assert {n["kind"] for n in r.json()} == {"assigned", "stage_changed"}
        assert _walk(r.json()) == []
        assert not any(f in r.text for f in self.FIGURES)

    def test_the_daily_digest(self, crm):
        from datetime import UTC, datetime

        from app.services.crm import store

        http, made = crm
        self._viewer_owns_the_deal(http, made)
        now = datetime(2026, 10, 20, 3, 30, tzinfo=UTC)
        mine = [m for m in store.list_all_memberships() if m["user_id"] == VIEWER["id"]]
        workspaces = {w["id"]: w for w in store.list_workspaces()}
        viewer = {**VIEWER, "timezone": "Asia/Kolkata"}
        parts = digest_service.daily_parts(viewer, mine, workspaces, now)
        items = [i for p in parts for s in p["sections"] for i in s["items"]]
        assert [i["title"] for i in items] == ["College - AI course"]
        assert _walk(parts) == []
        body = digest_service.rules.daily_body("Devansh", now.date(), parts, "x")
        assert not any(f in body for f in self.FIGURES)
        # The same deal, for its admin, does carry it.
        owner = [m for m in store.list_all_memberships() if m["user_id"] == OWNER["id"]]
        admin_parts = digest_service.daily_parts(
            {**OWNER, "id": VIEWER["id"]},
            [{**m, "user_id": VIEWER["id"]} for m in owner],
            workspaces,
            now,
        )
        assert any(f.endswith(".amount") for f in _walk(admin_parts))


class TestEmails:
    """S10 — the Emails tab and `crm_get`'s emails, for a Viewer, carry
    subjects and participants only: no amount, and no body (Q-29)."""

    def test_a_viewer_reads_threads_with_no_amount(self, crm):
        from app.services.crm import store

        http, made = crm
        fake = store.get_record.__self__
        academy = next(w for w in store.list_workspaces() if w["key"] == "academy")
        mailbox = fake.add(
            "crm_mailboxes",
            workspace_id=academy["id"],
            email="owner@nunnari.academy",
            owner_id=OWNER["id"],
            labels=["Leads"],
            is_active=True,
        )
        msg = fake.add(
            "crm_email_messages",
            workspace_id=academy["id"],
            mailbox_id=mailbox["id"],
            gmail_message_id="g1",
            thread_id="t1",
            subject="Re: the proposal",
            sent_at="2026-10-13T09:00:00+00:00",
            direction="inbound",
            from_email="p@college.edu",
            participants=[{"email": "p@college.edu", "name": "Priya", "role": "from"}],
            is_internal=False,
            is_auto=False,
        )
        for record_type, key in (("opportunity", "deal"), ("company", "company")):
            fake.add(
                "crm_email_links",
                workspace_id=academy["id"],
                message_id=msg["id"],
                record_type=record_type,
                record_id=made[key]["id"],
            )
        tab = http.get(
            f"/api/v1/crm/opportunities/{made['deal']['id']}/emails{ACADEMY}",
            headers=as_(VIEWER["id"]),
        )
        assert tab.status_code == 200
        assert [t["subject"] for t in tab.json()["threads"]] == ["Re: the proposal"]
        assert _walk(tab.json()) == []
        assert "body" not in tab.text
        got = asyncio.run(
            tools.crm_get(Ctx(VIEWER["id"]), "opportunity", made["deal"]["id"], workspace="academy")
        )
        assert [t["thread_id"] for t in got["emails"]] == ["t1"]
        assert _walk(got) == []


class TestCampaigns:
    """S11a — a campaign's budget, its INR value and rate, and the figures
    derived from its members' deals (pipeline created, won value, cost per
    converted member) are absent for a Viewer everywhere (FR-CRM-ACC-11)."""

    @pytest.fixture
    def campaign(self, crm):
        http, made = crm
        me = as_(OWNER["id"])
        made_campaign = http.post(
            f"/api/v1/crm/campaigns{ACADEMY}",
            json={"name": "Webinars", "budget": "100", "budget_currency": "USD"},
            headers=me,
        ).json()
        cid = made_campaign["id"]
        http.patch(f"/api/v1/crm/campaigns/{cid}{ACADEMY}", json={"budget": "120"}, headers=me)
        http.post(
            f"/api/v1/crm/campaigns/{cid}/members{ACADEMY}",
            json={"person_ids": [made["person"]["id"]]},
            headers=me,
        )
        member = http.get(f"/api/v1/crm/campaigns/{cid}/members{ACADEMY}", headers=me).json()
        r = http.post(
            f"/api/v1/crm/campaign-members/{member['rows'][0]['id']}/convert{ACADEMY}",
            json={"opportunity_id": made["deal"]["id"]},
            headers=me,
        )
        assert r.status_code == 200, r.text
        return http, cid

    def _reads(self, cid):
        return [
            ("GET", f"/api/v1/crm/campaigns/{cid}{ACADEMY}", None),
            ("GET", f"/api/v1/crm/campaigns{ACADEMY}", None),
            ("GET", f"/api/v1/crm/campaigns/{cid}/timeline{ACADEMY}", None),
            ("GET", f"/api/v1/crm/campaigns/{cid}/members{ACADEMY}", None),
            ("GET", f"/api/v1/crm/search{ACADEMY}&q=webinars", None),
            ("POST", f"/api/v1/crm/query{ACADEMY}", {"object": "campaign", "spec": {}}),
        ]

    def test_an_admin_sees_them(self, campaign):
        http, cid = campaign
        record = http.get(f"/api/v1/crm/campaigns/{cid}{ACADEMY}", headers=as_(OWNER["id"])).json()[
            "record"
        ]
        assert record["budget_in_inr"] == "10080.00"
        assert record["pipeline_created"] is not None
        assert {"budget", "budget_fx_rate", "won_value", "cost_per_converted"} <= set(record)

    @pytest.mark.parametrize("index", range(6))
    def test_a_viewer_gets_none(self, campaign, index):
        http, cid = campaign
        method, url, body = self._reads(cid)[index]
        r = http.request(method, url, json=body, headers=as_(VIEWER["id"]))
        assert r.status_code == 200, r.text
        assert _walk(r.json()) == []

    def test_the_currency_and_counts_stay(self, campaign):
        http, cid = campaign
        record = http.get(
            f"/api/v1/crm/campaigns/{cid}{ACADEMY}", headers=as_(VIEWER["id"])
        ).json()["record"]
        assert record["budget_currency"] == "USD"
        assert record["member_counts"]["converted"] == 1
        assert record["conversion_rate"] == "100.0"

    def test_a_viewer_cannot_sort_by_budget(self, campaign):
        http, _ = campaign
        r = http.post(
            f"/api/v1/crm/query{ACADEMY}",
            json={"object": "campaign", "spec": {"sorts": [{"field": "budget", "dir": "desc"}]}},
            headers=as_(VIEWER["id"]),
        )
        assert r.status_code == 422

    def test_crm_get(self, campaign):
        _, cid = campaign
        answer = asyncio.run(tools.crm_get(Ctx(VIEWER["id"]), "campaign", cid, "academy"))
        assert answer["record"]["name"] == "Webinars"
        assert _walk(answer) == []


class TestDashboardAndExport:
    """S11b — a Viewer cannot open the dashboard or export at all, so no
    amount reaches them there (FR-CRM-ACC-11, FR-CRM-DASH-01, FR-CRM-IO-04);
    an admin's dashboard does carry amounts, so the refusal is what keeps
    them out."""

    @pytest.fixture
    def dash(self, crm, monkeypatch):
        from app.services.crm import dashboard

        monkeypatch.setattr(dashboard, "today_in_company_tz", lambda: date(2026, 10, 14))
        return crm

    def test_an_admins_dashboard_has_amounts(self, dash):
        http, _ = dash
        r = http.get(f"/api/v1/crm/dashboard{ACADEMY}", headers=as_(OWNER["id"]))
        assert r.json()["pipeline_by_stage"]["total"]["amount_in_inr"] == "100800.00"

    @pytest.mark.parametrize(
        ("method", "url", "body"),
        [
            ("GET", f"/api/v1/crm/dashboard{ACADEMY}", None),
            ("POST", f"/api/v1/crm/export/view{ACADEMY}", {"object": "opportunities"}),
            (
                "POST",
                f"/api/v1/crm/export/view{ACADEMY}",
                {"object": "opportunities", "ids": ["x"], "columns": ["name"]},
            ),
            ("GET", f"/api/v1/crm/export/workspace{ACADEMY}", None),
            ("GET", f"/api/v1/crm/targets{ACADEMY}", None),
        ],
    )
    def test_a_viewer_is_refused(self, dash, method, url, body):
        http, _ = dash
        r = http.request(method, url, json=body, headers=as_(VIEWER["id"]))
        assert r.status_code == 403, r.text
        assert _walk(r.json()) == []

    def test_crm_dashboard_as_a_viewer(self, dash):
        from app.mcp import server

        with pytest.raises(server.ToolError) as refused:
            asyncio.run(tools.crm_dashboard(Ctx(VIEWER["id"]), workspace="academy"))
        assert "Viewers have no dashboard." in str(refused.value)
