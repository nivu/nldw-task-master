"""Workspace isolation for records — spec 007 FR-CRM-WS-02; spec §11 AC
"another workspace's record id is answered 404".

The owner is an Admin of both workspaces, so nothing here is refused for
role: a record of Academy named while acting in Labs is simply not found, and
Labs's lists contain nothing of Academy. A record id is answered 404 whether
it is absent or belongs to another workspace, so its existence is never
disclosed. Later slices add their objects' routes here; S4 added deals,
tasks, notes, deal contacts and stage moves, S5 the handoff and project
links, S10 the Emails tab and `crm_get`'s emails (FR-CRM-SYNC-10).
"""

from __future__ import annotations

import pytest
from crm_fake import OWNER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

LABS = "?workspace=labs"
ACADEMY = "?workspace=academy"


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    two_workspaces(fake)
    http = crm_client(monkeypatch, fake=fake)
    me = as_(OWNER["id"])
    company = http.post(
        f"/api/v1/crm/companies{ACADEMY}",
        json={"name": "Academy College", "domain": "college.edu"},
        headers=me,
    ).json()
    person = http.post(
        f"/api/v1/crm/people{ACADEMY}",
        json={"first_name": "Priya", "company_id": company["id"], "primary_email": "p@college.edu"},
        headers=me,
    ).json()
    link = http.post(
        f"/api/v1/crm/links{ACADEMY}",
        json={
            "record_type": "company",
            "record_id": company["id"],
            "url": "https://drive.google.com/a",
            "label": "MoU",
        },
        headers=me,
    ).json()
    trashed = http.post(
        f"/api/v1/crm/companies{ACADEMY}", json={"name": "Gone College"}, headers=me
    ).json()
    http.delete(f"/api/v1/crm/companies/{trashed['id']}{ACADEMY}", headers=me)
    http.patch(
        f"/api/v1/crm/companies/{company['id']}{ACADEMY}",
        json={"lead_stage": "qualified"},
        headers=me,
    )
    deal = http.post(
        f"/api/v1/crm/opportunities{ACADEMY}",
        json={
            "name": "College - AI course",
            "company_id": company["id"],
            "next_step": "Call",
            "next_step_on": "2026-10-20",
        },
        headers=me,
    ).json()
    links = [{"record_type": "opportunity", "record_id": deal["id"]}]
    task = http.post(
        f"/api/v1/crm/tasks{ACADEMY}", json={"title": "Call", "links": links}, headers=me
    ).json()
    note = http.post(
        f"/api/v1/crm/notes{ACADEMY}", json={"body": "Met them.", "links": links}, headers=me
    ).json()
    return http, {
        "companies": company,
        "people": person,
        "link": link,
        "trashed": trashed,
        "opportunities": deal,
        "tasks": task,
        "notes": note,
    }


def _routes(plural: str, record_id: str) -> list[tuple[str, str, dict | None]]:
    base = f"/api/v1/crm/{plural}/{record_id}"
    return [
        ("GET", f"{base}{LABS}", None),
        ("PATCH", f"{base}{LABS}", {"city": "X"}),
        ("DELETE", f"{base}{LABS}", None),
        ("POST", f"{base}/restore{LABS}", None),
        ("DELETE", f"{base}/permanent{LABS}", None),
        ("GET", f"{base}/timeline{LABS}", None),
    ]


