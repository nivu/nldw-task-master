"""Saved views, favourites and bulk actions — spec 007 FR-CRM-VIEW-05, -07,
-11, FR-CRM-UI-02; Q-37; contracts §2.7, §2.5 `/crm/bulk`; spec §11 AC
"Viewers save private views only".

Routes run against `tests/crm_fake.py`, whose seeded views are read from
migration 034 itself, so the definitions asserted here are the database's.
Bulk actions go through the same service calls as one edit, so each record
meets the pipeline rules (and, in Labs, the TVA effects of S5) on its own.
"""

from __future__ import annotations

from datetime import date

import pytest
from crm_fake import MEMBER, OWNER, SELLER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.services.crm import deals
from app.services.crm import views as service

ACADEMY = "?workspace=academy"
LABS = "?workspace=labs"
TODAY = date(2026, 10, 14)
NEXT = {"next_step": "Call", "next_step_on": "2026-10-20"}
OPEN = ["discovery", "qualification", "proposal", "negotiation"]


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.add_deal_lists(labs)
    fake.add_deal_lists(academy)
    fake.add_member(academy, VIEWER["id"], "viewer")
    fake.add_member(academy, SELLER["id"], "member")
    fake.add("crm_fx_rates", workspace_id=labs["id"], currency="USD", month="2026-10-01", rate=84)
    monkeypatch.setattr(deals, "today_in_company_tz", lambda: TODAY)
    monkeypatch.setattr(service, "today_for", lambda user: TODAY)
    return crm_client(monkeypatch, fake=fake), fake, labs, academy


def _views(http, obj=None, who=OWNER["id"], query=ACADEMY):
    url = f"/api/v1/crm/views{query}" + (f"&object={obj}" if obj else "")
    r = http.get(url, headers=as_(who))
    assert r.status_code == 200, r.text
    return r.json()


def _new_view(http, who=OWNER["id"], query=ACADEMY, **body):
    payload = {"object": "companies", "name": "Mine", "config": {}, **body}
    return http.post(f"/api/v1/crm/views{query}", json=payload, headers=as_(who))


def _query(http, view, obj, who=OWNER["id"], query=ACADEMY):
    r = http.post(f"/api/v1/crm/query{query}", json={"object": obj, "view": view}, headers=as_(who))
    assert r.status_code == 200, r.text
    return r.json()


def _names(answer):
    return sorted(row.get("name") or row.get("title") for row in answer["rows"])


def _company(http, name, query=ACADEMY, who=OWNER["id"], **fields):
    r = http.post(f"/api/v1/crm/companies{query}", json={"name": name, **fields}, headers=as_(who))
    assert r.status_code == 201, r.text
    return r.json()


def _patch(http, obj, record_id, body, query=ACADEMY, who=OWNER["id"]):
    r = http.patch(f"/api/v1/crm/{obj}/{record_id}{query}", json=body, headers=as_(who))
    assert r.status_code == 200, r.text
    return r.json()


