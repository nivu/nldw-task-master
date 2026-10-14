"""Workspace isolation for records — spec 007 FR-CRM-WS-02; spec §11 AC
"another workspace's record id is answered 404".

The owner is an Admin of both workspaces, so nothing here is refused for
role: a record of Academy named while acting in Labs is simply not found, and
Labs's lists contain nothing of Academy. A record id is answered 404 whether
it is absent or belongs to another workspace, so its existence is never
disclosed. Later slices add their objects' routes here.
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
    return http, {"companies": company, "people": person, "link": link, "trashed": trashed}


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
    @pytest.mark.parametrize("plural", ["companies", "people"])
    def test_every_record_route(self, crm, plural):
        http, made = crm
        for method, url, body in _routes(plural, made[plural]["id"]):
            r = http.request(method, url, json=body, headers=as_(OWNER["id"]))
            assert r.status_code == 404, (method, url, r.text)

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
    @pytest.mark.parametrize("plural", ["companies", "people"])
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
