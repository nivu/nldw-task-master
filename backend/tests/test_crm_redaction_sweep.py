"""Viewer redaction, swept — spec 007 FR-CRM-ACC-11, FR-CRM-MCP-07, D3;
NFR-CRM-10; spec §11 AC "A Viewer receives no amount, weighted amount or
pipeline value anywhere, including MCP responses, search, kanban totals,
exports, notifications and digests; a Viewer filter or sort on an amount is
refused".

`test_crm_redaction.py` states the rule route by route; this file is
generated from `app.routes` and the field registry, so a route or an amount
field added later is swept without anyone remembering to list it:

- every CRM `GET` route, and every `POST` that only reads (a query, a
  duplicate check, a preview, an export, a dry run), called as Devansh, a
  Viewer in Labs, over a workspace whose deal and campaign carry amounts:
  a recursive walk finds no amount key and no timeline change of one. A
  refusal (403) passes, since it carries nothing.
- every amount field of every object, as a filter, a sort, a grouping and a
  total, is refused 422 for the Viewer, through the route and `crm_list`.
- every READ tool, as the Viewer.
- spec 009 FR-CRMAI-LLM-05, NFR-CRMAI-03, §15: with every AI job run on
  the spec's money fixtures, every Viewer route and read tool again, for
  none of the figures.

The amount names are written out in `test_crm_redaction.py` and imported
from there, never from production (plan §5.5).
"""

from __future__ import annotations

import asyncio
import re
from datetime import date

import pytest
from crm_fake import MEMBER, OWNER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client
from fastapi.routing import APIRoute, iter_route_contexts
from test_crm_redaction import AGGREGATES, AMOUNTS, _walk

from app.main import app
from app.mcp import crm as tools
from app.mcp import server
from app.services.crm import dashboard, deals, store
from app.services.crm import views as view_service

CRM = "/api/v1/crm/"
LABS = {"workspace": "labs"}

#: POST routes that only read: what they answer is walked too. Every other
#: POST writes, and is named in WRITES so that a new POST must be placed.
POST_READS = {
    "/api/v1/crm/query",
    "/api/v1/crm/companies/duplicates",
    "/api/v1/crm/people/duplicates",
    "/api/v1/crm/opportunities/{deal_id}/stage/preview",
    "/api/v1/crm/export/view",
    "/api/v1/crm/imports/dry-run",
}
WRITES = {
    "/api/v1/crm/workspaces/{key}/use",
    "/api/v1/crm/invitations",
    "/api/v1/crm/lists/{list_key}/options",
    "/api/v1/crm/companies/merge",
    "/api/v1/crm/campaigns/{campaign_id}/members",
    "/api/v1/crm/campaign-members/{member_id}/convert",
    "/api/v1/crm/links",
    "/api/v1/crm/tasks/{task_id}/complete",
    "/api/v1/crm/companies",
    "/api/v1/crm/people",
    "/api/v1/crm/opportunities",
    "/api/v1/crm/tasks",
    "/api/v1/crm/notes",
    "/api/v1/crm/campaigns",
    "/api/v1/crm/companies/{record_id}/restore",
    "/api/v1/crm/people/{record_id}/restore",
    "/api/v1/crm/opportunities/{record_id}/restore",
    "/api/v1/crm/tasks/{record_id}/restore",
    "/api/v1/crm/notes/{record_id}/restore",
    "/api/v1/crm/campaigns/{record_id}/restore",
    "/api/v1/crm/opportunities/{deal_id}/stage",
    "/api/v1/crm/opportunities/{deal_id}/handoff",
    "/api/v1/crm/opportunities/{deal_id}/link-project",
    "/api/v1/crm/opportunities/{deal_id}/contacts",
    "/api/v1/crm/views",
    "/api/v1/crm/favourites",
    "/api/v1/crm/bulk",
    "/api/v1/crm/imports/{run_id}/apply",
    "/api/v1/crm/csv-mappings",
    "/api/v1/crm/notifications/read",
    "/api/v1/crm/mailboxes",
    "/api/v1/crm/email-sync/run",
    "/api/v1/crm/tva-admin/workspaces/{workspace_id}/invitations",
    # Spec 009 S1b: deciding a Review Queue item.
    "/api/v1/crm/review/{item_id}/approve",
    "/api/v1/crm/review/{item_id}/dismiss",
    # Spec 009 S2b: Enrich selected.
    "/api/v1/crm/enrichment/run",
    # Spec 009 S3a: an AI field's definition; a record's recompute.
    "/api/v1/crm/ai-fields",
    "/api/v1/crm/companies/{record_id}/ai-fields/recompute",
    "/api/v1/crm/opportunities/{record_id}/ai-fields/recompute",
}
#: The TVA admin panel spans workspaces and holds no amount; a Viewer is
#: refused it anyway (test_crm_tva_admin.py).
EXEMPT_PREFIX = "/api/v1/crm/tva-admin/"


