"""Tasks and notes — spec 007 FR-CRM-TASK-01, -02, FR-CRM-NOTE-01 to -03,
FR-CRM-LINK-01 (tasks, notes, deals), FR-CRM-REC-03; Q-27, Q-53, Q-57, Q-63;
contracts §2.5.

A task links to at least one record and is assigned to a member; a Viewer
may mark a task assigned to them done, and change nothing else. A note links
to any number of records, a typed note logs an activity on its date, and
only members can be mentioned.
"""

from __future__ import annotations

import asyncio

import pytest
from crm_fake import MEMBER, OWNER, SELLER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.mcp import crm as tools

LABS = "?workspace=labs"
DRIVE = "https://drive.google.com/file/d/abc/view"


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.add_deal_lists(labs)
    http = crm_client(monkeypatch, fake=fake)
    me = as_(OWNER["id"])
    company = http.post(f"/api/v1/crm/companies{LABS}", json={"name": "Acme"}, headers=me).json()
    person = http.post(f"/api/v1/crm/people{LABS}", json={"first_name": "Deepa"}, headers=me).json()
    http.patch(
        f"/api/v1/crm/companies/{company['id']}{LABS}", json={"lead_stage": "qualified"}, headers=me
    )
    deal = http.post(
        f"/api/v1/crm/opportunities{LABS}",
        json={
            "name": "Acme - AIOps",
            "company_id": company["id"],
            "next_step": "Call",
            "next_step_on": "2026-10-20",
        },
        headers=me,
    ).json()
    made = {"company": company, "person": person, "opportunity": deal}
    return http, fake, labs, made


def _links(*records):
    return [{"record_type": r["record_type"], "record_id": r["id"]} for r in records]


def _task(http, links, who=OWNER["id"], **fields):
    return http.post(
        f"/api/v1/crm/tasks{LABS}",
        json={"title": "Send the deck", "links": links, **fields},
        headers=as_(who),
    )


def _note(http, links, who=OWNER["id"], **fields):
    return http.post(
        f"/api/v1/crm/notes{LABS}",
        json={"body": "Met the **CTO**.", "links": links, **fields},
        headers=as_(who),
    )


class TestTasks:
    """FR-CRM-TASK-01, -02."""

    def test_a_task_needs_a_link(self, crm):
        http, fake, *_ = crm
        for links in ([], None):
            body = {"title": "Orphan"} | ({"links": links} if links is not None else {})
            r = http.post(f"/api/v1/crm/tasks{LABS}", json=body, headers=as_(OWNER["id"]))
            assert (r.status_code, r.json()["detail"]) == (
                422,
                "A task must be linked to at least one record.",
            )
        assert fake.rows["crm_tasks"] == {}

    def test_created_with_its_links_and_the_caller_as_assignee(self, crm):
        http, _, _, made = crm
        r = _task(http, _links(made["company"], made["opportunity"]), who=MEMBER["id"])
        assert r.status_code == 201, r.text
        body = r.json()
        assert (body["status"], body["assignee_id"], body["assignee_name"]) == (
            "todo",
            MEMBER["id"],
            "Deepika",
        )
        assert [(lk["record_type"], lk["name"]) for lk in body["links"]] == [
            ("company", "Acme"),
            ("opportunity", "Acme - AIOps"),
        ]

    def test_the_assignee_must_be_a_member(self, crm):
        http, _, _, made = crm
        r = _task(http, _links(made["company"]), assignee_id="u-outsider")
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "The assignee must be a member of this workspace.",
        )

    def test_a_link_to_another_workspaces_record_is_not_found(self, crm):
        http, *_ = crm
        other = http.post(
            "/api/v1/crm/companies?workspace=academy",
            json={"name": "College"},
            headers=as_(OWNER["id"]),
        ).json()
        r = _task(http, [{"record_type": "company", "record_id": other["id"]}])
        assert (r.status_code, r.json()["detail"]) == (404, "No such company.")

    def test_a_campaign_link_waits_for_campaigns(self, crm):
        http, _, _, made = crm
        r = _task(http, [{"record_type": "campaign", "record_id": made["company"]["id"]}])
        assert r.status_code == 404

    def test_the_category_by_name(self, crm):
        http, _, _, made = crm
        r = _task(http, _links(made["person"]), category="sales")
        assert r.json()["category_label"] == "Sales"
        r = _task(http, _links(made["person"]), category="Lunch")
        assert r.json()["detail"] == "“Lunch” is not a task category in this workspace."

    def test_done_stamps_completed_at_and_reopening_clears_it(self, crm):
        http, fake, _, made = crm
        task = _task(http, _links(made["person"])).json()
        r = http.patch(
            f"/api/v1/crm/tasks/{task['id']}{LABS}",
            json={"status": "done"},
            headers=as_(OWNER["id"]),
        )
        assert r.json()["completed_at"]
        r = http.patch(
            f"/api/v1/crm/tasks/{task['id']}{LABS}",
            json={"status": "in_progress"},
            headers=as_(OWNER["id"]),
        )
        assert r.json()["completed_at"] is None

    def test_links_can_be_replaced_but_not_emptied(self, crm):
        http, fake, _, made = crm
        task = _task(http, _links(made["person"])).json()
        r = http.patch(
            f"/api/v1/crm/tasks/{task['id']}{LABS}",
            json={"links": _links(made["company"])},
            headers=as_(OWNER["id"]),
        )
        assert [lk["record_type"] for lk in r.json()["links"]] == ["company"]
        r = http.patch(
            f"/api/v1/crm/tasks/{task['id']}{LABS}", json={"links": []}, headers=as_(OWNER["id"])
        )
        assert r.status_code == 422

    def test_on_the_record_page(self, crm):
        http, _, _, made = crm
        _task(http, _links(made["opportunity"]))
        page = http.get(
            f"/api/v1/crm/opportunities/{made['opportunity']['id']}{LABS}",
            headers=as_(VIEWER["id"]),
        ).json()
        assert [t["title"] for t in page["tasks"]] == ["Send the deck"]
        timeline = page["timeline_head"]
        assert timeline[0]["type"] == "task"
        assert timeline[0]["summary"] == "Added task “Send the deck”"

    def test_the_tasks_list_by_assignee_and_status(self, crm):
        http, _, _, made = crm
        mine = _task(http, _links(made["person"]), assignee_id=MEMBER["id"]).json()
        _task(http, _links(made["person"]), assignee_id=SELLER["id"])
        done = _task(http, _links(made["person"]), assignee_id=MEMBER["id"], status="done").json()
        r = http.get(
            f"/api/v1/crm/tasks{LABS}&assignee_id={MEMBER['id']}&status=open",
            headers=as_(MEMBER["id"]),
        )
        assert [t["id"] for t in r.json()["rows"]] == [mine["id"]]
        r = http.get(f"/api/v1/crm/tasks{LABS}&status=done", headers=as_(MEMBER["id"]))
        assert [t["id"] for t in r.json()["rows"]] == [done["id"]]


