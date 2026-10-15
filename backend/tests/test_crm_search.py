"""Search — spec 007 FR-CRM-SRCH-01 to -03, FR-CRM-WS-02, FR-CRM-ACC-11,
FR-CRM-DUP-03; contracts §2.8 `GET /crm/search`.

One search across companies, people, deals, tasks and notes of the current
workspace only, with the saved views the person can see, so the command
menu can jump to either. The Trash and merged companies never appear, and a
Viewer's deal hits carry no amount. Ranking and literal `%` and `_` are
proved against Postgres in `tests/db/test_crm_search_db.py`.
"""

from __future__ import annotations

import pytest
from crm_fake import OWNER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

LABS = "?workspace=labs"
ACADEMY = "?workspace=academy"
#: Plan §5.5 — the redaction denylist, written out, never imported.
AMOUNTS = {"amount", "amount_in_inr", "fx_rate_used", "won_fx_rate", "weighted_amount"}


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.add_deal_lists(labs)
    fake.add("crm_fx_rates", workspace_id=labs["id"], currency="INR", month="2026-10-01", rate=1)
    http = crm_client(monkeypatch, fake=fake)
    me = as_(OWNER["id"])
    acme = http.post(
        f"/api/v1/crm/companies{LABS}", json={"name": "Acme", "domain": "acme.com"}, headers=me
    ).json()
    http.patch(
        f"/api/v1/crm/companies/{acme['id']}{LABS}", json={"lead_stage": "qualified"}, headers=me
    )
    deal = http.post(
        f"/api/v1/crm/opportunities{LABS}",
        json={
            "name": "Acme - AIOps",
            "company_id": acme["id"],
            "amount": "250000",
            "currency": "INR",
            "next_step": "Call",
            "next_step_on": "2026-10-20",
        },
        headers=me,
    ).json()
    person = http.post(
        f"/api/v1/crm/people{LABS}",
        json={"first_name": "Priya", "last_name": "Acharya", "primary_email": "priya@acme.com"},
        headers=me,
    ).json()
    links = [{"record_type": "company", "record_id": acme["id"]}]
    task = http.post(
        f"/api/v1/crm/tasks{LABS}", json={"title": "Acme follow-up", "links": links}, headers=me
    ).json()
    note = http.post(
        f"/api/v1/crm/notes{LABS}",
        json={"body": "Acme wants a pilot in November.", "links": links},
        headers=me,
    ).json()
    http.post(f"/api/v1/crm/companies{ACADEMY}", json={"name": "Acme College"}, headers=me)
    made = {"acme": acme, "deal": deal, "person": person, "task": task, "note": note}
    return http, fake, made


def _search(http, q, who=OWNER["id"], workspace=LABS):
    return http.get(f"/api/v1/crm/search{workspace}&q={q}", headers=as_(who))


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


class TestOneWorkspace:
    """FR-CRM-SRCH-01, FR-CRM-WS-02."""

    def test_every_object_of_this_workspace(self, crm):
        http, _, made = crm
        found = _search(http, "acme").json()
        assert set(found) == {
            "companies",
            "people",
            "opportunities",
            "tasks",
            "notes",
            "campaigns",
            "views",
        }
        assert [c["name"] for c in found["companies"]] == ["Acme"]
        assert found["companies"][0]["domain"] == "acme.com"
        assert [d["id"] for d in found["opportunities"]] == [made["deal"]["id"]]
        assert [t["id"] for t in found["tasks"]] == [made["task"]["id"]]
        assert [n["id"] for n in found["notes"]] == [made["note"]["id"]]
        assert found["campaigns"] == []

    def test_people_by_name_surname_and_email(self, crm):
        http, _, made = crm
        for q in ("priya", "acharya", "priya@acme"):
            assert [p["id"] for p in _search(http, q).json()["people"]] == [made["person"]["id"]]

    def test_nothing_of_another_workspace(self, crm):
        http, *_ = crm
        labs = _search(http, "college").json()
        assert labs["companies"] == []
        academy = _search(http, "college", workspace=ACADEMY).json()
        assert [c["name"] for c in academy["companies"]] == ["Acme College"]

    def test_the_query_reaches_the_database_with_the_workspace(self, crm):
        http, fake, _ = crm
        _search(http, "acme")
        labs = next(w for w in fake.rows["crm_workspaces"].values() if w["key"] == "labs")
        assert fake.queries[-1] == {"workspace_id": labs["id"], "search": "acme", "redact": False}