def _crm_routes() -> list[tuple[str, str]]:
    return sorted(
        (method, rc.path)
        for rc in iter_route_contexts(app.routes)
        if isinstance(rc.original_route, APIRoute) and (rc.path or "").startswith(CRM)
        for method in rc.original_route.methods
    )


GETS = [p for m, p in _crm_routes() if m == "GET" and not p.startswith(EXEMPT_PREFIX)]


@pytest.fixture
def crm(monkeypatch):
    """Labs: a $1,000 deal at Proposal owned by Devansh (a Viewer), with an
    open task of his due today, a campaign with a budget and a converted
    member, a note and a Drive link; he has been told of the deal."""
    fake = FakeCrmStore().install(monkeypatch)
    labs, _ = two_workspaces(fake)
    fake.add_deal_lists(labs)
    fake.add_campaign_lists(labs)
    fake.add("crm_fx_rates", workspace_id=labs["id"], currency="USD", month="2026-10-01", rate=84)
    monkeypatch.setattr(deals, "today_in_company_tz", lambda: date(2026, 10, 14))
    monkeypatch.setattr(view_service, "today_for", lambda user: date(2026, 10, 14))
    monkeypatch.setattr(dashboard, "today_in_company_tz", lambda: date(2026, 10, 14))
    monkeypatch.setattr(dashboard.capacity, "weekly", lambda start, end: [])
    http = crm_client(monkeypatch, fake=fake)
    me = as_(OWNER["id"])

    def call(method: str, path: str, body: dict | None = None) -> dict:
        r = http.request(method, f"/api/v1/crm/{path}", params=LABS, json=body, headers=me)
        assert r.status_code in (200, 201), (path, r.text)
        return r.json()

    company = call("POST", "companies", {"name": "Acme"})
    call("PATCH", f"companies/{company['id']}", {"lead_stage": "qualified"})
    person = call("POST", "people", {"first_name": "Priya", "company_id": company["id"]})
    deal = call(
        "POST",
        "opportunities",
        {
            "name": "Acme platform",
            "company_id": company["id"],
            "point_of_contact_id": person["id"],
            "amount": "1000",
            "currency": "USD",
            "next_step": "Call",
            "next_step_on": "2026-10-14",
        },
    )
    call("PATCH", f"opportunities/{deal['id']}", {"amount": "1200"})
    call(
        "POST",
        f"opportunities/{deal['id']}/stage",
        {"stage": "proposal", "changes": {"expected_close_on": "2026-12-01"}},
    )
    call("PATCH", f"opportunities/{deal['id']}", {"owner_id": VIEWER["id"]})
    links = [{"record_type": "opportunity", "record_id": deal["id"]}]
    task = call(
        "POST",
        "tasks",
        {"title": "Send deck", "links": links, "assignee_id": VIEWER["id"], "due_on": "2026-10-14"},
    )
    note = call("POST", "notes", {"body": "Priced at 1200.", "links": links})
    call(
        "POST",
        "links",
        {
            "record_type": "opportunity",
            "record_id": deal["id"],
            "url": "https://drive.google.com/p",
            "label": "Proposal",
        },
    )
    campaign = call(
        "POST", "campaigns", {"name": "Acme roundtable", "budget": "100", "budget_currency": "USD"}
    )
    call("POST", f"campaigns/{campaign['id']}/members", {"person_ids": [person["id"]]})
    member = call("GET", f"campaigns/{campaign['id']}/members")["rows"][0]
    call("POST", f"campaign-members/{member['id']}/convert", {"opportunity_id": deal["id"]})
    # Spec 009 S1c: a thread summary, phase 1 on; its money was masked
    # before it was stored (FR-CRMAI-LLM-05).
    fake.set_ai(labs, phases={"1": True})
    mailbox = fake.add(
        "crm_mailboxes",
        workspace_id=labs["id"],
        email="owner@nunnarilabs.com",
        owner_id=OWNER["id"],
        labels=["Clients"],
        is_active=True,
    )
    message = fake.add(
        "crm_email_messages",
        workspace_id=labs["id"],
        mailbox_id=mailbox["id"],
        gmail_message_id="g1",
        thread_id="t1",
        subject="Acme pricing",
        sent_at="2026-10-13T09:00:00+00:00",
        direction="inbound",
        from_email="priya@acme.in",
        participants=[{"email": "priya@acme.in", "name": "Priya", "role": "from"}],
        is_internal=False,
        is_auto=False,
    )
    for kind, record in (("company", company), ("opportunity", deal)):
        fake.add(
            "crm_email_links",
            workspace_id=labs["id"],
            message_id=message["id"],
            record_type=kind,
            record_id=record["id"],
        )
    summary = fake.add(
        "crm_email_summaries",
        workspace_id=labs["id"],
        mailbox_id=mailbox["id"],
        thread_id="t1",
        summary="Priya asked about the price of [amount]. We will send a revised quote.",
        asks=["A revised quote of [amount]"],
        commitments=[
            {"text": "Send the quote", "owner_id": None, "owner_unknown": True, "due_on": None}
        ],
        next_step="Send the quote",
        next_step_on="2026-10-20",
        sentiment="neutral",
        watermark_message_id="g1",
        watermark_at="2026-10-13T09:00:00+00:00",
        model="claude-sonnet-5-5",
        generated_at="2026-10-13T09:05:00+00:00",
        created_at="2026-10-13T09:05:00+00:00",
    )
    for kind, record in (("company", company), ("opportunity", deal)):
        fake.add(
            "crm_email_summary_links",
            workspace_id=labs["id"],
            summary_id=summary["id"],
            record_type=kind,
            record_id=record["id"],
        )
    made = {
        "companies": company,
        "people": person,
        "opportunities": deal,
        "tasks": task,
        "notes": note,
        "campaigns": campaign,
        "thread": {"id": f"{mailbox['id']}:t1"},
    }
    return http, made


