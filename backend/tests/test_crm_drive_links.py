"""Google Drive links — spec 007 FR-CRM-LINK-01 to -03; owner decision D4;
contracts §2.5 `POST /crm/links`, `DELETE /crm/links/{id}`.

A link is an `https://` address and a label on a record, nothing more: no
upload, content, preview or Drive permission is accepted or stored.
"""

from __future__ import annotations

import pytest
from crm_fake import MEMBER, OWNER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

LABS = "?workspace=labs"
DRIVE = "https://drive.google.com/file/d/abc123/view"


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    two_workspaces(fake)
    http = crm_client(monkeypatch, fake=fake)
    company = http.post(
        f"/api/v1/crm/companies{LABS}", json={"name": "Acme"}, headers=as_(OWNER["id"])
    ).json()
    person = http.post(
        f"/api/v1/crm/people{LABS}", json={"first_name": "Deepa"}, headers=as_(OWNER["id"])
    ).json()
    return http, fake, {"company": company, "person": person}


def _link(
    http, record_type, record_id, *, url=DRIVE, label="Signed SOW", who=MEMBER["id"], **extra
):
    return http.post(
        f"/api/v1/crm/links{LABS}",
        json={
            "record_type": record_type,
            "record_id": record_id,
            "url": url,
            "label": label,
            **extra,
        },
        headers=as_(who),
    )


class TestOnEveryRecord:
    """FR-CRM-LINK-01 — on every record type that exists so far."""

    @pytest.mark.parametrize("record_type", ["company", "person"])
    def test_added_and_listed_on_the_record(self, crm, record_type):
        http, _, made = crm
        record = made[record_type]
        r = _link(http, record_type, record["id"])
        assert r.status_code == 201, r.text
        assert (r.json()["url"], r.json()["label"]) == (DRIVE, "Signed SOW")
        plural = {"company": "companies", "person": "people"}[record_type]
        page = http.get(f"/api/v1/crm/{plural}/{record['id']}{LABS}", headers=as_(VIEWER["id"]))
        assert [link["label"] for link in page.json()["links"]] == ["Signed SOW"]

    def test_a_record_type_not_built_yet_is_not_found(self, crm):
        http, _, made = crm
        r = _link(http, "opportunity", made["company"]["id"])
        assert r.status_code == 404
        assert r.json()["detail"] == "No such opportunity."

    def test_a_record_in_the_trash_takes_no_link(self, crm):
        http, _, made = crm
        http.delete(f"/api/v1/crm/people/{made['person']['id']}{LABS}", headers=as_(OWNER["id"]))
        assert _link(http, "person", made["person"]["id"]).status_code == 404


class TestHttpsOnly:
    """FR-CRM-LINK-02."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://drive.google.com/x",
            "javascript:alert(1)",
            "ftp://x.com/a",
            "https://",
            "drive.google.com/x",
        ],
    )
    def test_anything_but_https_is_refused(self, crm, url):
        http, _, made = crm
        r = _link(http, "company", made["company"]["id"], url=url)
        assert r.status_code == 422
        assert r.json()["detail"] == "A Drive link must be an https:// address."

    @pytest.mark.parametrize("extra", [{"content": "UEsDBBQ"}, {"preview": "x"}, {"file": "x"}])
    def test_no_content_is_accepted(self, crm, extra):
        http, fake, made = crm
        r = _link(http, "company", made["company"]["id"], **extra)
        assert r.status_code == 422
        assert fake.rows["crm_drive_links"] == {}

    def test_a_label_is_required(self, crm):
        http, _, made = crm
        assert _link(http, "company", made["company"]["id"], label="").status_code == 422


class TestWhoMay:
    def test_viewers_add_no_links(self, crm):
        http, _, made = crm
        assert _link(http, "company", made["company"]["id"], who=VIEWER["id"]).status_code == 403

    def test_a_removed_link_leaves_the_record_page(self, crm):
        http, fake, made = crm
        link = _link(http, "company", made["company"]["id"]).json()
        r = http.delete(f"/api/v1/crm/links/{link['id']}{LABS}", headers=as_(MEMBER["id"]))
        assert r.status_code == 200 and r.json()["deleted_group"]
        page = http.get(
            f"/api/v1/crm/companies/{made['company']['id']}{LABS}", headers=as_(OWNER["id"])
        )
        assert page.json()["links"] == []
        assert fake.rows["crm_drive_links"][link["id"]]["deleted_at"]

    def test_removing_an_unknown_link_is_not_found(self, crm):
        http, *_ = crm
        r = http.delete(f"/api/v1/crm/links/not-a-uuid{LABS}", headers=as_(MEMBER["id"]))
        assert r.status_code == 404
        assert r.json()["detail"] == "No such Drive link."