def _deal(http, company, name, query=ACADEMY, **fields):
    r = http.post(
        f"/api/v1/crm/opportunities{query}",
        json={"name": name, "company_id": company["id"], **NEXT, **fields},
        headers=as_(OWNER["id"]),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _option(fake, ws, list_key, label):
    return next(
        o["id"]
        for o in fake.rows["crm_select_options"].values()
        if o["workspace_id"] == ws["id"] and o["list_key"] == list_key and o["label"] == label
    )


class TestSeededViews:
    """FR-CRM-VIEW-11, data-model §7.5 — the spec's definitions, written
    out, in each workspace."""

    EXPECTED = {
        "companies_all": ("company", "All companies", "table"),
        "lead_funnel": ("company", "Lead funnel", "kanban"),
        "partners_channels": ("company", "Partners and channels", "table"),
        "my_accounts": ("company", "My accounts", "table"),
        "pipeline": ("opportunity", "Pipeline", "kanban"),
        "closing_this_month": ("opportunity", "Closing this month", "table"),
        "won_lost_quarter": ("opportunity", "Won and lost this quarter", "table"),
        "open_tasks_by_owner": ("task", "Open tasks by owner", "table"),
        "my_week": ("task", "My week", "kanban"),
        # 038 (1b).
        "follow_ups_owed": ("company", "Follow-ups owed", "table"),
        # 041 (1b).
        "members_by_status": ("campaign_member", "Members by status", "kanban"),
    }

    @pytest.mark.parametrize("query", [LABS, ACADEMY])
    def test_every_workspace_has_them_shared(self, crm, query):
        http, *_ = crm
        seen = {v["system_key"]: v for v in _views(http, query=query) if v["system_key"]}
        assert {k: (v["object"], v["name"], v["kind"]) for k, v in seen.items()} == self.EXPECTED
        assert {v["visibility"] for v in seen.values()} == {"shared"}

    def test_their_definitions(self, crm):
        http, *_ = crm
        seen = {v["system_key"]: v["config"] for v in _views(http) if v["system_key"]}
        assert seen["lead_funnel"]["kanban_field"] == "lead_stage"
        assert seen["lead_funnel"]["filters"]["items"] == [
            {"field": "lead_stage", "operator": "none_of", "value": ["partner"]}
        ]
        assert seen["partners_channels"]["filters"]["items"] == [
            {"field": "channel_ids", "operator": "is_not_empty"}
        ]
        assert seen["partners_channels"]["group_by"] == "account_type_id"
        assert seen["my_accounts"]["filters"]["items"] == [
            {"field": "owner_id", "operator": "is_me"}
        ]
        assert seen["pipeline"]["kanban_field"] == "stage"
        assert seen["pipeline"]["filters"]["items"] == [
            {"field": "stage", "operator": "any_of", "value": OPEN}
        ]
        assert seen["closing_this_month"]["filters"]["items"] == [
            {"field": "stage", "operator": "any_of", "value": OPEN},
            {"field": "expected_close_on", "operator": "this_month"},
        ]
        assert seen["won_lost_quarter"]["group_by"] == "stage"
        assert seen["open_tasks_by_owner"]["group_by"] == "assignee_id"
        assert seen["my_week"]["kanban_field"] == "bucket"
        assert seen["my_week"]["filters"]["items"] == [
            {"field": "assignee_id", "operator": "is_me"}
        ]

    def test_one_default_per_object(self, crm):
        http, *_ = crm
        defaults = {v["object"]: v["system_key"] for v in _views(http) if v["is_default"]}
        assert defaults == {
            "company": "companies_all",
            "opportunity": "pipeline",
            "task": "open_tasks_by_owner",
            "campaign_member": "members_by_status",
        }

    @pytest.mark.parametrize(
        "obj", ["campaign", "campaigns", "campaign_member", "campaign_members"]
    )
    def test_campaign_lists_can_ask_for_their_views(self, crm, obj):
        """The Campaigns list page asks `GET /crm/views?object=campaign`;
        the route takes every object the schemas know, not a shorter list."""
        http, *_ = crm
        views = _views(http, obj=obj)
        kind = "campaign_member" if obj.startswith("campaign_member") else "campaign"
        assert {v["object"] for v in views} <= {kind}
        if kind == "campaign_member":
            assert [v["system_key"] for v in views] == ["members_by_status"]

    def test_company_views_return_the_right_rows(self, crm):
        http, fake, _, academy = crm
        channel = fake.option(academy, "Referral")["id"]
        partner_type = _option(fake, academy, "company.account_type", "Client")
        _company(http, "Mine")
        _company(http, "Hers", who=MEMBER["id"])
        partner = _company(http, "Channel", channel_ids=[channel], account_type_id=partner_type)
        _patch(http, "companies", partner["id"], {"lead_stage": "qualified"})
        assert _names(_query(http, "my_accounts", "companies")) == ["Channel", "Mine"]
        assert _names(_query(http, "My accounts", "companies", who=MEMBER["id"])) == ["Hers"]
        found = _query(http, "partners_channels", "companies")
        assert _names(found) == ["Channel"]
        assert [(g["label"], g["count"]) for g in found["groups"]] == [("Client", 1)]
        funnel = _query(http, "lead_funnel", "companies")
        assert {g["label"]: g["count"] for g in funnel["groups"]} == {"New": 2, "Qualified": 1}

    def test_the_lead_funnel_hides_partners(self, crm):
        http, fake, _, academy = crm
        made = _company(http, "Ally")
        fake.rows["crm_companies"][made["id"]]["lead_stage"] = "partner"
        assert _names(_query(http, "lead_funnel", "companies")) == []

    def test_deal_views_return_the_right_rows(self, crm):
        http, fake, _, academy = crm
        company = _company(http, "College")
        _patch(http, "companies", company["id"], {"lead_stage": "qualified"})
        soon = _deal(http, company, "Soon", expected_close_on="2026-10-30")
        _deal(http, company, "Later", expected_close_on="2027-02-01")
        won = _deal(http, company, "Won one")
        lost = _deal(http, company, "Lost last year")
        rows = fake.rows["crm_opportunities"]
        rows[won["id"]].update(stage="closed_won", won_on="2026-10-02")
        rows[lost["id"]].update(stage="closed_lost", lost_on="2025-12-20")
        pipeline = _query(http, "pipeline", "opportunities")
        assert _names(pipeline) == ["Later", "Soon"]
        assert [(g["key"], g["count"]) for g in pipeline["groups"]] == [("discovery", 2)]
        assert _names(_query(http, "closing_this_month", "opportunities")) == [soon["name"]]
        quarter = _query(http, "won_lost_quarter", "opportunities")
        assert _names(quarter) == ["Won one"]
        assert [g["label"] for g in quarter["groups"]] == ["Closed Won"]

    def test_task_views_return_the_right_rows(self, crm):
        http, fake, _, academy = crm
        company = _company(http, "College")
        link = [{"record_type": "company", "record_id": company["id"]}]
        for title, who, bucket in (
            ("Mine today", OWNER["id"], "today"),
            ("Hers", MEMBER["id"], "this_week"),
            ("Done already", OWNER["id"], None),
        ):
            body = {"title": title, "links": link, "assignee_id": who}
            if bucket:
                body["bucket"] = bucket
            r = http.post(f"/api/v1/crm/tasks{ACADEMY}", json=body, headers=as_(OWNER["id"]))
            assert r.status_code == 201, r.text
            if title == "Done already":
                http.post(
                    f"/api/v1/crm/tasks/{r.json()['id']}/complete{ACADEMY}",
                    headers=as_(OWNER["id"]),
                )
        by_owner = _query(http, "open_tasks_by_owner", "tasks")
        assert _names(by_owner) == ["Hers", "Mine today"]
        assert sorted((g["label"], g["count"]) for g in by_owner["groups"]) == [
            ("Deepika", 1),
            ("Owner", 1),
        ]
        week = _query(http, "my_week", "tasks")
        assert _names(week) == ["Done already", "Mine today"]
        # 008 FR-TASK-DATA-08: a task created with no bucket gets Later.
        assert [g["label"] for g in week["groups"]] == ["Today", "Later"]

    def test_the_other_workspaces_rows_never_appear(self, crm):
        http, *_ = crm
        _company(http, "Labs co", query=LABS)
        assert _names(_query(http, "companies_all", "companies")) == []
        assert _names(_query(http, "companies_all", "companies", query=LABS)) == ["Labs co"]


def _ask(http, obj, spec, view=None, who=OWNER["id"], query=ACADEMY):
    body = {"object": obj, "spec": spec, **({"view": view} if view else {})}
    r = http.post(f"/api/v1/crm/query{query}", json=body, headers=as_(who))
    assert r.status_code == 200, r.text
    return r.json()


class TestListOrder:
    """FR-CRM-VIEW-03, -04: a list sorts and groups in each list's own order,
    and a grouped list pages in the order of its groups."""

    def test_the_pipeline_pages_in_stage_order(self, crm):
        http, fake, _, academy = crm
        company = _company(http, "College")
        _patch(http, "companies", company["id"], {"lead_stage": "qualified"})
        rows = fake.rows["crm_opportunities"]
        for stage in reversed(OPEN):
            for n in range(2):
                made = _deal(http, company, f"{stage} {n}")
                rows[made["id"]]["stage"] = stage
        seen, spec = [], {"limit": 3}
        for _ in range(5):
            page = _ask(http, "opportunities", spec, view="pipeline")
            seen += [r["stage"] for r in page["rows"]]
            if not page["next"]:
                break
            spec = {**spec, "after": page["next"]}
        assert seen == [s for s in OPEN for _ in range(2)]
        assert [g["key"] for g in page["groups"]] == OPEN

    def test_sort_by_stage_owner_and_select(self, crm):
        http, fake, _, academy = crm
        company = _company(http, "College")
        _patch(http, "companies", company["id"], {"lead_stage": "qualified"})
        rows = fake.rows["crm_opportunities"]
        for name, stage, owner in (
            ("A", "negotiation", OWNER["id"]),
            ("B", "discovery", MEMBER["id"]),
            ("C", "proposal", OWNER["id"]),
        ):
            made = _deal(http, company, name)
            rows[made["id"]].update(stage=stage, owner_id=owner)
        by_stage = _ask(http, "opportunities", {"sorts": [{"field": "stage", "dir": "asc"}]})
        assert [r["name"] for r in by_stage["rows"]] == ["B", "C", "A"]
        by_owner = _ask(
            http,
            "opportunities",
            {"sorts": [{"field": "owner_id", "dir": "asc"}, {"field": "name", "dir": "asc"}]},
        )
        # Deepika before Owner, by name.
        assert [r["name"] for r in by_owner["rows"]] == ["B", "A", "C"]
        client_type = _option(fake, academy, "company.account_type", "Client")
        prospect = _option(fake, academy, "company.account_type", "Prospect")
        _company(http, "Zed", account_type_id=prospect)
        _company(http, "Ace", account_type_id=client_type)
        by_type = _ask(
            http,
            "companies",
            {"sorts": [{"field": "account_type_id", "dir": "desc"}], "limit": 2},
        )
        # Client sits after Prospect in the list, so it comes first descending.
        assert [r["name"] for r in by_type["rows"]] == ["Ace", "Zed"]

    def test_a_day_is_the_persons_own_day(self, crm):
        http, fake, _, academy = crm
        late = _company(http, "Late")
        early = _company(http, "Early")
        rows = fake.rows["crm_companies"]
        # 23:30 IST on the 13th, and 00:30 IST on the 14th (both the 13th in UTC).
        rows[late["id"]]["last_contact_at"] = "2026-10-13T18:00:00+00:00"
        rows[early["id"]]["last_contact_at"] = "2026-10-13T19:00:00+00:00"
        on = {"field": "last_contact_at", "operator": "on", "value": "2026-10-14"}
        found = _ask(http, "companies", {"filters": {"op": "and", "items": [on]}})
        assert _names(found) == ["Early"]
        within = {"field": "last_contact_at", "operator": "within_last_days", "value": 0}
        found = _ask(http, "companies", {"filters": {"op": "and", "items": [within]}})
        assert _names(found) == ["Early"]
        assert fake.queries[-1]["spec"]["tz"] == "Asia/Kolkata"


class TestSavingViews:
    """FR-CRM-VIEW-05, Q-37."""

    def test_viewers_save_private_views_only(self, crm):
        http, *_ = crm
        r = _new_view(http, who=VIEWER["id"], visibility="shared")
        assert r.status_code == 403
        assert r.json()["detail"] == "Viewers can save private views only."
        r = _new_view(http, who=VIEWER["id"], visibility="private")
        assert r.status_code == 201, r.text
        assert r.json()["owner_id"] == VIEWER["id"]

    def test_a_viewers_view_naming_an_amount_is_refused(self, crm):
        http, *_ = crm
        r = _new_view(
            http,
            who=VIEWER["id"],
            object="opportunities",
            config={"sorts": [{"field": "amount", "dir": "desc"}]},
        )
        assert r.status_code == 422

    def test_a_private_view_is_its_owners_alone(self, crm):
        http, *_ = crm
        made = _new_view(http, who=MEMBER["id"], name="Deepika's").json()
        assert made["name"] in [v["name"] for v in _views(http, who=MEMBER["id"])]
        assert made["name"] not in [v["name"] for v in _views(http)]
        r = http.patch(
            f"/api/v1/crm/views/{made['id']}{ACADEMY}", json={"name": "x"}, headers=as_(OWNER["id"])
        )
        assert r.status_code == 404
        r = http.post(
            f"/api/v1/crm/query{ACADEMY}",
            json={"object": "companies", "view": "Deepika's"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 404

    @pytest.mark.parametrize("who", [OWNER["id"], MEMBER["id"]])
    def test_admins_and_members_manage_shared_views(self, crm, who):
        http, *_ = crm
        made = _new_view(http, who=who, name="Shared", visibility="shared")
        assert made.status_code == 201, made.text
        assert made.json()["owner_id"] is None
        r = http.patch(
            f"/api/v1/crm/views/{made.json()['id']}{ACADEMY}",
            json={"name": "Renamed", "position": 7},
            headers=as_(who),
        )
        assert (r.status_code, r.json()["name"], r.json()["position"]) == (200, "Renamed", 7)

    def test_a_viewer_cannot_change_a_shared_view(self, crm):
        http, *_ = crm
        seeded = next(v for v in _views(http, who=VIEWER["id"]) if v["system_key"] == "my_week")
        assert seeded["can_edit"] is False
        r = http.patch(
            f"/api/v1/crm/views/{seeded['id']}{ACADEMY}",
            json={"name": "x"},
            headers=as_(VIEWER["id"]),
        )
        assert r.status_code == 403
        r = http.delete(f"/api/v1/crm/views/{seeded['id']}{ACADEMY}", headers=as_(VIEWER["id"]))
        assert r.status_code == 403

    def test_a_new_default_replaces_the_old(self, crm):
        http, *_ = crm
        made = _new_view(http, name="Mine first", visibility="shared").json()
        r = http.patch(
            f"/api/v1/crm/views/{made['id']}{ACADEMY}",
            json={"is_default": True},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        defaults = [v["name"] for v in _views(http, "companies") if v["is_default"]]
        assert defaults == ["Mine first"]

    def test_a_private_view_cannot_be_the_default(self, crm):
        http, *_ = crm
        made = _new_view(http).json()
        r = http.patch(
            f"/api/v1/crm/views/{made['id']}{ACADEMY}",
            json={"is_default": True},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422

    def test_a_seeded_view_is_hidden_not_deleted(self, crm):
        http, fake, *_ = crm
        seeded = next(v for v in _views(http) if v["system_key"] == "lead_funnel")
        r = http.delete(f"/api/v1/crm/views/{seeded['id']}{ACADEMY}", headers=as_(MEMBER["id"]))
        assert (r.status_code, r.json()) == (200, {"hidden": True})
        assert seeded["id"] in fake.rows["crm_saved_views"]
        assert "lead_funnel" not in [v["system_key"] for v in _views(http)]
        # Still reachable by its key, and shown again on request.
        assert _query(http, "lead_funnel", "companies")["view"]["hidden"] is True
        r = http.patch(
            f"/api/v1/crm/views/{seeded['id']}{ACADEMY}",
            json={"hidden": False},
            headers=as_(MEMBER["id"]),
        )
        assert r.status_code == 200 and r.json()["hidden"] is False

    def test_a_hidden_seeded_view_can_be_found_and_shown_again(self, crm):
        http, *_ = crm
        seeded = next(v for v in _views(http) if v["system_key"] == "pipeline")
        assert seeded["is_default"] is True
        http.delete(f"/api/v1/crm/views/{seeded['id']}{ACADEMY}", headers=as_(OWNER["id"]))
        everything = _views(http, query=ACADEMY + "&include_hidden=true")
        hidden = next(v for v in everything if v["id"] == seeded["id"])
        assert hidden["hidden"] is True and hidden["is_default"] is False
        # A Viewer does not manage shared views, so is not shown hidden ones.
        theirs = _views(http, who=VIEWER["id"], query=ACADEMY + "&include_hidden=true")
        assert seeded["id"] not in [v["id"] for v in theirs]
        url = f"/api/v1/crm/views/{seeded['id']}{ACADEMY}"
        r = http.patch(url, json={"is_default": True}, headers=as_(OWNER["id"]))
        assert r.status_code == 422
        r = http.patch(url, json={"hidden": False}, headers=as_(OWNER["id"]))
        assert r.status_code == 200 and r.json()["hidden"] is False
        assert seeded["id"] in [v["id"] for v in _views(http)]
        r = http.patch(url, json={"is_default": True}, headers=as_(OWNER["id"]))
        assert r.status_code == 200 and r.json()["is_default"] is True

    def test_hiding_a_default_view_by_patch_clears_the_default(self, crm):
        http, *_ = crm
        seeded = next(v for v in _views(http) if v["system_key"] == "pipeline")
        r = http.patch(
            f"/api/v1/crm/views/{seeded['id']}{ACADEMY}",
            json={"hidden": True},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200 and r.json()["is_default"] is False

    def test_a_seeded_view_keeps_its_kind(self, crm):
        http, *_ = crm
        seeded = next(v for v in _views(http) if v["system_key"] == "pipeline")
        r = http.patch(
            f"/api/v1/crm/views/{seeded['id']}{ACADEMY}",
            json={"kind": "table"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422

    def test_a_kanban_view_needs_a_select_field(self, crm):
        http, *_ = crm
        r = _new_view(http, kind="kanban", config={})
        assert r.status_code == 422
        r = _new_view(http, kind="kanban", config={"kanban_field": "owner_id"})
        assert r.status_code == 422
        r = _new_view(http, kind="kanban", config={"kanban_field": "lead_stage"})
        assert r.status_code == 201, r.text

    def test_a_name_used_twice_is_409(self, crm):
        http, *_ = crm
        assert _new_view(http, name="Same", visibility="shared").status_code == 201
        r = _new_view(http, name="same", visibility="shared")
        assert r.status_code == 409

    def test_a_view_change_is_audited(self, crm):
        http, fake, *_ = crm
        _new_view(http, name="Audited")
        assert fake.audit[-1]["action"] == "saved_view.created"


class TestFavourites:
    """FR-CRM-UI-02."""

    def test_pin_a_view_and_a_record(self, crm):
        http, *_ = crm
        company = _company(http, "Acme")
        view = next(v for v in _views(http) if v["system_key"] == "pipeline")
        for body in (
            {"target_type": "view", "target_id": view["id"]},
            {"target_type": "record", "record_type": "company", "target_id": company["id"]},
        ):
            r = http.post(f"/api/v1/crm/favourites{ACADEMY}", json=body, headers=as_(MEMBER["id"]))
            assert r.status_code == 201, r.text
        r = http.get(f"/api/v1/crm/favourites{ACADEMY}", headers=as_(MEMBER["id"]))
        assert [(f["target_type"], f["name"]) for f in r.json()] == [
            ("view", "Pipeline"),
            ("record", "Acme"),
        ]
        assert http.get(f"/api/v1/crm/favourites{ACADEMY}", headers=as_(OWNER["id"])).json() == []

    def test_twice_is_409(self, crm):
        http, *_ = crm
        company = _company(http, "Acme")
        body = {"target_type": "record", "record_type": "company", "target_id": company["id"]}
        assert (
            http.post(
                f"/api/v1/crm/favourites{ACADEMY}", json=body, headers=as_(OWNER["id"])
            ).status_code
            == 201
        )
        r = http.post(f"/api/v1/crm/favourites{ACADEMY}", json=body, headers=as_(OWNER["id"]))
        assert (r.status_code, r.json()["detail"]) == (409, "That is already a favourite.")

    def test_another_workspaces_record_or_someone_elses_view_is_not_found(self, crm):
        http, *_ = crm
        labs_company = _company(http, "Labs co", query=LABS)
        private = _new_view(http, who=MEMBER["id"]).json()
        for body in (
            {"target_type": "record", "record_type": "company", "target_id": labs_company["id"]},
            {"target_type": "view", "target_id": private["id"]},
        ):
            r = http.post(f"/api/v1/crm/favourites{ACADEMY}", json=body, headers=as_(OWNER["id"]))
            assert r.status_code == 404, body

    def test_a_viewer_pins_too(self, crm):
        http, *_ = crm
        company = _company(http, "Acme")
        body = {"target_type": "record", "record_type": "company", "target_id": company["id"]}
        r = http.post(f"/api/v1/crm/favourites{ACADEMY}", json=body, headers=as_(VIEWER["id"]))
        assert r.status_code == 201, r.text

    def test_unpin_and_only_your_own(self, crm):
        http, *_ = crm
        company = _company(http, "Acme")
        body = {"target_type": "record", "record_type": "company", "target_id": company["id"]}
        fav = http.post(
            f"/api/v1/crm/favourites{ACADEMY}", json=body, headers=as_(OWNER["id"])
        ).json()
        r = http.delete(f"/api/v1/crm/favourites/{fav['id']}{ACADEMY}", headers=as_(MEMBER["id"]))
        assert r.status_code == 404
        r = http.delete(f"/api/v1/crm/favourites/{fav['id']}{ACADEMY}", headers=as_(OWNER["id"]))
        assert r.status_code == 200
        assert http.get(f"/api/v1/crm/favourites{ACADEMY}", headers=as_(OWNER["id"])).json() == []

    def test_a_trashed_record_drops_out_and_a_deleted_view_takes_its_pins(self, crm):
        http, fake, *_ = crm
        company = _company(http, "Acme")
        view = _new_view(http, name="Gone soon").json()
        for body in (
            {"target_type": "record", "record_type": "company", "target_id": company["id"]},
            {"target_type": "view", "target_id": view["id"]},
        ):
            http.post(f"/api/v1/crm/favourites{ACADEMY}", json=body, headers=as_(OWNER["id"]))
        http.delete(f"/api/v1/crm/companies/{company['id']}{ACADEMY}", headers=as_(OWNER["id"]))
        assert [
            f["name"]
            for f in http.get(f"/api/v1/crm/favourites{ACADEMY}", headers=as_(OWNER["id"])).json()
        ] == ["Gone soon"]
        http.delete(f"/api/v1/crm/views/{view['id']}{ACADEMY}", headers=as_(OWNER["id"]))
        assert [f["target_type"] for f in fake.rows["crm_favourites"].values()] == ["record"]

    def test_purging_a_record_removes_its_pins(self, crm):
        http, fake, *_ = crm
        company = _company(http, "Acme")
        body = {"target_type": "record", "record_type": "company", "target_id": company["id"]}
        http.post(f"/api/v1/crm/favourites{ACADEMY}", json=body, headers=as_(OWNER["id"]))
        http.delete(f"/api/v1/crm/companies/{company['id']}{ACADEMY}", headers=as_(OWNER["id"]))
        r = http.delete(
            f"/api/v1/crm/companies/{company['id']}/permanent{ACADEMY}", headers=as_(OWNER["id"])
        )
        assert r.status_code == 200, r.text
        assert fake.rows["crm_favourites"] == {}


class TestBulk:
    """FR-CRM-VIEW-07 — each record on its own, the answer per id."""

    def _bulk(self, http, body, who=OWNER["id"], query=ACADEMY):
        return http.post(f"/api/v1/crm/bulk{query}", json=body, headers=as_(who))

    def test_viewers_cannot(self, crm):
        http, *_ = crm
        company = _company(http, "Acme")
        r = self._bulk(
            http,
            {
                "object": "companies",
                "ids": [company["id"]],
                "action": "set",
                "values": {"city": "X"},
            },
            who=VIEWER["id"],
        )
        assert r.status_code == 403

    def test_set_answers_per_id(self, crm):
        http, fake, *_ = crm
        a, b = _company(http, "A"), _company(http, "B")
        missing = "00000000-0000-4000-8000-000000000999"
        r = self._bulk(
            http,
            {
                "object": "companies",
                "ids": [a["id"], b["id"], missing],
                "action": "set",
                "values": {"city": "Pune"},
            },
        )
        assert r.status_code == 200, r.text
        assert r.json() == {
            "ok": [a["id"], b["id"]],
            "failed": [{"id": missing, "reason": "No such company."}],
        }
        assert {c["city"] for c in fake.rows["crm_companies"].values()} == {"Pune"}

    def test_a_stage_move_obeys_the_pipeline_rules_per_deal(self, crm):
        http, fake, *_ = crm
        company = _company(http, "College")
        _patch(http, "companies", company["id"], {"lead_stage": "qualified"})
        person = http.post(
            f"/api/v1/crm/people{ACADEMY}", json={"first_name": "Priya"}, headers=as_(OWNER["id"])
        ).json()
        ready = _deal(
            http,
            company,
            "Ready",
            point_of_contact_id=person["id"],
            amount="1000",
            currency="INR",
            expected_close_on="2026-12-01",
        )
        bare = _deal(http, company, "Bare")
        r = self._bulk(
            http,
            {
                "object": "opportunities",
                "ids": [ready["id"], bare["id"]],
                "action": "set",
                "values": {"stage": "proposal"},
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["ok"] == [ready["id"]]
        [failed] = r.json()["failed"]
        assert failed["id"] == bare["id"]
        assert failed["reason"].startswith("Proposal needs")
        assert "amount" in failed["reason"]
        assert fake.rows["crm_opportunities"][ready["id"]]["stage"] == "proposal"
        assert fake.rows["crm_opportunities"][bare["id"]]["stage"] == "discovery"

    def test_a_labs_stage_move_runs_the_tva_effects(self, crm):
        """S5: a Labs deal moved to Proposal in bulk gets its tentative
        project, as one move would."""
        http, fake, *_ = crm
        company = _company(http, "Acme Corp", query=LABS)
        _patch(http, "companies", company["id"], {"lead_stage": "qualified"}, query=LABS)
        person = http.post(
            f"/api/v1/crm/people{LABS}", json={"first_name": "Priya"}, headers=as_(OWNER["id"])
        ).json()
        made = [
            _deal(
                http,
                company,
                f"Acme - Phase {n}",
                query=LABS,
                point_of_contact_id=person["id"],
                amount="1000",
                currency="USD",
                expected_close_on="2026-12-01",
            )
            for n in (1, 2)
        ]
        r = self._bulk(
            http,
            {
                "object": "opportunities",
                "ids": [d["id"] for d in made],
                "action": "set",
                "values": {"stage": "proposal"},
            },
            query=LABS,
        )
        assert r.status_code == 200, r.text
        assert r.json()["failed"] == []
        assert sorted(p["name"] for p in fake.rows["projects"].values()) == [
            "Acme - Phase 1",
            "Acme - Phase 2",
        ]

    def test_a_deal_save_still_needs_its_next_step(self, crm):
        http, *_ = crm
        company = _company(http, "College")
        _patch(http, "companies", company["id"], {"lead_stage": "qualified"})
        deal = _deal(http, company, "Open")
        r = self._bulk(
            http,
            {
                "object": "opportunities",
                "ids": [deal["id"]],
                "action": "set",
                "values": {"next_step": None},
            },
        )
        assert r.json()["ok"] == []
        assert "next step" in r.json()["failed"][0]["reason"]

    def test_assign_owner(self, crm):
        http, fake, *_ = crm
        a = _company(http, "A")
        r = self._bulk(
            http,
            {
                "object": "companies",
                "ids": [a["id"]],
                "action": "assign_owner",
                "values": {"owner_id": MEMBER["id"]},
            },
        )
        assert r.json() == {"ok": [a["id"]], "failed": []}
        assert fake.rows["crm_companies"][a["id"]]["owner_id"] == MEMBER["id"]
        r = self._bulk(
            http,
            {
                "object": "companies",
                "ids": [a["id"]],
                "action": "assign_owner",
                "values": {"owner_id": "u-outsider"},
            },
        )
        assert r.json()["failed"] == [
            {"id": a["id"], "reason": "The owner must be a member of this workspace."}
        ]

    def test_add_task_links_each_record(self, crm):
        http, fake, *_ = crm
        a, b = _company(http, "A"), _company(http, "B")
        r = self._bulk(
            http,
            {
                "object": "companies",
                "ids": [a["id"], b["id"]],
                "action": "add_task",
                "values": {"title": "Follow up", "assignee_id": MEMBER["id"]},
            },
        )
        assert r.json() == {"ok": [a["id"], b["id"]], "failed": []}
        linked = sorted(link["record_id"] for link in fake.rows["task_links"].values())
        assert linked == sorted([a["id"], b["id"]])

    def test_delete_holds_each_record_to_who_may_delete_it(self, crm):
        """FR-CRM-DEL-04 — a Member deletes what they own."""
        http, fake, *_ = crm
        mine = _company(http, "Mine", who=MEMBER["id"])
        theirs = _company(http, "Theirs")
        r = self._bulk(
            http,
            {"object": "companies", "ids": [mine["id"], theirs["id"]], "action": "delete"},
            who=MEMBER["id"],
        )
        assert r.json()["ok"] == [mine["id"]]
        assert r.json()["failed"][0]["id"] == theirs["id"]
        assert fake.rows["crm_companies"][theirs["id"]]["deleted_at"] is None

    def test_a_request_that_fits_no_record_is_422(self, crm):
        http, *_ = crm
        company = _company(http, "A")
        for body in (
            {
                "object": "notes",
                "ids": [company["id"]],
                "action": "assign_owner",
                "values": {"owner_id": OWNER["id"]},
            },
            {
                "object": "tasks",
                "ids": [company["id"]],
                "action": "add_task",
                "values": {"title": "x"},
            },
            {"object": "companies", "ids": [company["id"]], "action": "add_to_campaign"},
            {"object": "companies", "ids": [company["id"]], "action": "set", "values": {}},
        ):
            assert self._bulk(http, body).status_code == 422, body