def _url(path: str, made: dict) -> str:
    def value(m: re.Match) -> str:
        param = m.group(1)
        if param == "record_id":
            return made[path.removeprefix(CRM).split("/")[0]]["id"]
        return made[
            {"deal_id": "opportunities", "campaign_id": "campaigns", "thread_key": "thread"}[param]
        ]["id"]

    return re.sub(r"\{([^}]+)\}", value, path)


#: Bodies for the reading POSTs: every object's plain list with every total
#: the Viewer may ask for, the seeded views (the kanban's sums among them).
def _post_reads(made: dict) -> list[tuple[str, dict]]:
    objects = ("company", "person", "opportunity", "task", "note", "campaign", "campaign_member")
    deal = made["opportunities"]["id"]
    return (
        [("/api/v1/crm/query", {"object": o, "spec": {}}) for o in objects]
        + [
            ("/api/v1/crm/query", {"object": "opportunity", "view": v})
            for v in ("pipeline", "closing_this_month", "won_lost_quarter")
        ]
        + [
            (
                "/api/v1/crm/query",
                {
                    "object": "opportunity",
                    "spec": {"group_by": "stage", "aggregates": [{"fn": "count"}]},
                },
            ),
            ("/api/v1/crm/companies/duplicates", {"name": "Acme"}),
            ("/api/v1/crm/people/duplicates", {"name": "Priya"}),
            (f"/api/v1/crm/opportunities/{deal}/stage/preview", {"stage": "negotiation"}),
            ("/api/v1/crm/export/view", {"object": "opportunities"}),
            (
                "/api/v1/crm/imports/dry-run",
                {"format": "csv", "content": "Name\nX\n", "object": "companies"},
            ),
        ]
        # Spec 009 S4: the contact recency view field, a filter a Viewer
        # may use (it is not an amount).
        + [
            (
                "/api/v1/crm/query",
                {
                    "object": o,
                    "spec": {
                        "filters": {
                            "op": "and",
                            "items": [
                                {
                                    "field": "contact_recency",
                                    "operator": "any_of",
                                    "value": ["recent", "fading", "lapsed"],
                                }
                            ],
                        }
                    },
                },
            )
            for o in ("company", "person")
        ]
    )


