"""The record timeline — spec 007 FR-CRM-REC-03, -04, FR-CRM-AUD-05,
FR-CRM-ACC-11; contracts §2.5 `GET /crm/{object}/{id}/timeline`.

The timeline is the audit, read for one record: every entry names its actor
and channel. For a Viewer it obeys the amount rule: no amount value appears
in it. Companies and people carry no amount, so that part is a structure test
on the presenter with an opportunity-shaped audit row, ready for S4's deals.
"""

from __future__ import annotations

import asyncio

import pytest
from crm_fake import MEMBER, OWNER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.mcp import crm as tools
from app.services.crm import present

LABS = "?workspace=labs"

#: Plan §5.5 — the redaction tests' own denylist, written out here and never
#: imported from production, so an omission there cannot hide itself.
AMOUNTS = {
    "amount",
    "amount_in_inr",
    "fx_rate_used",
    "won_fx_rate",
    "weighted_amount",
    "budget",
    "budget_in_inr",
    "budget_fx_rate",
    "pipeline_created",
    "won_value",
    "cost_per_converted",
    "revenue",
    "suggested_inr",
    "existing_tva_revenue",
}


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    two_workspaces(fake)
    return crm_client(monkeypatch, fake=fake), fake


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


def _timeline(http, record_id, who=OWNER["id"], plural="companies", query=""):
    r = http.get(f"/api/v1/crm/{plural}/{record_id}/timeline{LABS}{query}", headers=as_(who))
    assert r.status_code == 200, r.text
    return r.json()


class TestEntries:
    """FR-CRM-REC-03, -04."""

    def test_create_change_delete_and_restore(self, crm):
        http, _ = crm
        made = http.post(
            f"/api/v1/crm/companies{LABS}", json={"name": "Acme"}, headers=as_(MEMBER["id"])
        ).json()
        http.patch(
            f"/api/v1/crm/companies/{made['id']}{LABS}",
            json={"city": "Chennai", "owner_id": OWNER["id"]},
            headers=as_(MEMBER["id"]),
        )
        http.delete(f"/api/v1/crm/companies/{made['id']}{LABS}", headers=as_(OWNER["id"]))
        http.post(f"/api/v1/crm/companies/{made['id']}/restore{LABS}", headers=as_(OWNER["id"]))

        entries = _timeline(http, made["id"])
        assert [(e["type"], e["summary"], e["actor_name"]) for e in entries] == [
            ("delete", "Restored from Trash", "Owner"),
            ("delete", "Moved to Trash", "Owner"),
            ("field", "Changed City and Owner", "Deepika"),
            ("create", "Created this company", "Deepika"),
        ]
        assert all((e["actor_kind"], e["channel"]) == ("user", "web") for e in entries)
        change = {c["field"]: c for c in entries[2]["changes"]}
        assert (change["owner_id"]["before"], change["owner_id"]["after"]) == ("Deepika", "Owner")
        assert (change["city"]["before"], change["city"]["after"]) == (None, "Chennai")

    def test_a_lead_stage_move_is_a_stage_entry(self, crm):
        http, _ = crm
        made = http.post(
            f"/api/v1/crm/companies{LABS}", json={"name": "Acme"}, headers=as_(OWNER["id"])
        ).json()
        http.patch(
            f"/api/v1/crm/companies/{made['id']}{LABS}",
            json={"lead_stage": "qualified"},
            headers=as_(OWNER["id"]),
        )
        entry = _timeline(http, made["id"])[0]
        assert (entry["type"], entry["changes"][0]["after"]) == ("stage", "Qualified")

    def test_domains_and_drive_links_show_on_their_record(self, crm):
        http, _ = crm
        made = http.post(
            f"/api/v1/crm/companies{LABS}",
            json={"name": "Acme", "domain": "acme.com"},
            headers=as_(OWNER["id"]),
        ).json()
        link = http.post(
            f"/api/v1/crm/links{LABS}",
            json={
                "record_type": "company",
                "record_id": made["id"],
                "url": "https://drive.google.com/x",
                "label": "NDA",
            },
            headers=as_(OWNER["id"]),
        ).json()
        http.delete(f"/api/v1/crm/links/{link['id']}{LABS}", headers=as_(OWNER["id"]))
        summaries = [e["summary"] for e in _timeline(http, made["id"])]
        assert summaries[:2] == ["Removed Drive link “NDA”", "Added Drive link “NDA”"]
        assert "Added domain acme.com" in summaries

    def test_the_type_filter(self, crm):
        http, _ = crm
        made = http.post(
            f"/api/v1/crm/people{LABS}", json={"first_name": "Deepa"}, headers=as_(OWNER["id"])
        ).json()
        http.patch(
            f"/api/v1/crm/people/{made['id']}{LABS}",
            json={"city": "Pune"},
            headers=as_(OWNER["id"]),
        )
        entries = _timeline(http, made["id"], plural="people", query="&types=create")
        assert [e["type"] for e in entries] == ["create"]

    def test_the_type_filter_reaches_past_a_page_of_other_changes(self, crm):
        """A stage move older than 250 field changes still comes back for
        types=stage: the filter is not applied to one page of audit rows."""
        http, _ = crm
        made = http.post(
            f"/api/v1/crm/companies{LABS}", json={"name": "Acme"}, headers=as_(OWNER["id"])
        ).json()
        http.patch(
            f"/api/v1/crm/companies/{made['id']}{LABS}",
            json={"lead_stage": "qualified"},
            headers=as_(OWNER["id"]),
        )
        for n in range(250):
            http.patch(
                f"/api/v1/crm/companies/{made['id']}{LABS}",
                json={"city": f"City {n}"},
                headers=as_(OWNER["id"]),
            )
        entries = _timeline(http, made["id"], query="&types=stage&limit=50")
        assert [e["type"] for e in entries] == ["stage"]
        fields = _timeline(http, made["id"], query="&types=field&limit=50")
        assert len(fields) == 50

    def test_an_mcp_write_says_so(self, crm):
        http, _ = crm
        made = asyncio.run(
            tools.crm_create(Ctx(OWNER["id"]), "person", {"first_name": "Ravi"}, workspace="labs")
        )
        entry = _timeline(http, made["id"], plural="people")[0]
        assert (entry["type"], entry["channel"]) == ("create", "mcp")

    def test_the_record_page_carries_the_last_ten(self, crm):
        http, _ = crm
        made = http.post(
            f"/api/v1/crm/people{LABS}", json={"first_name": "Deepa"}, headers=as_(OWNER["id"])
        ).json()
        for n in range(12):
            http.patch(
                f"/api/v1/crm/people/{made['id']}{LABS}",
                json={"city": f"City {n}"},
                headers=as_(OWNER["id"]),
            )
        page = http.get(f"/api/v1/crm/people/{made['id']}{LABS}", headers=as_(VIEWER["id"]))
        assert len(page.json()["timeline_head"]) == 10