class TestViewerTasks:
    """Q-57 — a Viewer marks a task assigned to them done, status only."""

    def _theirs(self, http, made, assignee=VIEWER["id"]):
        return _task(http, _links(made["person"]), assignee_id=assignee).json()

    def test_complete_their_own(self, crm):
        http, fake, _, made = crm
        task = self._theirs(http, made)
        r = http.post(f"/api/v1/crm/tasks/{task['id']}/complete{LABS}", headers=as_(VIEWER["id"]))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "done"
        assert fake.audit[-1]["actor_id"] == VIEWER["id"]

    def test_status_through_patch(self, crm):
        http, _, _, made = crm
        task = self._theirs(http, made)
        r = http.patch(
            f"/api/v1/crm/tasks/{task['id']}{LABS}",
            json={"status": "in_progress", "expect_updated_at": task["updated_at"]},
            headers=as_(VIEWER["id"]),
        )
        assert r.status_code == 200, r.text

    def test_nothing_else(self, crm):
        http, _, _, made = crm
        task = self._theirs(http, made)
        r = http.patch(
            f"/api/v1/crm/tasks/{task['id']}{LABS}",
            json={"status": "done", "title": "Mine now"},
            headers=as_(VIEWER["id"]),
        )
        assert (r.status_code, r.json()["detail"]) == (
            403,
            "Viewers may change only the status of a task assigned to them.",
        )

    def test_not_someone_elses(self, crm):
        http, _, _, made = crm
        task = self._theirs(http, made, assignee=MEMBER["id"])
        r = http.post(f"/api/v1/crm/tasks/{task['id']}/complete{LABS}", headers=as_(VIEWER["id"]))
        assert r.status_code == 403
        r = http.patch(
            f"/api/v1/crm/tasks/{task['id']}{LABS}",
            json={"status": "done"},
            headers=as_(VIEWER["id"]),
        )
        assert r.status_code == 403

    def test_a_viewer_creates_no_task(self, crm):
        http, _, _, made = crm
        assert _task(http, _links(made["person"]), who=VIEWER["id"]).status_code == 403

    def test_through_mcp(self, crm):
        http, _, _, made = crm
        task = self._theirs(http, made)
        done = asyncio.run(tools.crm_complete_task(Ctx(VIEWER["id"]), task["id"], workspace="labs"))
        assert done["status"] == "done"