class TestTheSweepCoversTheApi:
    def test_every_post_is_placed(self):
        posts = {p for m, p in _crm_routes() if m == "POST"}
        assert posts == POST_READS | WRITES

    def test_there_are_reads_to_sweep(self):
        assert len(GETS) > 40

    def test_the_walk_finds_an_admins_amounts(self, crm):
        http, made = crm
        found = []
        for path in GETS:
            r = http.get(_url(path, made), params=LABS, headers=as_(OWNER["id"]))
            if r.headers.get("content-type", "").startswith("application/json"):
                found += _walk(r.json())
        for name in ("amount", "amount_in_inr", "weighted_amount", "budget", "won_value"):
            assert any(f.endswith(f".{name}") for f in found), name


@pytest.mark.parametrize("path", GETS)
def test_every_get_as_a_viewer(crm, path):
    http, made = crm
    params = {**LABS, "q": "acme"} if path.endswith("/search") else LABS
    r = http.get(_url(path, made), params=params, headers=as_(VIEWER["id"]))
    assert r.status_code in (200, 403, 404), (path, r.status_code, r.text)
    if r.headers.get("content-type", "").startswith("application/json"):
        assert _walk(r.json()) == [], path
    else:
        # A file: nothing a Viewer may open is one (exports are refused).
        assert r.status_code == 403, path
    assert not any(figure in r.text for figure in ("100800", "1,00,800", "1200.00")), path


def test_every_post_read_as_a_viewer(crm):
    http, made = crm
    for path, body in _post_reads(made):
        r = http.post(path, params=LABS, json=body, headers=as_(VIEWER["id"]))
        assert r.status_code in (200, 403), (path, body, r.status_code, r.text)
        assert _walk(r.json()) == [], (path, body)


def _amount_fields() -> list[tuple[str, str]]:
    return sorted((f["object"], f["field"]) for f in store.list_object_fields() if f["is_amount"])