class TestViewerSeesNoAmounts:
    """FR-CRM-AUD-05, FR-CRM-ACC-11 — structure test, ready for S4's amounts."""

    ROWS = [
        {
            "id": 3,
            "at": "2026-10-14T10:00:00+05:30",
            "actor_id": "u-owner",
            "actor_label": "user",
            "channel": "web",
            "action": "opportunity.updated",
            "target_table": "crm_opportunities",
            "target_id": "o1",
            "before": {"amount": "500000.00", "amount_in_inr": "500000.00", "name": "Pilot"},
            "after": {"amount": "900000.00", "amount_in_inr": "900000.00", "name": "Pilot 2"},
        },
        {
            "id": 2,
            "at": "2026-10-13T10:00:00+05:30",
            "actor_id": "u-owner",
            "actor_label": "user",
            "channel": "mcp",
            "action": "opportunity.updated",
            "target_table": "crm_opportunities",
            "target_id": "o1",
            "before": {"weighted_amount": "1.00"},
            "after": {"weighted_amount": "2.00"},
        },
    ]

    def _entries(self, role):
        facts = present.Facts(names={"u-owner": "Owner"}, role=role)
        return present.timeline(self.ROWS, "opportunity", facts)

    def test_a_member_sees_them(self):
        fields = {c["field"] for e in self._entries("member") for c in e["changes"]}
        assert {"amount", "amount_in_inr", "weighted_amount"} <= fields

    def test_a_viewer_sees_no_amount_anywhere(self):
        entries = self._entries("viewer")
        assert [e["summary"] for e in entries] == ["Changed Name"]
        text = repr(entries)
        assert not any(f"'{name}'" in text for name in AMOUNTS)
        assert "500000" not in text and "900000" not in text

    def test_redact_omits_rather_than_zeroes(self):
        deal = AMOUNTS - {
            "budget",
            "budget_in_inr",
            "budget_fx_rate",
            "pipeline_created",
            "won_value",
            "cost_per_converted",
        }
        row = {"id": "o1", "name": "Pilot", "currency": "USD", **{k: "1.00" for k in deal}}
        kept = present.redact(row, "opportunity", "viewer")
        assert set(kept) == {"id", "name", "currency"}
        assert present.redact(row, "opportunity", "member") == row
        campaign = {"id": "c1", **{k: "1.00" for k in AMOUNTS - deal}}
        assert set(present.redact(campaign, "campaign", "viewer")) == {"id"}
