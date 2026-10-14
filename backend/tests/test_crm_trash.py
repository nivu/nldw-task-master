"""Deletion and the Trash — spec 007 FR-CRM-DEL-01, -02, -04; Q-36, Q-54;
contracts §2.5 `DELETE`, `/restore`, `/permanent`, `GET /crm/trash`; plan
§5.11 (`crm.purge_trash`).

Deletion is soft. Everything one action deletes shares a `deleted_group`, so
an Admin's restore brings it back together; after 90 days the daily job
purges it, and an Admin may purge sooner.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from crm_fake import MEMBER, OWNER, SELLER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.mcp import crm as tools
from app.tasks import crm as jobs

LABS = "?workspace=labs"


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    return crm_client(monkeypatch, fake=fake), fake, labs, academy


def _company(http, who=OWNER["id"], **fields):
    return http.post(
        f"/api/v1/crm/companies{LABS}", json={"name": "Acme", **fields}, headers=as_(who)
    ).json()


def _person(http, who=OWNER["id"], **fields):
    return http.post(
        f"/api/v1/crm/people{LABS}", json={"first_name": "Deepa", **fields}, headers=as_(who)
    ).json()


def _delete(http, plural, record_id, who=OWNER["id"], body=None):
    return http.request(
        "DELETE", f"/api/v1/crm/{plural}/{record_id}{LABS}", json=body, headers=as_(who)
    )


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


class TestWhoMayDelete:
    """FR-CRM-DEL-04, Q-36."""

    def test_a_member_deletes_what_they_own(self, crm):
        http, *_ = crm
        made = _company(http, who=MEMBER["id"])
        assert _delete(http, "companies", made["id"], who=MEMBER["id"]).status_code == 200

    def test_a_member_cannot_delete_someone_elses(self, crm):
        http, *_ = crm
        made = _company(http, owner_id=SELLER["id"])
        r = _delete(http, "companies", made["id"], who=MEMBER["id"])
        assert r.status_code == 403
        assert r.json()["detail"] == "Only a workspace admin or its owner can delete this company."

    def test_with_no_owner_the_creator_may(self, crm):
        http, *_ = crm
        mine = _person(http, who=MEMBER["id"])
        theirs = _person(http, who=SELLER["id"], first_name="Ravi")
        assert _delete(http, "people", mine["id"], who=MEMBER["id"]).status_code == 200
        assert _delete(http, "people", theirs["id"], who=MEMBER["id"]).status_code == 403

    def test_an_admin_deletes_anything(self, crm):
        http, *_ = crm
        made = _company(http, owner_id=SELLER["id"])
        assert _delete(http, "companies", made["id"]).status_code == 200

    def test_a_viewer_deletes_nothing(self, crm):
        http, *_ = crm
        made = _company(http)
        assert _delete(http, "companies", made["id"], who=VIEWER["id"]).status_code == 403


class TestDeletedRecordsLeave:
    """FR-CRM-DEL-01 — every view, list and MCP answer."""

    def test_from_the_record_page_the_list_and_mcp(self, crm):
        http, *_ = crm
        made = _company(http)
        _delete(http, "companies", made["id"])
        r = http.get(f"/api/v1/crm/companies/{made['id']}{LABS}", headers=as_(OWNER["id"]))
        assert (r.status_code, r.json()["detail"]) == (404, "No such company.")
        listed = http.get(f"/api/v1/crm/companies{LABS}", headers=as_(OWNER["id"])).json()
        assert listed["rows"] == []
        with pytest.raises(Exception) as refused:
            asyncio.run(tools.crm_get(Ctx(OWNER["id"]), "company", made["id"], workspace="labs"))
        assert str(refused.value) == "404: No such company."

    def test_a_trashed_record_cannot_be_edited(self, crm):
        http, *_ = crm
        made = _company(http)
        _delete(http, "companies", made["id"])
        r = http.patch(
            f"/api/v1/crm/companies/{made['id']}{LABS}",
            json={"city": "X"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 404


class TestCompanyPeople:
    """FR-CRM-DEL-02, Q-54 — deleting a company asks about its people."""

    def test_the_choice_is_required(self, crm):
        http, *_ = crm
        company = _company(http)
        _person(http, company_id=company["id"])
        _person(http, first_name="Ravi", company_id=company["id"])
        r = _delete(http, "companies", company["id"])
        assert r.status_code == 422
        assert r.json()["detail"] == (
            "Acme has 2 people; say whether to unlink them or delete them too."
        )

    def test_unlink_keeps_the_people(self, crm):
        http, fake, *_ = crm
        company = _company(http)
        person = _person(http, company_id=company["id"])
        r = _delete(http, "companies", company["id"], body={"people": "unlink"})
        assert r.status_code == 200
        row = fake.rows["crm_people"][person["id"]]
        assert (row["company_id"], row["deleted_at"]) == (None, None)

    def test_delete_takes_them_in_the_same_group(self, crm):
        http, fake, *_ = crm
        company = _company(http)
        person = _person(http, company_id=company["id"])
        group = _delete(http, "companies", company["id"], body={"people": "delete"}).json()[
            "deleted_group"
        ]
        assert fake.rows["crm_people"][person["id"]]["deleted_group"] == group
        assert fake.rows["crm_companies"][company["id"]]["deleted_group"] == group


class TestRestore:
    """FR-CRM-DEL-01, -02 — Admins restore, and the group comes back."""

    def test_the_group_comes_back(self, crm):
        http, fake, *_ = crm
        company = _company(http)
        person = _person(http, company_id=company["id"])
        _delete(http, "companies", company["id"], body={"people": "delete"})
        r = http.post(
            f"/api/v1/crm/companies/{company['id']}/restore{LABS}", headers=as_(OWNER["id"])
        )
        assert r.status_code == 200 and r.json()["deleted_at"] is None
        assert fake.rows["crm_people"][person["id"]]["deleted_at"] is None
        assert fake.rows["crm_people"][person["id"]]["company_id"] == company["id"]

    def test_members_cannot_restore(self, crm):
        http, *_ = crm
        made = _company(http, who=MEMBER["id"])
        _delete(http, "companies", made["id"], who=MEMBER["id"])
        r = http.post(
            f"/api/v1/crm/companies/{made['id']}/restore{LABS}", headers=as_(MEMBER["id"])
        )
        assert r.status_code == 403

    def test_a_live_record_is_not_in_the_trash(self, crm):
        http, *_ = crm
        made = _company(http)
        r = http.post(f"/api/v1/crm/companies/{made['id']}/restore{LABS}", headers=as_(OWNER["id"]))
        assert (r.status_code, r.json()["detail"]) == (404, "No such company in the Trash.")


class TestTrashList:
    def test_admins_see_days_left(self, crm):
        http, fake, *_ = crm
        made = _company(http)
        _delete(http, "companies", made["id"])
        ten_days_ago = (datetime.now(UTC) - timedelta(days=10, hours=1)).isoformat()
        fake.rows["crm_companies"][made["id"]]["deleted_at"] = ten_days_ago
        r = http.get(f"/api/v1/crm/trash{LABS}&object=companies", headers=as_(OWNER["id"]))
        row = r.json()["rows"][0]
        assert (row["name"], row["deleted_by_name"], row["days_left"]) == ("Acme", "Owner", 80)

    def test_members_do_not(self, crm):
        http, *_ = crm
        assert http.get(f"/api/v1/crm/trash{LABS}", headers=as_(MEMBER["id"])).status_code == 403


class TestPurge:
    """FR-CRM-DEL-01 — Admin only before 90 days; the job after."""

    def test_permanent_delete_is_admin_only(self, crm):
        http, fake, *_ = crm
        made = _company(http, who=MEMBER["id"])
        _delete(http, "companies", made["id"], who=MEMBER["id"])
        url = f"/api/v1/crm/companies/{made['id']}/permanent{LABS}"
        assert http.delete(url, headers=as_(MEMBER["id"])).status_code == 403
        r = http.delete(url, headers=as_(OWNER["id"]))
        assert (r.status_code, r.json()) == (200, {"purged": 1})
        assert made["id"] not in fake.rows["crm_companies"]

    def test_only_from_the_trash(self, crm):
        http, fake, *_ = crm
        made = _company(http)
        r = http.delete(
            f"/api/v1/crm/companies/{made['id']}/permanent{LABS}", headers=as_(OWNER["id"])
        )
        assert r.status_code == 404
        assert made["id"] in fake.rows["crm_companies"]

    def test_purging_takes_the_drive_links_and_domains(self, crm):
        http, fake, *_ = crm
        made = _company(http, domain="acme.com")
        http.post(
            f"/api/v1/crm/links{LABS}",
            json={
                "record_type": "company",
                "record_id": made["id"],
                "url": "https://drive.google.com/x",
                "label": "SOW",
            },
            headers=as_(OWNER["id"]),
        )
        _delete(http, "companies", made["id"])
        http.delete(f"/api/v1/crm/companies/{made['id']}/permanent{LABS}", headers=as_(OWNER["id"]))
        assert fake.rows["crm_drive_links"] == {}
        assert fake.rows["crm_company_domains"] == {}

    def test_the_job_purges_after_90_days(self, crm):
        http, fake, labs, _ = crm
        old = _company(http, name="Old")
        recent = _company(http, name="Recent")
        _delete(http, "companies", old["id"])
        _delete(http, "companies", recent["id"])
        now = datetime.now(UTC)
        fake.rows["crm_companies"][old["id"]]["deleted_at"] = (now - timedelta(days=91)).isoformat()
        fake.rows["crm_companies"][recent["id"]]["deleted_at"] = (
            now - timedelta(days=89)
        ).isoformat()

        result = jobs.purge_trash()
        assert result == {"purged": {"labs": 1, "academy": 0}, "failed": []}
        assert old["id"] not in fake.rows["crm_companies"]
        assert recent["id"] in fake.rows["crm_companies"]
        actor, _ = fake.applied[-1]
        assert (actor["kind"], actor["user_id"], actor["workspace_id"]) == (
            "system",
            None,
            labs["id"],
        )

    def test_one_workspace_failing_does_not_stop_the_others(self, crm, monkeypatch):
        _, fake, *_ = crm
        calls = []

        def purge(workspace_id, now):
            calls.append(workspace_id)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return 0

        monkeypatch.setattr(jobs.records, "purge_expired", purge)
        result = jobs.purge_trash()
        assert len(calls) == 2 and len(result["failed"]) == 1

    def test_it_runs_nightly(self):
        from app.worker import celery_app

        entry = celery_app.conf.beat_schedule["crm-purge-trash"]
        assert entry["task"] == "crm.purge_trash"
        assert (entry["schedule"].hour, entry["schedule"].minute) == ({1}, {10})