class TestAmountsCannotBeAskedFor:
    def test_the_registry_marks_exactly_the_written_out_amounts(self, crm):
        fields = store.list_object_fields()
        marked = {f["field"] for f in fields if f["is_amount"]}
        named = {f["field"] for f in fields} & AMOUNTS
        assert marked == named
        assert len(_amount_fields()) >= 8

    @pytest.mark.parametrize(
        "ask",
        [
            lambda f: {
                "filters": {"op": "and", "items": [{"field": f, "operator": "gt", "value": "1"}]}
            },
            lambda f: {"filters": {"op": "and", "items": [{"field": f, "operator": "is_empty"}]}},
            lambda f: {"sorts": [{"field": f, "dir": "desc"}]},
            lambda f: {"sorts": [{"field": f, "dir": "asc"}]},
            lambda f: {"group_by": f},
            lambda f: {"aggregates": [{"fn": "sum", "field": f}]},
            lambda f: {"aggregates": [{"fn": "max", "field": f}]},
            lambda f: {"aggregates": [{"fn": "count", "field": f}]},
            lambda f: {"columns": [f]},
        ],
        ids=["filter", "empty", "sort-desc", "sort-asc", "group", "sum", "max", "count", "column"],
    )
    def test_every_amount_field_every_way(self, crm, ask):
        http, _ = crm
        for obj, field in _amount_fields():
            r = http.post(
                "/api/v1/crm/query",
                params=LABS,
                json={"object": obj, "spec": ask(field)},
                headers=as_(VIEWER["id"]),
            )
            assert r.status_code == 422, (obj, field, r.status_code, r.text)
            assert _walk(r.json()) == []

    def test_through_crm_list(self, crm):
        for obj, field in _amount_fields():
            for kwargs in (
                {"filters": [{"field": field, "operator": "gt", "value": "1"}]},
                {"sort": [{"field": field, "dir": "desc"}]},
            ):
                with pytest.raises(server.ToolError) as refused:
                    asyncio.run(tools.crm_list(Ctx(VIEWER["id"]), obj, workspace="labs", **kwargs))
                assert str(refused.value).startswith("422"), (obj, field, kwargs)

    def test_an_admin_may(self, crm):
        http, _ = crm
        r = http.post(
            "/api/v1/crm/query",
            params=LABS,
            json={
                "object": "opportunity",
                "spec": {"sorts": [{"field": "amount_in_inr", "dir": "desc"}]},
            },
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


def _read_tools(made: dict) -> list[tuple[str, dict]]:
    gets = [
        ("crm_get", {"record_type": t, "id": made[plural]["id"]})
        for t, plural in (
            ("company", "companies"),
            ("person", "people"),
            ("opportunity", "opportunities"),
            ("task", "tasks"),
            ("note", "notes"),
            ("campaign", "campaigns"),
        )
    ]
    lists = [
        ("crm_list", {"object": o})
        for o in ("company", "person", "opportunity", "task", "note", "campaign", "campaign_member")
    ] + [("crm_list", {"object": "opportunity", "view": v}) for v in ("pipeline", "Pipeline")]
    return (
        [("crm_workspaces", {}), ("crm_today", {}), ("crm_search", {"query": "acme"})]
        + gets
        + lists
        # Spec 009 S1b: a Viewer has no queue (FR-CRMAI-REV-03).
        + [("crm_review_list", {})]
        # Spec 009 S1c: a Viewer reads a thread's summary (FR-CRMAI-SUM-08).
        + [("crm_thread_summary", {"thread_key": made["thread"]["id"]})]
    )


class TestEveryReadTool:
    def test_every_read_tool_is_swept(self, crm):
        _, made = crm
        reads = {
            t.name
            for t in asyncio.run(server.mcp.list_tools())
            if t.name.startswith("crm_") and t.annotations.read_only_hint
        }
        # crm_dashboard refuses a Viewer outright: below.
        assert reads - {n for n, _ in _read_tools(made)} == {"crm_dashboard"}

    def test_each_as_a_viewer(self, crm):
        _, made = crm
        for name, kwargs in _read_tools(made):
            if name != "crm_workspaces":
                kwargs = {**kwargs, "workspace": "labs"}
            answer = asyncio.run(getattr(tools, name)(Ctx(VIEWER["id"]), **kwargs))
            assert answer, (name, kwargs)
            assert _walk(answer) == [], (name, kwargs)

    def test_the_viewers_today_has_the_deal_without_its_amount(self, crm):
        _, made = crm
        answer = asyncio.run(tools.crm_today(Ctx(VIEWER["id"]), workspace="labs"))
        ids = [i.get("record_id") for s in answer["sections"] for i in s["items"]]
        assert made["opportunities"]["id"] in ids
        assert _walk(answer) == []

    def test_crm_dashboard_refuses_a_viewer(self, crm):
        with pytest.raises(server.ToolError) as refused:
            asyncio.run(tools.crm_dashboard(Ctx(VIEWER["id"]), workspace="labs"))
        assert str(refused.value).startswith("403")


def test_the_denylist_has_its_aggregates():
    """The walk also finds a total's key, `sum_amount_in_inr` and the like."""
    assert "sum_amount_in_inr" in AGGREGATES and "max_budget" in AGGREGATES


class TestContactRecency:
    """Spec 009 FR-CRMAI-REL-02: the contact recency view field is not an
    amount, so a Viewer may filter by it, and its answer carries none."""

    @pytest.mark.parametrize("obj", ["company", "person"])
    def test_a_viewer_filters_by_contact_recency(self, crm, obj):
        http, _ = crm
        r = http.post(
            "/api/v1/crm/query",
            params=LABS,
            json={
                "object": obj,
                "spec": {
                    "filters": {
                        "op": "and",
                        "items": [
                            {"field": "contact_recency", "operator": "is_empty"},
                        ],
                    }
                },
            },
            headers=as_(VIEWER["id"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["rows"]
        assert _walk(r.json()) == []

    def test_it_is_not_an_amount(self, crm):
        assert {
            (f["object"], f["is_amount"])
            for f in store.list_object_fields()
            if f["field"] == "contact_recency"
        } == {("company", False), ("person", False)}


#: Spec 009 §15: "₹4.5L, 12 lakh, 1.2 Cr, $20k, AUD 15,000 and 'budget of
#: 50k'", as a model might write them in every generated string.
MONEY = "₹4.5L, 12 lakh, 1.2 Cr, $20k, AUD 15,000 and a budget of 50k"
#: What would be left of them were any one not masked.
FIGURES = ("4.5L", "12 lakh", "1.2 Cr", "20k", "15,000", "50k")


def _figures(text: str) -> list[str]:
    return [f for f in FIGURES if f in text]


class Priced:
    """Labs with every phase on and a budget, where each AI job has run with
    a model answering the spec §15 money fixtures in every string it writes:
    a thread summary and its items (summaries), a meeting note and its items
    (meetings), Acme's enriched values (enrichment) and its and its deal's
    AI field values and reasons (AI fields). Devansh is a Viewer; the items
    are Deepika's. A task and a campaign so that every route resolves."""

    def __init__(self, monkeypatch) -> None:
        from crm_ai_fakes import FakeClaude, FakeFireflies, FakeWeb
        from test_crm_ai_fields_job import ByField
        from test_crm_summaries_job import NOW, Mail

        from app.services.crm import ai_fields, enrichment
        from app.tasks import crm_ai

        mail = Mail(monkeypatch)
        self.mail, self.fake, self.http, self.labs = mail, mail.fake, mail.http, mail.labs
        self.fake.add_campaign_lists(self.labs)
        self.fake.set_ai(
            self.labs, budget_usd=10, phases={"1": True, "2": True, "3": True, "4": True}
        )
        self.fake.seed_ai_fields(self.labs)
        monkeypatch.setattr(enrichment, "_now", lambda: NOW)
        monkeypatch.setattr(ai_fields, "_now", lambda: NOW)
        monkeypatch.setattr(deals, "today_in_company_tz", lambda: date(2026, 10, 18))
        monkeypatch.setattr(view_service, "today_for", lambda user: date(2026, 10, 18))
        monkeypatch.setattr(dashboard, "today_in_company_tz", lambda: date(2026, 10, 18))
        monkeypatch.setattr(dashboard.capacity, "weekly", lambda start, end: [])
        me = as_(OWNER["id"])

        # Summaries (phase 1).
        mail.message("g1", f"Priya: our budget is {MONEY}.")
        mail.answer(
            {
                "summary": f"Priya named {MONEY}. Deepika will send a quote.",
                "asks": [f"A quote of {MONEY}"],
                "commitments": [
                    {"text": f"Send the quote of {MONEY}", "owner": "1", "due_on": "2026-10-24"}
                ],
                "next_step": "Send the quote of $20k",
                "next_step_on": "2026-10-24",
                "sentiment": "neutral",
            }
        )
        assert mail.tick()["outcome"] == "ok"

        # Meetings (phase 2): Priya's address matches her to Acme.
        r = self.http.patch(
            f"/api/v1/crm/people/{mail.priya['id']}",
            params=LABS,
            json={"primary_email": "priya@acme.in"},
            headers=me,
        )
        assert r.status_code == 200, r.text
        FakeFireflies().install(monkeypatch).meeting(
            "m1",
            ["priya@acme.in", "deepika@nunnarilabs.com"],
            sentences=[("Priya", f"Our budget is {MONEY}.")],
        )
        FakeClaude(
            {
                "summary": f"Priya named {MONEY}.",
                "decisions": [f"Hold the fee at {MONEY}"],
                "action_items": [
                    {"text": f"Revise the quote to {MONEY}", "owner": "1", "due_on": "2026-10-24"}
                ],
                "next_step": "Send the revised quote of AUD 15,000",
                "next_step_on": "2026-10-25",
            }
        ).install(monkeypatch)
        assert crm_ai._meetings(NOW)["runs"]["labs"]["outcome"] == "ok"

        # Enrichment (phase 2): a website queues Acme; one batch reads it.
        queue: list[dict] = []
        monkeypatch.setattr(enrichment, "enqueue", lambda args, countdown: queue.append(args))
        monkeypatch.setattr(enrichment, "acquire_lock", lambda ws, token: True)
        monkeypatch.setattr(enrichment, "extend_lock", lambda ws, token: None)
        monkeypatch.setattr(enrichment, "release_lock", lambda ws, token: None)
        FakeWeb().install(monkeypatch).site(
            "acme.in", {"/": f"<html><body><p>Lathes from {MONEY}.</p></body></html>"}
        )
        r = self.http.patch(
            f"/api/v1/crm/companies/{mail.acme['id']}",
            params=LABS,
            json={"website": {"primary": {"url": "https://acme.in"}}},
            headers=me,
        )
        assert r.status_code == 200, r.text
        FakeClaude(
            default={
                "industry": None,
                "sub_industry": None,
                "employee_band": None,
                "city": None,
                "country": None,
                "description": f"Acme sells lathes at {MONEY}.",
                "linkedin": None,
                "products": ["Lathes at ₹4.5L", "Mills at $20k"],
            }
        ).install(monkeypatch)
        crm_ai._enrichment(NOW)
        while queue:
            crm_ai.enrichment_batch(queue.pop(0))

        # AI fields (phase 3).
        ByField(
            {
                "ICP fit": {"value": "Factory Brain"},
                "Account brief": {"value": f"Acme weighs {MONEY}."},
                "Deal outlook": {"value": "At risk", "reason": f"Stuck on {MONEY}."},
                "What is blocking": {"value": f"The budget of 50k and {MONEY}."},
            }
        ).install(monkeypatch)
        assert crm_ai._ai_fields(NOW)["runs"]["labs"]["outcome"] == "ok"

        task = self.http.post(
            "/api/v1/crm/tasks",
            params=LABS,
            json={
                "title": "Send deck",
                "links": [{"record_type": "opportunity", "record_id": mail.pilot["id"]}],
                "assignee_id": VIEWER["id"],
                "due_on": "2026-10-18",
            },
            headers=me,
        )
        assert task.status_code == 201, task.text
        campaign = self.http.post(
            "/api/v1/crm/campaigns", params=LABS, json={"name": "Acme roundtable"}, headers=me
        )
        assert campaign.status_code == 201, campaign.text
        [note] = [
            n
            for n in self.fake.rows["crm_notes"].values()
            if n["workspace_id"] == self.labs["id"] and n.get("source_ref")
        ]
        self.made = {
            "companies": mail.acme,
            "people": mail.priya,
            "opportunities": mail.pilot,
            "tasks": task.json(),
            "notes": note,
            "campaigns": campaign.json(),
            "thread": {"id": f"{mail.mailbox['id']}:t1"},
        }

    def stored(self) -> dict[str, list[str]]:
        """Every generated string each job stored, by table."""
        ws = self.labs["id"]

        def of(table: str) -> list[dict]:
            return [r for r in self.fake.rows[table].values() if r["workspace_id"] == ws]

        return {
            "crm_email_summaries": [str(r) for r in of("crm_email_summaries")],
            "crm_notes": [r["body"] for r in of("crm_notes") if r.get("source_ref")],
            "crm_review_items": [
                str((r["suggestion"], r.get("current_values"))) for r in of("crm_review_items")
            ],
            "crm_company_enrichment": [str(r.get("values")) for r in of("crm_company_enrichment")],
            "crm_ai_field_values": [
                f"{r.get('value_text')} {r.get('reason')}" for r in of("crm_ai_field_values")
            ],
        }


@pytest.fixture
def priced(monkeypatch) -> Priced:
    return Priced(monkeypatch)


class TestTheMoneyFixtures:
    """Spec 009 FR-CRMAI-LLM-05, NFR-CRMAI-03; spec §15 AC "With a fixture
    email and transcript holding ₹4.5L, 12 lakh, 1.2 Cr, $20k, AUD 15,000 and
    'budget of 50k', the Viewer routes and MCP responses for summaries,
    meeting notes, review items, enriched values and AI fields contain none
    of them": every Viewer route and read tool, over what each job wrote."""

    def test_money_every_job_stored_masked_text(self, priced):
        for table, texts in priced.stored().items():
            assert texts, table
            assert any("[amount]" in t for t in texts), (table, texts)
            assert [t for t in texts if _figures(t)] == [], table

    @pytest.mark.parametrize("path", GETS)
    def test_money_every_get_as_a_viewer(self, priced, path):
        params = {**LABS, "q": "acme"} if path.endswith("/search") else LABS
        r = priced.http.get(_url(path, priced.made), params=params, headers=as_(VIEWER["id"]))
        assert r.status_code in (200, 403, 404), (path, r.status_code, r.text)
        assert _figures(r.text) == [], path

    def test_money_every_post_read_as_a_viewer(self, priced):
        for path, body in _post_reads(priced.made):
            r = priced.http.post(path, params=LABS, json=body, headers=as_(VIEWER["id"]))
            assert r.status_code in (200, 403), (path, body, r.status_code, r.text)
            assert _figures(r.text) == [], (path, body)

    def test_money_every_read_tool_as_a_viewer(self, priced):
        for name, kwargs in _read_tools(priced.made):
            if name != "crm_workspaces":
                kwargs = {**kwargs, "workspace": "labs"}
            answer = asyncio.run(getattr(tools, name)(Ctx(VIEWER["id"]), **kwargs))
            assert _figures(str(answer)) == [], name

    def test_money_the_viewer_is_shown_the_masked_text(self, priced):
        """The sweep reads the generated text, not an empty answer."""
        made, viewer = priced.made, as_(VIEWER["id"])
        for path in (
            f"/api/v1/crm/email-threads/{made['thread']['id']}/summary",
            f"/api/v1/crm/notes/{made['notes']['id']}",
            f"/api/v1/crm/companies/{made['companies']['id']}",
            f"/api/v1/crm/opportunities/{made['opportunities']['id']}",
        ):
            r = priced.http.get(path, params=LABS, headers=viewer)
            assert r.status_code == 200, (path, r.text)
            assert "[amount]" in r.text, path
        summary = asyncio.run(
            tools.crm_thread_summary(
                Ctx(VIEWER["id"]), thread_key=made["thread"]["id"], workspace="labs"
            )
        )
        assert "[amount]" in str(summary)

    def test_money_review_items_for_their_owner(self, priced):
        """A Viewer has no queue (FR-CRMAI-REV-03); the items' owner reads
        them masked, through the route and `crm_review_list`."""
        r = priced.http.get("/api/v1/crm/review", params=LABS, headers=as_(MEMBER["id"]))
        assert r.status_code == 200, r.text
        assert r.json()["items"]
        assert "[amount]" in r.text
        assert _figures(r.text) == []
        listed = asyncio.run(tools.crm_review_list(Ctx(MEMBER["id"]), workspace="labs"))
        assert "[amount]" in str(listed)
        assert _figures(str(listed)) == []

    def test_money_the_check_finds_a_figure(self):
        assert _figures(f"Priya named {MONEY}.") == list(FIGURES)


class TestThe009Tools:
    """Spec 009 FR-CRMAI-MCP-01, FR-CRMAI-REV-03: the three new tools as a
    Viewer. The two reads are swept above; the decision is refused."""

    def test_the_three_tools_are_registered(self):
        names = {t.name for t in asyncio.run(server.mcp.list_tools())}
        assert {"crm_review_list", "crm_review_decide", "crm_thread_summary"} <= names

    def test_a_viewer_cannot_decide_an_item(self, priced):
        [item, *_] = [
            r
            for r in priced.fake.rows["crm_review_items"].values()
            if r["workspace_id"] == priced.labs["id"]
        ]
        before = {t: dict(rows) for t, rows in priced.fake.rows.items()}
        with pytest.raises(server.ToolError) as refused:
            asyncio.run(
                tools.crm_review_decide(
                    Ctx(VIEWER["id"]), item_id=item["id"], decision="approve", workspace="labs"
                )
            )
        assert str(refused.value).startswith("403"), str(refused.value)
        assert _figures(str(refused.value)) == []
        assert {t: dict(rows) for t, rows in priced.fake.rows.items()} == before

    def test_the_009_gets_are_swept(self):
        assert {
            "/api/v1/crm/ai",
            "/api/v1/crm/ai-fields",
            "/api/v1/crm/review",
            "/api/v1/crm/email-threads/{thread_key}/summary",
            "/api/v1/crm/jobs",
        } <= set(GETS)
