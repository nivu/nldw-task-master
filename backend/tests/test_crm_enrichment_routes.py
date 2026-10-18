"""Enrichment's routes — spec 009 FR-CRMAI-ENR-04, -05, -08, FR-CRMAI-REV-04,
-05, FR-CRMAI-MCP-02, -04; contracts §1, §2.2, §2.4; spec §15 "Promoting an
enriched value is an audited edit by the person; an enriched value that
differs from a filled field shows an enrichment_conflict item, not a promote
button":

- **Enrich selected** (`POST /crm/enrichment/run`) is for Admins, takes the
  selected companies only, finds none of another workspace's, and queues
  them without reading anything;
- a company's record read carries its `enrichment` while phase 2 is on;
- promoting is an ordinary `PATCH /crm/companies/{id}`, audited as the
  person's;
- approving an `enrichment_conflict` replaces the field through the same
  path (`records.update`), audited with the item's id, and is refused once
  the field changed after the item was made.

Routes run against `tests/crm_fake.py`, with the enrichment job of
`test_crm_enrichment_job.py` and its fakes.
"""

from __future__ import annotations

import uuid

import pytest
from crm_fake import MEMBER, OWNER, VIEWER, as_
from test_crm_enrichment_job import API, LABS, Sites

from app.mcp import crm as tools


@pytest.fixture
def sites(monkeypatch) -> Sites:
    sites = Sites(monkeypatch)
    sites.site()
    return sites


def _run(sites: Sites, ids: list[str], who=OWNER, params=LABS):
    return sites.http.post(
        f"{API}/enrichment/run", params=params, json={"company_ids": ids}, headers=as_(who["id"])
    )


class TestEnrichSelected:
    """Contracts §2.4; FR-CRMAI-ENR-01, -07, -08; D-3."""

    @pytest.mark.parametrize("who", [MEMBER, VIEWER])
    def test_enrichment_run_is_for_admins_only(self, sites, who):
        acme = sites.company("Acme", "https://acme.in")
        r = _run(sites, [acme["id"]], who=who)
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "Only a workspace admin can do that."

    def test_enrichment_run_counts_and_reads_nothing(self, sites):
        acme = sites.company("Acme", "https://acme.in")
        bare = sites.company("Bare")
        sites.run()
        beta = sites.company("Beta", domain="beta.in")
        r = _run(sites, [acme["id"], bare["id"], beta["id"], acme["id"]])
        assert r.status_code == 200, r.text
        assert r.json() == {"queued": 2, "skipped_no_website": 1, "already_enriched": 1}
        assert sites.row(acme)["status"] == "queued"
        assert sites.row(bare) is None
        assert len(sites.claude.calls) == 1 and sites.queue == []

    @pytest.mark.parametrize("ids", [["not-an-id"], [str(uuid.uuid4())]])
    def test_enrichment_run_an_unknown_company_is_not_found(self, sites, ids):
        r = _run(sites, ids)
        assert r.status_code == 404 and r.json()["detail"] == "No such company."

    def test_enrichment_run_a_company_in_the_trash_is_not_found(self, sites):
        acme = sites.company("Acme", "https://acme.in")
        r = sites.http.delete(
            f"{API}/companies/{acme['id']}", params=LABS, headers=as_(OWNER["id"])
        )
        assert r.status_code == 200, r.text
        assert _run(sites, [acme["id"]]).status_code == 404

    @pytest.mark.parametrize("ids", [[], [str(uuid.uuid4())] * 501])
    def test_enrichment_run_takes_one_to_five_hundred(self, sites, ids):
        assert _run(sites, ids).status_code == 422

    def test_enrichment_run_is_web_only(self):
        assert not [n for n in dir(tools) if n.startswith("crm_") and "enrich" in n]


class TestTheRecordRead:
    """Contracts §1; FR-CRMAI-ENR-04, FR-CRMAI-MCP-02."""

    def test_enrichment_shown_with_status_values_and_promotable(self, sites):
        acme = sites.company("Acme", "https://acme.in", city="Chennai")
        assert sites.get(acme)["enrichment"]["status"] == "queued"
        sites.run()
        shown = sites.get(acme)["enrichment"]
        assert shown["values"]["industry"] == "Manufacturing"
        assert shown["promotable"] == {
            "industry_id": True,
            "size_id": True,
            "city": False,
            "country": True,
            "description": True,
            "linkedin": True,
        }
        assert len(shown["conflicts"]) == 1
        assert "failure_reason" not in shown

    def test_enrichment_a_failure_shows_its_reason(self, sites):
        acme = sites.company("Acme", "https://acme.org")
        sites.run()
        shown = sites.get(acme)["enrichment"]
        assert (shown["status"], shown["failure_reason"], shown["ai"]) == (
            "failed",
            "Website did not respond",
            None,
        )

    def test_enrichment_none_for_a_company_never_queued(self, sites):
        assert sites.get(sites.company("Bare"))["enrichment"] is None

    def test_enrichment_a_viewer_sees_the_values_and_no_items(self, sites):
        acme = sites.company("Acme", "https://acme.in", city="Chennai")
        sites.run()
        shown = sites.get(acme, who=VIEWER)["enrichment"]
        assert shown["values"]["city"] == "Coimbatore"
        assert shown["conflicts"] == []