class TestForeignIdsAreNotFound:
    @pytest.mark.parametrize("plural", ["companies", "people", "opportunities", "tasks", "notes"])
    def test_every_record_route(self, crm, plural):
        http, made = crm
        for method, url, body in _routes(plural, made[plural]["id"]):
            r = http.request(method, url, json=body, headers=as_(OWNER["id"]))
            assert r.status_code == 404, (method, url, r.text)

    def test_deal_contacts_and_stage_moves(self, crm):
        http, made = crm
        deal, person = made["opportunities"]["id"], made["people"]["id"]
        base = f"/api/v1/crm/opportunities/{deal}"
        for method, url, body in [
            ("POST", f"{base}/stage{LABS}", {"stage": "closed_lost"}),
            ("POST", f"{base}/stage/preview{LABS}", {"stage": "qualification"}),
            ("POST", f"{base}/contacts{LABS}", {"person_id": person}),
            ("DELETE", f"{base}/contacts/{person}{LABS}", None),
        ]:
            r = http.request(method, url, json=body, headers=as_(OWNER["id"]))
            assert r.status_code == 404, (method, url, r.text)

    def test_the_handoff_and_project_links(self, crm):
        """S5: an Academy deal named from Labs is not found there either."""
        http, made = crm
        base = f"/api/v1/crm/opportunities/{made['opportunities']['id']}"
        for method, url, body in [
            ("GET", f"{base}/handoff{LABS}", None),
            ("POST", f"{base}/handoff{LABS}", {"lead_id": OWNER["id"], "phases": {}}),
            ("POST", f"{base}/link-project{LABS}", {"mode": "create"}),
        ]:
            r = http.request(method, url, json=body, headers=as_(OWNER["id"]))
            assert r.status_code == 404, (method, url, r.text)

    def test_completing_a_task(self, crm):
        http, made = crm
        r = http.post(
            f"/api/v1/crm/tasks/{made['tasks']['id']}/complete{LABS}", headers=as_(OWNER["id"])
        )
        assert r.status_code == 404

    def test_references_to_its_records(self, crm):
        """A Labs deal, task or note naming an Academy record."""
        http, made = crm
        me = as_(OWNER["id"])
        labs_company = http.post(
            f"/api/v1/crm/companies{LABS}", json={"name": "Acme"}, headers=me
        ).json()
        http.patch(
            f"/api/v1/crm/companies/{labs_company['id']}{LABS}",
            json={"lead_stage": "qualified"},
            headers=me,
        )
        for url, body in [
            (
                f"/api/v1/crm/opportunities{LABS}",
                {
                    "name": "X",
                    "company_id": made["companies"]["id"],
                    "next_step": "Call",
                    "next_step_on": "2026-10-20",
                },
            ),
            (
                f"/api/v1/crm/opportunities{LABS}",
                {
                    "name": "X",
                    "company_id": labs_company["id"],
                    "point_of_contact_id": made["people"]["id"],
                    "next_step": "Call",
                    "next_step_on": "2026-10-20",
                },
            ),
            (
                f"/api/v1/crm/tasks{LABS}",
                {
                    "title": "X",
                    "links": [
                        {"record_type": "opportunity", "record_id": made["opportunities"]["id"]}
                    ],
                },
            ),
            (
                f"/api/v1/crm/notes{LABS}",
                {
                    "body": "X",
                    "links": [{"record_type": "person", "record_id": made["people"]["id"]}],
                },
            ),
        ]:
            r = http.post(url, json=body, headers=me)
            assert r.status_code == 404, (url, r.text)

    def test_a_trashed_record_of_another_workspace(self, crm):
        http, made = crm
        for method, url, body in _routes("companies", made["trashed"]["id"]):
            r = http.request(method, url, json=body, headers=as_(OWNER["id"]))
            assert r.status_code == 404, (method, url)

    def test_links(self, crm):
        http, made = crm
        r = http.post(
            f"/api/v1/crm/links{LABS}",
            json={
                "record_type": "company",
                "record_id": made["companies"]["id"],
                "url": "https://drive.google.com/b",
                "label": "Copy",
            },
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 404
        r = http.delete(f"/api/v1/crm/links/{made['link']['id']}{LABS}", headers=as_(OWNER["id"]))
        assert r.status_code == 404

    def test_references_on_a_write(self, crm):
        http, made = crm
        r = http.post(
            f"/api/v1/crm/people{LABS}",
            json={"first_name": "Ravi", "company_id": made["companies"]["id"]},
            headers=as_(OWNER["id"]),
        )
        assert (r.status_code, r.json()["detail"]) == (404, "No such company.")

    def test_the_same_answer_as_an_unknown_id(self, crm):
        http, made = crm
        foreign = http.get(
            f"/api/v1/crm/companies/{made['companies']['id']}{LABS}", headers=as_(OWNER["id"])
        )
        unknown = http.get(
            f"/api/v1/crm/companies/00000000-0000-0000-0000-000000000000{LABS}",
            headers=as_(OWNER["id"]),
        )
        assert foreign.json()["detail"] == unknown.json()["detail"] == "No such company."


class TestListsHoldNothingOfIt:
    @pytest.mark.parametrize("plural", ["companies", "people", "opportunities", "tasks", "notes"])
    def test_plain_lists(self, crm, plural):
        http, _ = crm
        listed = http.get(f"/api/v1/crm/{plural}{LABS}", headers=as_(OWNER["id"])).json()
        assert listed["rows"] == []

    def test_the_trash(self, crm):
        http, _ = crm
        listed = http.get(f"/api/v1/crm/trash{LABS}", headers=as_(OWNER["id"])).json()
        assert listed["rows"] == []

    def test_duplicates(self, crm):
        http, _ = crm
        r = http.post(
            f"/api/v1/crm/companies/duplicates{LABS}",
            json={"name": "Academy College", "domain": "college.edu"},
            headers=as_(OWNER["id"]),
        )
        assert r.json() == {"exact": None, "similar": []}
        r = http.post(
            f"/api/v1/crm/people/duplicates{LABS}",
            json={"name": "Priya", "email": "p@college.edu"},
            headers=as_(OWNER["id"]),
        )
        assert r.json() == {"exact": None, "similar": []}


def _fake():
    """The FakeCrmStore the fixture installed (its methods replace the store's)."""
    from app.services.crm import store

    return store.get_record.__self__


def _email(fake, workspace_id: str, record_type: str, record_id: str) -> dict:
    """A message stored by Gmail sync (S10), linked to one record."""
    mailbox = fake.add(
        "crm_mailboxes",
        workspace_id=workspace_id,
        email=f"box{len(fake.rows['crm_mailboxes'])}@nunnari.academy",
        owner_id=OWNER["id"],
        labels=["Leads"],
        is_active=True,
    )
    msg = fake.add(
        "crm_email_messages",
        workspace_id=workspace_id,
        mailbox_id=mailbox["id"],
        gmail_message_id="g1",
        thread_id="t-academy",
        subject="College intake",
        sent_at="2026-10-13T09:00:00+00:00",
        direction="inbound",
        from_email="p@college.edu",
        participants=[{"email": "p@college.edu", "name": "Priya", "role": "from"}],
        is_internal=False,
        is_auto=False,
    )
    fake.add(
        "crm_email_links",
        workspace_id=workspace_id,
        message_id=msg["id"],
        record_type=record_type,
        record_id=record_id,
    )
    return msg


class TestEmails:
    """S10, FR-CRM-SYNC-10 — email records are visible only to members of
    their workspace: the Emails tab and `crm_get`'s emails."""

    @pytest.mark.parametrize("plural", ["companies", "people", "opportunities"])
    def test_another_workspaces_record_has_no_emails_tab_here(self, crm, plural):
        http, made = crm
        r = http.get(
            f"/api/v1/crm/{plural}/{made[plural]['id']}/emails{LABS}", headers=as_(OWNER["id"])
        )
        assert r.status_code == 404, r.text

    def test_a_non_member_is_not_told_the_workspace_exists(self, crm):
        from crm_fake import OUTSIDER

        http, made = crm
        r = http.get(
            f"/api/v1/crm/companies/{made['companies']['id']}/emails{ACADEMY}",
            headers=as_(OUTSIDER["id"]),
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "No such workspace."

    def test_a_labs_record_never_shows_an_academy_email(self, crm, monkeypatch):
        from app.services.crm import store

        http, made = crm
        academy = next(w for w in store.list_workspaces() if w["key"] == "academy")
        fake = _fake()
        _email(fake, academy["id"], "company", made["companies"]["id"])
        labs_co = http.post(
            f"/api/v1/crm/companies{LABS}", json={"name": "Labs Co"}, headers=as_(OWNER["id"])
        ).json()
        here = http.get(
            f"/api/v1/crm/companies/{labs_co['id']}/emails{LABS}", headers=as_(OWNER["id"])
        ).json()
        assert here["threads"] == []
        page = http.get(f"/api/v1/crm/companies/{labs_co['id']}{LABS}", headers=as_(OWNER["id"]))
        assert page.json()["emails"] == []
        # In its own workspace it is there.
        there = http.get(
            f"/api/v1/crm/companies/{made['companies']['id']}/emails{ACADEMY}",
            headers=as_(OWNER["id"]),
        ).json()
        assert [t["thread_id"] for t in there["threads"]] == ["t-academy"]

    def test_a_link_to_another_workspaces_record_is_refused(self, crm):
        from app.services.crm import CrmRefused, store

        http, made = crm
        workspaces = {w["key"]: w for w in store.list_workspaces()}
        fake = _fake()
        labs_box = fake.add(
            "crm_mailboxes",
            workspace_id=workspaces["labs"]["id"],
            email="owner@nunnarilabs.com",
            owner_id=OWNER["id"],
            labels=["Sales"],
            is_active=True,
        )
        with pytest.raises(CrmRefused) as refused:
            store.store_emails(
                workspaces["labs"]["id"],
                labs_box["id"],
                [
                    {
                        "gmail_message_id": "x",
                        "thread_id": "t",
                        "subject": None,
                        "sent_at": "2026-10-13T09:00:00+00:00",
                        "direction": "inbound",
                        "from_email": "p@college.edu",
                        "participants": [],
                        "is_internal": False,
                        "is_auto": False,
                        "links": [{"record_type": "company", "record_id": made["companies"]["id"]}],
                    }
                ],
            )
        assert refused.value.status == 404
        assert fake.rows["crm_email_messages"] == {}