class TestTrashAndMerged:
    """FR-CRM-SRCH-03, FR-CRM-DEL-01, FR-CRM-DUP-03."""

    def test_a_trashed_record_is_not_found(self, crm):
        http, _, made = crm
        http.delete(f"/api/v1/crm/tasks/{made['task']['id']}{LABS}", headers=as_(OWNER["id"]))
        assert _search(http, "acme").json()["tasks"] == []

    def test_a_merged_company_is_not_found(self, crm):
        http, _, made = crm
        me = as_(OWNER["id"])
        other = http.post(
            f"/api/v1/crm/companies{LABS}", json={"name": "Acme Inc"}, headers=me
        ).json()
        assert {c["name"] for c in _search(http, "acme").json()["companies"]} == {
            "Acme",
            "Acme Inc",
        }
        http.post(
            f"/api/v1/crm/companies/merge{LABS}",
            json={"survivor_id": made["acme"]["id"], "merged_id": other["id"]},
            headers=me,
        )
        assert [c["name"] for c in _search(http, "acme").json()["companies"]] == ["Acme"]


class TestAmounts:
    """FR-CRM-SRCH-03, FR-CRM-ACC-11."""

    def test_an_admin_sees_the_amount(self, crm):
        http, *_ = crm
        [deal] = _search(http, "aiops").json()["opportunities"]
        assert (deal["amount"], deal["currency"], deal["stage_label"]) == (
            "250000.00",
            "INR",
            "Discovery",
        )

    def test_a_viewers_hits_carry_no_amount(self, crm):
        http, fake, _ = crm
        found = _search(http, "acme", who=VIEWER["id"]).json()
        assert found["opportunities"]
        assert _keys(found) & AMOUNTS == set()
        assert fake.queries[-1]["redact"] is True


class TestJumpTargets:
    """FR-CRM-SRCH-02 — saved views are jump targets too."""

    def test_saved_views_by_name(self, crm):
        http, *_ = crm
        views = _search(http, "pipe").json()["views"]
        assert [(v["name"], v["system_key"]) for v in views] == [("Pipeline", "pipeline")]

    def test_a_private_view_of_someone_else_is_not(self, crm):
        http, *_ = crm
        http.post(
            f"/api/v1/crm/views{LABS}",
            json={
                "object": "company",
                "name": "Owner's secret list",
                "kind": "table",
                "visibility": "private",
                "config": {},
            },
            headers=as_(OWNER["id"]),
        )
        assert _search(http, "secret").json()["views"] != []
        assert _search(http, "secret", who=VIEWER["id"]).json()["views"] == []

    def test_a_task_or_note_opens_the_record_it_is_linked_to(self, crm):
        http, _, made = crm
        found = _search(http, "acme").json()
        target = {"record_type": "company", "record_id": made["acme"]["id"]}
        assert found["tasks"][0]["opens"] == target
        assert found["notes"][0]["opens"] == target


class TestTheQuery:
    def test_one_character_is_too_short(self, crm):
        http, *_ = crm
        r = _search(http, "a")
        assert (r.status_code, r.json()["detail"]) == (422, "Type at least two characters.")

    def test_at_most_eight_of_each(self, crm):
        http, *_ = crm
        assert _search(http, "acme&limit=9").status_code == 422

    def test_wildcards_are_matched_literally(self, crm):
        http, *_ = crm
        assert _search(http, "a%25").json()["companies"] == []
        assert _search(http, "a_").json()["companies"] == []