class TestPromote:
    """FR-CRMAI-ENR-04, FR-CRMAI-MCP-04 — an ordinary audited edit."""

    def test_enrichment_promote_is_an_audited_edit_by_the_person(self, sites):
        acme = sites.company("Acme", "https://acme.in", owner_id=MEMBER["id"])
        sites.run()
        industry = sites.fake.option(sites.labs, "Manufacturing")
        before = dict(sites.row(acme))
        audit = len(sites.fake.audit)
        sites.patch(acme, {"industry_id": industry["id"]}, who=MEMBER)
        [row] = [a for a in sites.fake.audit[audit:] if a["target_table"] == "crm_companies"]
        assert (row["actor_id"], row["actor_label"]) == (MEMBER["id"], "user")
        assert row["after"] == {"industry_id": industry["id"]}
        assert not row.get("suggestion_id")
        assert sites.row(acme) == before
        assert sites.get(acme)["enrichment"]["promotable"]["industry_id"] is False


class TestConflictApproval:
    """FR-CRMAI-REV-04, -05; contracts §2.2 — through `records.update`."""

    def _item(self, sites):
        acme = sites.company("Acme", "https://acme.in", city="Chennai", owner_id=MEMBER["id"])
        sites.run()
        [item] = sites.items()
        return acme, item

    def _approve(self, sites, item, body=None, who=MEMBER):
        return sites.http.post(
            f"{API}/review/{item['id']}/approve",
            params=LABS,
            json=body or {},
            headers=as_(who["id"]),
        )

    def test_enrichment_approving_replaces_the_field_as_a_manual_edit(self, sites):
        acme, item = self._item(sites)
        r = self._approve(sites, item)
        assert r.status_code == 200, r.text
        assert sites.fake.rows["crm_companies"][acme["id"]]["city"] == "Coimbatore"
        [row] = [
            a
            for a in sites.fake.audit
            if a["target_table"] == "crm_companies" and a.get("suggestion_id")
        ]
        assert (row["actor_id"], row["suggestion_id"]) == (MEMBER["id"], item["id"])
        assert row["after"] == {"city": "Coimbatore"}
        assert sites.fake.rows["crm_review_items"][item["id"]]["status"] == "approved"

    def test_enrichment_a_field_changed_since_is_refused_as_changed(self, sites):
        acme, item = self._item(sites)
        sites.patch(acme, {"city": "Madurai"})
        r = self._approve(sites, item)
        assert r.status_code == 409
        assert r.json()["detail"] == "This record changed after the suggestion was made."
        assert sites.fake.rows["crm_companies"][acme["id"]]["city"] == "Madurai"

    def test_enrichment_another_fields_change_does_not_refuse(self, sites):
        acme, item = self._item(sites)
        sites.patch(acme, {"country": "India"})
        assert self._approve(sites, item).status_code == 200

    def test_enrichment_edit_and_approve_takes_the_edited_value(self, sites):
        acme, item = self._item(sites)
        r = self._approve(sites, item, {"edits": {"value": "Coimbatore North"}})
        assert r.status_code == 200, r.text
        assert sites.fake.rows["crm_companies"][acme["id"]]["city"] == "Coimbatore North"

    def test_enrichment_an_edited_linkedin_address_is_saved_as_a_link(self, sites):
        old = {"primary": {"url": "https://www.linkedin.com/company/acme-old/"}, "others": []}
        acme = sites.company("Acme", "https://acme.in", linkedin=old, owner_id=MEMBER["id"])
        sites.run()
        [item] = [i for i in sites.items() if i["suggestion"]["field"] == "linkedin"]
        edited = "https://www.linkedin.com/company/acme-new/"
        r = self._approve(sites, item, {"edits": {"value": edited}})
        assert r.status_code == 200, r.text
        stored = sites.fake.rows["crm_companies"][acme["id"]]["linkedin"]
        assert stored["primary"]["url"] == edited

    def test_enrichment_an_edit_the_manual_path_refuses_reopens_the_item(self, sites):
        acme, item = self._item(sites)
        r = self._approve(sites, item, {"edits": {"value": "x" * 101}})
        assert r.status_code == 422, r.text
        assert sites.fake.rows["crm_review_items"][item["id"]]["status"] == "open"
        assert sites.fake.rows["crm_companies"][acme["id"]]["city"] == "Chennai"
