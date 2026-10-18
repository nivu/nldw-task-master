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

The amount names are written out in `test_crm_redaction.py` and imported
from there, never from production (plan §5.5).
"""

from __future__ import annotations

import asyncio
import re
from datetime import date

import pytest
from crm_fake import OWNER, VIEWER, FakeCrmStore, as_, two_workspaces
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