class TestNotes:
    """FR-CRM-NOTE-01 to -03."""

    def test_a_note_links_to_many_records(self, crm):
        http, _, _, made = crm
        r = _note(http, _links(made["company"], made["person"], made["opportunity"]))
        assert r.status_code == 201, r.text
        assert [lk["name"] for lk in r.json()["links"]] == ["Acme", "Deepa", "Acme - AIOps"]
        for record in made.values():
            plural = {"company": "companies", "person": "people", "opportunity": "opportunities"}
            page = http.get(
                f"/api/v1/crm/{plural[record['record_type']]}/{record['id']}{LABS}",
                headers=as_(OWNER["id"]),
            ).json()
            assert [n["body"] for n in page["notes"]] == ["Met the **CTO**."]

    def test_log_activity_needs_a_date(self, crm):
        http, _, _, made = crm
        r = _note(http, _links(made["person"]), kind="call")
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Log activity needs the date it happened.",
        )
        r = _note(http, _links(made["person"]), kind="whatsapp", occurred_on="2026-10-13")
        assert r.status_code == 201
        assert (r.json()["kind_label"], r.json()["occurred_on"]) == ("WhatsApp", "2026-10-13")

    def test_logged_activity_on_the_timeline(self, crm):
        http, _, _, made = crm
        _note(http, _links(made["person"]), kind="meeting", occurred_on="2026-10-12")
        timeline = http.get(
            f"/api/v1/crm/people/{made['person']['id']}/timeline{LABS}", headers=as_(OWNER["id"])
        ).json()
        assert timeline[0]["type"] == "note"
        assert timeline[0]["summary"] == "Logged a meeting on 2026-10-12"

    def test_mentioning_a_non_member_is_refused(self, crm):
        """Q-63."""
        http, fake, _, made = crm
        r = _note(http, _links(made["person"]), mention_ids=["u-outsider"])
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Vinita is not a member of this workspace; only members can be mentioned.",
        )
        assert fake.rows["crm_notes"] == {}

    def test_mentioning_members(self, crm):
        http, _, _, made = crm
        r = _note(http, _links(made["person"]), mention_ids=[MEMBER["id"], SELLER["id"]])
        assert [m["name"] for m in r.json()["mentions"]] == ["Deepika", "Tamil"]

    def test_a_blank_note_is_refused(self, crm):
        http, _, _, made = crm
        r = _note(http, _links(made["person"]), body="   ")
        assert (r.status_code, r.json()["detail"]) == (422, "A note needs text.")

    def test_edited_with_new_links_and_mentions(self, crm):
        http, _, _, made = crm
        note = _note(http, _links(made["person"]), mention_ids=[MEMBER["id"]]).json()
        r = http.patch(
            f"/api/v1/crm/notes/{note['id']}{LABS}",
            json={"links": _links(made["company"]), "mention_ids": []},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert [lk["record_type"] for lk in r.json()["links"]] == ["company"]
        assert r.json()["mentions"] == []

    def test_through_mcp(self, crm):
        http, _, _, made = crm
        note = asyncio.run(
            tools.crm_add_note(
                Ctx(MEMBER["id"]),
                "Called; wants a demo.",
                _links(made["opportunity"]),
                kind="call",
                occurred_on="2026-10-14",
                workspace="labs",
            )
        )
        assert (note["kind"], note["links"][0]["record_type"]) == ("call", "opportunity")


class TestDriveLinks:
    """FR-CRM-LINK-01 — on deals, tasks and notes too."""

    def test_on_each(self, crm):
        http, _, _, made = crm
        task = _task(http, _links(made["person"])).json()
        note = _note(http, _links(made["person"])).json()
        for record_type, record_id in (
            ("opportunity", made["opportunity"]["id"]),
            ("task", task["id"]),
            ("note", note["id"]),
        ):
            r = http.post(
                f"/api/v1/crm/links{LABS}",
                json={
                    "record_type": record_type,
                    "record_id": record_id,
                    "url": DRIVE,
                    "label": "SOW",
                },
                headers=as_(MEMBER["id"]),
            )
            assert r.status_code == 201, (record_type, r.text)


class TestTrash:
    """FR-CRM-DEL-01, -04 for tasks and notes."""

    def test_a_member_deletes_only_what_they_created(self, crm):
        http, _, _, made = crm
        task = _task(http, _links(made["person"]), who=SELLER["id"]).json()
        r = http.delete(f"/api/v1/crm/tasks/{task['id']}{LABS}", headers=as_(MEMBER["id"]))
        assert r.status_code == 403
        r = http.delete(f"/api/v1/crm/tasks/{task['id']}{LABS}", headers=as_(SELLER["id"]))
        assert r.status_code == 200

    def test_purge_takes_its_links(self, crm):
        http, fake, _, made = crm
        note = _note(http, _links(made["person"]), mention_ids=[MEMBER["id"]]).json()
        http.delete(f"/api/v1/crm/notes/{note['id']}{LABS}", headers=as_(OWNER["id"]))
        trash = http.get(f"/api/v1/crm/trash{LABS}&object=notes", headers=as_(OWNER["id"])).json()
        assert [t["name"] for t in trash["rows"]] == ["Met the **CTO**."]
        r = http.delete(f"/api/v1/crm/notes/{note['id']}/permanent{LABS}", headers=as_(OWNER["id"]))
        assert r.status_code == 200
        assert fake.rows["crm_note_links"] == {} and fake.rows["crm_note_mentions"] == {}

    def test_purging_a_record_removes_the_links_to_it(self, crm):
        http, fake, _, made = crm
        _task(http, _links(made["person"], made["company"]))
        http.delete(f"/api/v1/crm/people/{made['person']['id']}{LABS}", headers=as_(OWNER["id"]))
        http.delete(
            f"/api/v1/crm/people/{made['person']['id']}/permanent{LABS}", headers=as_(OWNER["id"])
        )
        assert [lk["record_type"] for lk in fake.rows["crm_task_links"].values()] == ["company"]
