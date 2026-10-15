"""Workspace isolation, swept — spec 007 FR-CRM-WS-02, D3; NFR-CRM-10; spec
§11 AC "No query, search, view, dashboard, export or MCP call returns data
from a workspace the caller is not a member of; another workspace's record id
is answered 404".

`test_crm_isolation.py` states the rule object by object; this file is
generated from `app.routes`, so a route added later is swept without anyone
remembering to list it:

- every `/api/v1/crm/` route with an id in its path is called in Labs with
  an Academy id and must answer 404. Each path parameter is resolved to an
  Academy row by its name; a parameter this file cannot resolve, and is not
  exempt with its reason, fails the test.
- every read with no id in its path (lists, query, search, Today, exports,
  the dashboard, the bell) and the digest builders, answered for Labs, never
  carry Academy's marker string or any Academy record id.
- the same through every `crm_*` tool on the in-process bridge.

Academy's rows all carry the marker `Zqxacad` in their names and text, so a
leak shows up whichever field carries it.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, date, datetime

import pytest
from crm_fake import OUTSIDER, OWNER, SELLER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client
from fastapi.routing import APIRoute, iter_route_contexts

from app.main import app
from app.mcp import crm as tools
from app.mcp import server
from app.services.crm import dashboard, deals, store
from app.services.crm import digests as digest_service
from app.services.crm import views as view_service

CRM = "/api/v1/crm/"
MARK = "zqxacad"
LABS = {"workspace": "labs"}

#: Path parameters that do not name a workspace's row, with the reason.
EXEMPT_PARAMS = {
    # A setting's key and an FX rate's currency and month are the same in
    # every workspace, and so is a select list's key; the workspace query
    # parameter picks whose.
    "key",
    "list_key",
    "currency",
    "month",
}
#: Route prefixes that are not workspace-scoped, with the reason.
EXEMPT_PREFIXES = (
    # The TVA admin panel (FR-CRM-CHG-11) spans every workspace by design and
    # is guarded by the TVA admin role (test_crm_tva_admin.py).
    "/api/v1/crm/tva-admin/",
    # A workspace by key: a non-member is answered 404 in test_crm_guard.py.
    "/api/v1/crm/workspaces/{key}",
)


def _crm_routes() -> list[tuple[str, str]]:
    return sorted(
        (method, rc.path)
        for rc in iter_route_contexts(app.routes)
        if isinstance(rc.original_route, APIRoute) and (rc.path or "").startswith(CRM)
        for method in rc.original_route.methods
    )


def _params(path: str) -> list[str]:
    return re.findall(r"\{([^}]+)\}", path)


ID_ROUTES = [
    (m, p)
    for m, p in _crm_routes()
    if not p.startswith(EXEMPT_PREFIXES) and set(_params(p)) - EXEMPT_PARAMS
]
#: Reads with no id in the path: everything a list, a search, Today, an
#: export, the dashboard or the bell answers.
OPEN_READS = [
    p
    for m, p in _crm_routes()
    if m == "GET"
    and not _params(p)
    and not p.startswith(EXEMPT_PREFIXES)
    # Two company ids in its query: TestIdsInTheBodyOrQuery.
    and p != "/api/v1/crm/companies/merge-preview"
]
#: (path, caller) for every open read. The bell spans the person's
#: workspaces (contracts §2.10), so the owner's own rightly holds Academy's;
#: only Vinita's, who is in Labs alone, is checked there.
OPEN_READ_CALLS = [
    (path, who)
    for path in OPEN_READS
    for who in (OWNER["id"], OUTSIDER["id"])
    if not (path.startswith("/api/v1/crm/notifications") and who == OWNER["id"])
]


@pytest.fixture
def crm(monkeypatch):
    """Academy holds one of everything, marked; Vinita is an Admin of Labs
    only, the owner an Admin of both."""
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.add_deal_lists(academy)
    fake.add_campaign_lists(academy)
    fake.add_member(labs, OUTSIDER["id"], "admin")
    monkeypatch.setattr(deals, "today_in_company_tz", lambda: date(2026, 10, 14))
    monkeypatch.setattr(view_service, "today_for", lambda user: date(2026, 10, 14))
    monkeypatch.setattr(dashboard, "today_in_company_tz", lambda: date(2026, 10, 14))
    monkeypatch.setattr(dashboard.capacity, "weekly", lambda start, end: [])
    http = crm_client(monkeypatch, fake=fake)
    me = as_(OWNER["id"])
    a = "?workspace=academy"

    def post(path: str, body: dict) -> dict:
        r = http.post(f"/api/v1/crm/{path}{a}", json=body, headers=me)
        assert r.status_code in (200, 201), (path, r.text)
        return r.json()

    company = post("companies", {"name": "Zqxacad College", "domain": "zqxacad.edu"})
    http.patch(
        f"/api/v1/crm/companies/{company['id']}{a}", json={"lead_stage": "qualified"}, headers=me
    )
    person = post(
        "people",
        {"first_name": "Zqxacad", "company_id": company["id"], "primary_email": "p@zqxacad.edu"},
    )
    deal = post(
        "opportunities",
        {
            "name": "Zqxacad course",
            "company_id": company["id"],
            "point_of_contact_id": person["id"],
            "next_step": "Zqxacad call",
            "next_step_on": "2026-10-14",
        },
    )
    links = [{"record_type": "opportunity", "record_id": deal["id"]}]
    task = post("tasks", {"title": "Zqxacad task", "links": links, "due_on": "2026-10-14"})
    note = post("notes", {"body": "Zqxacad note", "links": links})
    link = post(
        "links",
        {
            "record_type": "company",
            "record_id": company["id"],
            "url": "https://drive.google.com/zqxacad",
            "label": "Zqxacad MoU",
        },
    )
    campaign = post("campaigns", {"name": "Zqxacad webinars"})
    post(f"campaigns/{campaign['id']}/members", {"company_ids": [company["id"]]})
    member = http.get(f"/api/v1/crm/campaigns/{campaign['id']}/members{a}", headers=me).json()[
        "rows"
    ][0]
    view = post("views", {"object": "companies", "name": "Zqxacad view", "visibility": "shared"})
    favourite = post("favourites", {"target_type": "view", "target_id": view["id"]})
    mapping = post(
        "csv-mappings", {"object": "companies", "name": "Zqxacad map", "mapping": {"Org": "name"}}
    )
    run = post(
        "imports/dry-run",
        {"format": "csv", "content": "Name\nZqxacad Two\n", "object": "companies"},
    )
    option = post("lists/channel/options", {"label": "Zqxacad fair"})
    labs_co = http.post(
        "/api/v1/crm/companies", params=LABS, json={"name": "Labs Co"}, headers=me
    ).json()
    mailbox = fake.add(
        "crm_mailboxes",
        workspace_id=academy["id"],
        email="zqxacad@nunnari.academy",
        owner_id=OWNER["id"],
        labels=["Zqxacad"],
        is_active=True,
    )
    message = fake.add(
        "crm_email_messages",
        workspace_id=academy["id"],
        mailbox_id=mailbox["id"],
        gmail_message_id="g-zqxacad",
        thread_id="t-zqxacad",
        subject="Zqxacad intake",
        sent_at="2026-10-13T09:00:00+00:00",
        direction="inbound",
        from_email="p@zqxacad.edu",
        participants=[{"email": "p@zqxacad.edu", "name": "Zqxacad", "role": "from"}],
        is_internal=False,
        is_auto=False,
    )
    fake.add(
        "crm_email_links",
        workspace_id=academy["id"],
        message_id=message["id"],
        record_type="company",
        record_id=company["id"],
    )
    membership = next(
        m
        for m in fake.rows["crm_memberships"].values()
        if m["workspace_id"] == academy["id"] and m["user_id"] != OWNER["id"]
    )
    made = {
        "companies": company,
        "people": person,
        "opportunities": deal,
        "tasks": task,
        "notes": note,
        "campaigns": campaign,
        "link": link,
        "member": member,
        "view": view,
        "favourite": favourite,
        "mapping": mapping,
        "run": {"id": run["run_id"]},
        "option": option,
        "mailbox": mailbox,
        "membership": membership,
    }
    made_in_labs = {"company": labs_co}
    return http, fake, {**made, "_labs": made_in_labs}


#: Path parameter → the Academy row it names. `record_id` is named by the
#: path's object.
def _value(param: str, path: str, made: dict) -> str:
    if param == "record_id":
        plural = path.removeprefix(CRM).split("/")[0]
        return made[plural]["id"]
    if param == "list_key":
        return "channel"
    return made[
        {
            "deal_id": "opportunities",
            "person_id": "people",
            "task_id": "tasks",
            "campaign_id": "campaigns",
            "link_id": "link",
            "member_id": "member",
            "membership_id": "membership",
            "option_id": "option",
            "view_id": "view",
            "favourite_id": "favourite",
            "run_id": "run",
            "mapping_id": "mapping",
            "mailbox_id": "mailbox",
        }[param]
    ]["id"]


#: A body each route accepts, so the answer is about the id and not a 422.
def _body(method: str, path: str, made: dict) -> dict | None:
    if method in ("GET", "DELETE"):
        return None
    tail = path.removeprefix(CRM)
    bodies = {
        "members/{membership_id}": {"role": "viewer"},
        "lists/{list_key}/options/{option_id}": {"label": "X"},
        "campaigns/{campaign_id}/members": {"company_ids": []},
        "campaign-members/{member_id}": {"status": "invited"},
        "campaign-members/{member_id}/convert": {"opportunity_id": made["opportunities"]["id"]},
        "opportunities/{deal_id}/stage/preview": {"stage": "qualification"},
        "opportunities/{deal_id}/stage": {"stage": "closed_lost"},
        "opportunities/{deal_id}/handoff": {"lead_id": OWNER["id"], "phases": {}},
        "opportunities/{deal_id}/link-project": {"mode": "create"},
        "opportunities/{deal_id}/contacts": {"person_id": made["people"]["id"]},
        "views/{view_id}": {"name": "X"},
        "imports/{run_id}/apply": {"format": "csv", "content": "Name\nX\n", "object": "companies"},
        "mailboxes/{mailbox_id}": {"labels": ["X"]},
    }
    if tail in bodies:
        return bodies[tail]
    if tail.endswith("{record_id}") and method == "PATCH":
        return {"city": "X"}
    return {}


class TestTheSweepCoversTheApi:
    def test_there_are_id_routes_to_sweep(self):
        assert len(ID_ROUTES) > 60

    def test_every_path_parameter_is_resolved(self, crm):
        _, _, made = crm
        for _, path in ID_ROUTES:
            for param in set(_params(path)) - EXEMPT_PARAMS:
                assert _value(param, path, made), (param, path)


@pytest.mark.parametrize(("method", "path"), ID_ROUTES, ids=[f"{m} {p}" for m, p in ID_ROUTES])
def test_another_workspaces_id_is_not_found(crm, method, path):
    http, fake, made = crm
    url = re.sub(r"\{([^}]+)\}", lambda m: _value(m.group(1), path, made), path)
    before = {t: dict(rows) for t, rows in fake.rows.items()}
    r = http.request(
        method, url, params=LABS, json=_body(method, path, made), headers=as_(OWNER["id"])
    )
    assert r.status_code == 404, (method, path, r.status_code, r.text)
    assert r.json()["detail"].startswith("No such"), r.text
    # Nothing of Academy changed.
    assert {t: dict(rows) for t, rows in fake.rows.items()} == before


class TestIdsInTheBodyOrQuery:
    """Routes that take another workspace's ids outside the path."""

    def test_merge_and_its_preview(self, crm):
        http, _, made = crm
        me = as_(OWNER["id"])
        mine = made["_labs"]["company"]
        r = http.get(
            "/api/v1/crm/companies/merge-preview",
            params={**LABS, "a": mine["id"], "b": made["companies"]["id"]},
            headers=me,
        )
        assert r.status_code == 404, r.text
        r = http.post(
            "/api/v1/crm/companies/merge",
            params=LABS,
            json={"survivor_id": mine["id"], "merged_id": made["companies"]["id"]},
            headers=me,
        )
        assert r.status_code == 404, r.text

    def test_bulk_finds_none_of_them(self, crm):
        http, fake, made = crm
        r = http.post(
            "/api/v1/crm/bulk",
            params=LABS,
            json={
                "object": "companies",
                "ids": [made["companies"]["id"]],
                "action": "set",
                "values": {"city": "X"},
            },
            headers=as_(OWNER["id"]),
        )
        assert [f["id"] for f in r.json()["failed"]] == [made["companies"]["id"]]
        assert fake.rows["crm_companies"][made["companies"]["id"]].get("city") != "X"

    def test_a_view_by_another_workspaces_id(self, crm):
        http, _, made = crm
        r = http.post(
            "/api/v1/crm/query",
            params=LABS,
            json={"object": "companies", "view_id": made["view"]["id"]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 404, r.text

    def test_a_favourite_of_another_workspaces_record(self, crm):
        http, _, made = crm
        r = http.post(
            "/api/v1/crm/favourites",
            params=LABS,
            json={
                "target_type": "record",
                "target_id": made["companies"]["id"],
                "record_type": "company",
            },
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 404, r.text


def _clean(text: str, made: dict) -> list[str]:
    """Academy's marker and record ids found in an answer."""
    found = [MARK] if MARK in text.lower() else []
    return found + [k for k, row in made.items() if not k.startswith("_") and row["id"] in text]


#: A body for the reads that are POSTs: one query per object, the duplicate
#: checks and an export per object.
POST_READS = [
    ("/api/v1/crm/query", {"object": obj, "spec": {}})
    for obj in (
        "companies",
        "people",
        "opportunities",
        "tasks",
        "notes",
        "campaign",
        "campaign_member",
    )
] + [
    ("/api/v1/crm/companies/duplicates", {"name": "Zqxacad College", "domain": "zqxacad.edu"}),
    ("/api/v1/crm/people/duplicates", {"name": "Zqxacad", "email": "p@zqxacad.edu"}),
    ("/api/v1/crm/export/view", {"object": "companies"}),
    ("/api/v1/crm/export/view", {"object": "people"}),
    ("/api/v1/crm/export/view", {"object": "opportunities"}),
]


class TestAnswersForLabsHoldNothingOfAcademy:
    """Every read with no id in its path, as the owner acting in Labs and as
    Vinita, an Admin of Labs alone."""

    def test_there_are_open_reads(self):
        assert {
            "/api/v1/crm/today",
            "/api/v1/crm/search",
            "/api/v1/crm/dashboard",
            "/api/v1/crm/export/workspace",
            "/api/v1/crm/notifications",
            "/api/v1/crm/audit",
        } <= set(OPEN_READS)

    @pytest.mark.parametrize(("path", "who"), OPEN_READ_CALLS)
    def test_every_open_read(self, crm, path, who):
        http, _, made = crm
        params = {**LABS, "q": MARK} if path.endswith("/search") else LABS
        r = http.get(path, params=params, headers=as_(who))
        assert r.status_code in (200, 403), (path, r.status_code, r.text)
        assert _clean(r.content.decode("utf-8-sig"), made) == [], path

    @pytest.mark.parametrize(("path", "body"), POST_READS)
    @pytest.mark.parametrize("who", [OWNER["id"], OUTSIDER["id"]])
    def test_every_post_read(self, crm, path, body, who):
        http, _, made = crm
        r = http.post(path, params=LABS, json=body, headers=as_(who))
        assert r.status_code == 200, (path, r.text)
        assert _clean(r.content.decode("utf-8-sig"), made) == [], (path, body)

    def test_a_non_member_cannot_name_the_workspace(self, crm):
        http, _, _ = crm
        for path in OPEN_READS:
            if path.startswith("/api/v1/crm/notifications") or path in (
                "/api/v1/crm/workspaces",
                "/api/v1/crm/bootstrap",
                "/api/v1/crm/me/preferences",
                "/api/v1/crm/projects/linkable",
            ):
                continue
            r = http.get(path, params={"workspace": "academy"}, headers=as_(OUTSIDER["id"]))
            assert (r.status_code, r.json()["detail"]) == (404, "No such workspace."), path

    def test_the_bell_of_a_labs_only_person(self, crm):
        """A notification of a workspace the person has left (or never
        joined) is not shown, even when it was addressed to them."""
        http, fake, made = crm
        academy = next(w for w in store.list_workspaces() if w["key"] == "academy")
        row = {
            "workspace_id": academy["id"],
            "user_id": SELLER["id"],
            "kind": "assigned",
            "title": "Zqxacad course",
            "record_type": "opportunity",
            "record_id": made["opportunities"]["id"],
            "dedupe_key": "x",
        }
        # crm_notify addresses members only…
        assert fake.notify([row]) == []
        # …and one left behind by a membership since removed is not shown.
        fake.add("crm_notifications", **row, created_at="2026-10-14T00:00:00+00:00", read_at=None)
        r = http.get("/api/v1/crm/notifications", headers=as_(SELLER["id"]))
        assert r.json() == []
        assert _clean(r.text, made) == []
        assert http.get(
            "/api/v1/crm/notifications/unread-count", headers=as_(SELLER["id"])
        ).json() == {"count": 0}

    def test_a_notification_cannot_name_another_workspaces_record(self, crm):
        """044 (found by tests/db/test_crm_security_db.py): the record a
        notification opens is held to its workspace, as a link's is."""
        from app.services.crm import CrmRefused

        _, fake, made = crm
        labs = next(w for w in store.list_workspaces() if w["key"] == "labs")
        with pytest.raises(CrmRefused) as refused:
            fake.notify(
                [
                    {
                        "workspace_id": labs["id"],
                        "user_id": OWNER["id"],
                        "kind": "assigned",
                        "title": "X",
                        "record_type": "company",
                        "record_id": made["companies"]["id"],
                        "dedupe_key": "y",
                    }
                ]
            )
        assert refused.value.status == 404
        assert not any(
            n["record_id"] == made["companies"]["id"]
            for n in fake.rows["crm_notifications"].values()
        )

    def test_the_digests(self, crm):
        _, _, made = crm
        workspaces = {w["id"]: w for w in store.list_workspaces()}
        labs = next(w for w in workspaces.values() if w["key"] == "labs")
        mine = [
            m
            for m in store.list_all_memberships()
            if m["user_id"] == OWNER["id"] and m["workspace_id"] == labs["id"]
        ]
        now = datetime(2026, 10, 14, 3, 30, tzinfo=UTC)
        parts = digest_service.daily_parts(OWNER, mine, workspaces, now)
        assert _clean(str(parts), made) == []
        weekly = digest_service.weekly(labs, date(2026, 10, 19), now)
        assert _clean(str(weekly), made) == []

    def test_the_marker_is_found_where_it_belongs(self, crm):
        """The check itself works: Academy's own answers carry it."""
        http, _, made = crm
        r = http.get(
            "/api/v1/crm/search",
            params={"workspace": "academy", "q": MARK},
            headers=as_(OWNER["id"]),
        )
        assert MARK in _clean(r.text, made)


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


def _tool_calls(made: dict) -> list[tuple[str, dict]]:
    """Every crm_* tool that names a record, given Academy's, in Labs."""
    deal, company = made["opportunities"]["id"], made["companies"]["id"]
    return [
        ("crm_get", {"record_type": "company", "id": company}),
        ("crm_get", {"record_type": "campaign", "id": made["campaigns"]["id"]}),
        (
            "crm_update",
            {"record_type": "person", "id": made["people"]["id"], "changes": {"city": "X"}},
        ),
        (
            "crm_set_next_step",
            {"opportunity_id": deal, "next_step": "X", "next_step_on": "2026-10-20"},
        ),
        (
            "crm_add_task",
            {"title": "X", "links": [{"record_type": "opportunity", "record_id": deal}]},
        ),
        ("crm_complete_task", {"task_id": made["tasks"]["id"]}),
        (
            "crm_add_note",
            {"body": "X", "links": [{"record_type": "company", "record_id": company}]},
        ),
        ("crm_move_stage", {"opportunity_id": deal, "stage": "closed_lost", "preview": True}),
        ("crm_move_stage", {"opportunity_id": deal, "stage": "closed_lost"}),
        ("crm_handoff", {"opportunity_id": deal}),
        ("crm_merge", {"survivor_id": made["_labs"]["company"]["id"], "merged_id": company}),
        (
            "crm_merge",
            {
                "survivor_id": made["_labs"]["company"]["id"],
                "merged_id": company,
                "preview": False,
            },
        ),
        (
            "crm_campaign_members",
            {"campaign_id": made["campaigns"]["id"], "action": "add", "company_ids": [company]},
        ),
        (
            "crm_campaign_members",
            {
                "campaign_id": made["campaigns"]["id"],
                "action": "status",
                "member_id": made["member"]["id"],
                "status": "invited",
            },
        ),
        (
            "crm_import",
            {
                "content": "Name\nX\n",
                "format": "csv",
                "mode": "run",
                "run_id": made["run"]["id"],
                "object": "companies",
            },
        ),
        (
            "crm_create",
            {
                "record_type": "drive_link",
                "fields": {
                    "record_type": "company",
                    "record_id": company,
                    "url": "https://x.y",
                    "label": "X",
                },
            },
        ),
    ]


#: The read tools, whose Labs answers must hold nothing of Academy.
TOOL_READS = [
    ("crm_workspaces", {}),
    ("crm_today", {"workspace": "labs"}),
    ("crm_search", {"query": MARK, "workspace": "labs"}),
    ("crm_dashboard", {"workspace": "labs"}),
] + [
    ("crm_list", {"object": obj, "workspace": "labs"})
    for obj in ("company", "person", "opportunity", "task", "note", "campaign", "campaign_member")
]


class TestThroughEveryTool:
    def test_every_tool_is_swept(self, crm):
        _, _, made = crm
        assert len(_tool_calls(made)) == 16  # the parametrize below
        named = {n for n, _ in _tool_calls(made)} | {n for n, _ in TOOL_READS}
        every = {t.name for t in asyncio.run(server.mcp.list_tools()) if t.name.startswith("crm_")}
        # crm_run_email_sync names no record and reads none: it is refused
        # in Academy's name below.
        assert every - named == {"crm_run_email_sync"}

    @pytest.mark.parametrize("index", range(16))
    def test_a_tool_naming_an_academy_record_in_labs(self, crm, index):
        _, fake, made = crm
        name, kwargs = _tool_calls(made)[index]
        before = {t: dict(rows) for t, rows in fake.rows.items()}
        with pytest.raises(server.ToolError) as refused:
            asyncio.run(getattr(tools, name)(Ctx(OWNER["id"]), **kwargs, workspace="labs"))
        assert str(refused.value).startswith("404: No such"), (name, str(refused.value))
        assert {t: dict(rows) for t, rows in fake.rows.items()} == before

    @pytest.mark.parametrize(("name", "kwargs"), TOOL_READS)
    @pytest.mark.parametrize("who", [OWNER["id"], OUTSIDER["id"]])
    def test_a_read_tool_answers_nothing_of_academy(self, crm, name, kwargs, who):
        _, _, made = crm
        answer = asyncio.run(getattr(tools, name)(Ctx(who), **kwargs))
        if name == "crm_workspaces":
            keys = [w["key"] for w in answer]
            assert keys == (["academy", "labs"] if who == OWNER["id"] else ["labs"])
            return
        assert _clean(str(answer), made) == [], name

    def test_a_non_member_naming_academy_is_told_nothing(self, crm):
        for name, kwargs in [*TOOL_READS[1:], ("crm_run_email_sync", {})]:
            kwargs = {**kwargs, "workspace": "academy"}
            with pytest.raises(server.ToolError) as refused:
                asyncio.run(getattr(tools, name)(Ctx(OUTSIDER["id"]), **kwargs))
            assert str(refused.value) == "404: No such workspace.", name
