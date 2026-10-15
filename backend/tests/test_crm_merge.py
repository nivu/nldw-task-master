"""Company merge — spec 007 FR-CRM-DUP-03, -04, FR-CRM-PIPE-04; contracts
§2.8 `GET /crm/companies/merge-preview`, `POST /crm/companies/merge`.

The person picks the survivor and, field by field, whose value it keeps;
every person, deal, task, note, Drive link and domain of the other company
moves to it, both timelines say so, and TVA projects stay linked, now to the
survivor. The atomic database function and its refusals are proved against
Postgres in `tests/db/test_crm_merge_db.py`.
"""

from __future__ import annotations

import pytest
from crm_fake import MEMBER, OWNER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.domain.crm import merge as rules

LABS = "?workspace=labs"
ACADEMY = "?workspace=academy"
DRIVE = "https://drive.google.com/file/d/abc123/view"


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.add_deal_lists(labs)
    fake.add("crm_fx_rates", workspace_id=labs["id"], currency="INR", month="2026-10-01", rate=1)
    http = crm_client(monkeypatch, fake=fake)
    me = as_(OWNER["id"])

    def company(name, domain, **values):
        row = http.post(
            f"/api/v1/crm/companies{LABS}", json={"name": name, "domain": domain}, headers=me
        ).json()
        if values:
            http.patch(f"/api/v1/crm/companies/{row['id']}{LABS}", json=values, headers=me)
        return row

    acme = company("Acme", "acme.com", city="Chennai", lead_stage="qualified")
    dup = company("Acme Corp", "acmecorp.com", country="India", city="Bengaluru")
    links_to = [{"record_type": "company", "record_id": dup["id"]}]
    person = http.post(
        f"/api/v1/crm/people{LABS}",
        json={"first_name": "Priya", "company_id": dup["id"]},
        headers=me,
    ).json()
    task = http.post(
        f"/api/v1/crm/tasks{LABS}", json={"title": "Call Priya", "links": links_to}, headers=me
    ).json()
    note = http.post(
        f"/api/v1/crm/notes{LABS}", json={"body": "Met at the expo.", "links": links_to}, headers=me
    ).json()
    link = http.post(
        f"/api/v1/crm/links{LABS}",
        json={"record_type": "company", "record_id": dup["id"], "url": DRIVE, "label": "NDA"},
        headers=me,
    ).json()
    made = {"acme": acme, "dup": dup, "person": person, "task": task, "note": note, "link": link}
    return http, fake, made


def _deal(http, name, company_id):
    r = http.post(
        f"/api/v1/crm/opportunities{LABS}",
        json={
            "name": name,
            "company_id": company_id,
            "next_step": "Call",
            "next_step_on": "2026-10-20",
        },
        headers=as_(OWNER["id"]),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _preview(http, a, b, who=OWNER["id"], workspace=LABS):
    return http.get(f"/api/v1/crm/companies/merge-preview{workspace}&a={a}&b={b}", headers=as_(who))


def _merge(http, a, b, choices=None, who=OWNER["id"], workspace=LABS, **extra):
    return http.post(
        f"/api/v1/crm/companies/merge{workspace}",
        json={"survivor_id": a, "merged_id": b, "choices": choices or {}, **extra},
        headers=as_(who),
    )


class TestPreview:
    """FR-CRM-DUP-03 — what the person chooses between, and what moves."""

    def test_differing_fields_with_both_values_and_a_default(self, crm):
        http, _, made = crm
        r = _preview(http, made["acme"]["id"], made["dup"]["id"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["a"]["id"], body["b"]["id"]) == (made["acme"]["id"], made["dup"]["id"])
        fields = {f["field"]: f for f in body["differing_fields"]}
        assert {"name", "domain", "city", "country", "lead_stage"} <= set(fields)
        assert (fields["name"]["a"], fields["name"]["b"], fields["name"]["default"]) == (
            "Acme",
            "Acme Corp",
            "a",
        )
        assert (fields["domain"]["a"], fields["domain"]["b"]) == ("acme.com", "acmecorp.com")
        # Only the merged company has a country: keeping nothing would lose it.
        assert (fields["country"]["a"], fields["country"]["default"]) == (None, "b")

    def test_the_counts_that_move(self, crm):
        http, _, made = crm
        moves = _preview(http, made["acme"]["id"], made["dup"]["id"]).json()["moves"]
        assert moves == {
            "people": 1,
            "deals": 0,
            "tasks": 1,
            "notes": 1,
            "links": 1,
            "projects": 0,
            "campaign_members": 0,
        }

    def test_equal_fields_are_not_a_choice(self, crm):
        http, _, made = crm
        fields = _preview(http, made["acme"]["id"], made["dup"]["id"]).json()["differing_fields"]
        assert "owner_id" not in {f["field"] for f in fields}

    def test_a_viewer_may_not_merge(self, crm):
        http, _, made = crm
        assert (
            _preview(http, made["acme"]["id"], made["dup"]["id"], VIEWER["id"]).status_code == 403
        )
        r = _merge(http, made["acme"]["id"], made["dup"]["id"], who=VIEWER["id"])
        assert r.status_code == 403

    def test_the_same_company_twice_is_refused(self, crm):
        http, _, made = crm
        r = _preview(http, made["acme"]["id"], made["acme"]["id"])
        assert (r.status_code, r.json()["detail"]) == (422, rules.SAME_COMPANY)


class TestFieldChoices:
    """FR-CRM-DUP-03 — the survivor keeps what the person chose."""

    def test_defaults_keep_the_survivors_and_fill_its_gaps(self, crm):
        http, fake, made = crm
        r = _merge(http, made["acme"]["id"], made["dup"]["id"])
        assert r.status_code == 200, r.text
        row = fake.rows["crm_companies"][made["acme"]["id"]]
        assert (row["name"], row["city"], row["country"]) == ("Acme", "Chennai", "India")

    def test_choosing_b_takes_the_merged_companys_value(self, crm):
        http, fake, made = crm
        r = _merge(
            http, made["acme"]["id"], made["dup"]["id"], {"name": "b", "city": "b", "domain": "b"}
        )
        assert r.status_code == 200, r.text
        row = fake.rows["crm_companies"][made["acme"]["id"]]
        assert (row["name"], row["city"]) == ("Acme Corp", "Bengaluru")
        domains = {
            d["domain"]: d["is_primary"]
            for d in fake.rows["crm_company_domains"].values()
            if d["company_id"] == made["acme"]["id"]
        }
        assert domains == {"acme.com": False, "acmecorp.com": True}

    def test_the_survivor_keeps_every_domain_of_both(self, crm):
        http, fake, made = crm
        _merge(http, made["acme"]["id"], made["dup"]["id"])
        domains = {
            d["domain"]: d["is_primary"]
            for d in fake.rows["crm_company_domains"].values()
            if d["company_id"] == made["acme"]["id"]
        }
        assert domains == {"acme.com": True, "acmecorp.com": False}

    def test_a_field_that_cannot_be_chosen_is_refused(self, crm):
        http, fake, made = crm
        r = _merge(http, made["acme"]["id"], made["dup"]["id"], {"last_contact_at": "b"})
        assert r.status_code == 422
        assert r.json()["detail"] == "last_contact_at is not a field you can choose in a merge."
        assert not fake.rows["crm_companies"][made["dup"]["id"]].get("merged_into_id")

    def test_an_academy_field_is_not_a_labs_choice(self):
        assert "institution_type_id" not in rules.choosable(tva_linked=True)
        assert "entities" not in rules.choosable(tva_linked=False)

    def test_an_earlier_stage_cannot_hold_open_deals(self, crm):
        """FR-CRM-PIPE-04 — the survivor keeps every open deal of both."""
        http, fake, made = crm
        _deal(http, "Acme - AIOps", made["acme"]["id"])
        r = _merge(http, made["acme"]["id"], made["dup"]["id"], {"lead_stage": "b"})
        assert r.status_code == 422
        assert "cannot be" in r.json()["detail"]
        assert not fake.rows["crm_companies"][made["dup"]["id"]].get("merged_into_id")

    def test_a_change_meanwhile_is_refused(self, crm):
        http, _, made = crm
        r = _merge(
            http,
            made["acme"]["id"],
            made["dup"]["id"],
            expect={"survivor_updated_at": "2020-01-01T00:00:00+00:00"},
        )
        assert r.status_code == 409


class TestEverythingMoves:
    """FR-CRM-DUP-03 — nothing stays behind on the merged company."""

    def test_people_tasks_notes_and_links_move(self, crm):
        http, fake, made = crm
        assert _merge(http, made["acme"]["id"], made["dup"]["id"]).status_code == 200
        keep, gone = made["acme"]["id"], made["dup"]["id"]
        assert fake.rows["crm_people"][made["person"]["id"]]["company_id"] == keep
        for table in ("crm_task_links", "crm_note_links"):
            ids = {
                link["record_id"]
                for link in fake.rows[table].values()
                if link["record_type"] == "company"
            }
            assert keep in ids and gone not in ids
        assert fake.rows["crm_drive_links"][made["link"]["id"]]["record_id"] == keep

    def test_deals_move_trash_included(self, crm):
        http, fake, made = crm
        me = as_(OWNER["id"])
        http.patch(
            f"/api/v1/crm/companies/{made['dup']['id']}{LABS}",
            json={"lead_stage": "qualified"},
            headers=me,
        )
        deal = _deal(http, "Acme Corp - Pilot", made["dup"]["id"])
        old = _deal(http, "Acme Corp - Old", made["dup"]["id"])
        http.delete(f"/api/v1/crm/opportunities/{old['id']}{LABS}", headers=me)
        r = _merge(http, made["acme"]["id"], made["dup"]["id"])
        assert r.status_code == 200, r.text
        for d in (deal, old):
            assert fake.rows["crm_opportunities"][d["id"]]["company_id"] == made["acme"]["id"]

    def test_the_merged_company_goes_to_the_trash_and_resolves_to_the_survivor(self, crm):
        http, fake, made = crm
        _merge(http, made["acme"]["id"], made["dup"]["id"], who=MEMBER["id"])
        gone = fake.rows["crm_companies"][made["dup"]["id"]]
        assert gone["merged_into_id"] == made["acme"]["id"] and gone["deleted_at"]
        page = http.get(
            f"/api/v1/crm/companies/{made['dup']['id']}{LABS}", headers=as_(OWNER["id"])
        ).json()
        assert page["record"]["id"] == made["acme"]["id"]
        assert page["merged_from"] == {"id": made["dup"]["id"], "name": "Acme Corp"}

    def test_a_merged_id_still_resolves_after_its_survivor_is_merged(self, crm):
        """A into B, then B into C: A's id opens C."""
        http, _, made = crm
        me = as_(OWNER["id"])
        third = http.post(
            f"/api/v1/crm/companies{LABS}", json={"name": "Acme Ltd"}, headers=me
        ).json()
        assert _merge(http, made["acme"]["id"], made["dup"]["id"]).status_code == 200
        assert _merge(http, third["id"], made["acme"]["id"]).status_code == 200
        r = http.get(f"/api/v1/crm/companies/{made['dup']['id']}{LABS}", headers=me)
        assert r.status_code == 200, r.text
        assert r.json()["record"]["id"] == third["id"]
        assert r.json()["merged_from"] == {"id": made["dup"]["id"], "name": "Acme Corp"}

    def test_a_merged_company_cannot_be_restored(self, crm):
        http, fake, made = crm
        _merge(http, made["acme"]["id"], made["dup"]["id"])
        r = http.post(
            f"/api/v1/crm/companies/{made['dup']['id']}/restore{LABS}", headers=as_(OWNER["id"])
        )
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "This company was merged into another; open the survivor instead.",
        )
        assert fake.rows["crm_companies"][made["dup"]["id"]]["deleted_at"]

    def test_a_merged_company_cannot_be_merged_again(self, crm):
        http, _, made = crm
        _merge(http, made["acme"]["id"], made["dup"]["id"])
        r = _merge(http, made["acme"]["id"], made["dup"]["id"])
        assert r.status_code in (404, 422)

    def test_a_company_of_another_workspace_is_not_found(self, crm):
        http, _, made = crm
        other = http.post(
            f"/api/v1/crm/companies{ACADEMY}",
            json={"name": "Acme College"},
            headers=as_(OWNER["id"]),
        ).json()
        assert _merge(http, made["acme"]["id"], other["id"]).status_code == 404
        assert _preview(http, made["acme"]["id"], other["id"]).status_code == 404


class TestBothTimelines:
    """FR-CRM-DUP-03, FR-CRM-AUD-01 — each side says what happened."""

    def test_the_survivor_names_the_merged_company(self, crm):
        http, fake, made = crm
        _merge(http, made["acme"]["id"], made["dup"]["id"])
        r = http.get(
            f"/api/v1/crm/companies/{made['acme']['id']}/timeline{LABS}", headers=as_(OWNER["id"])
        )
        assert r.status_code == 200, r.text
        merges = [e for e in r.json() if e["type"] == "merge"]
        assert [e["summary"] for e in merges] == ["Merged “Acme Corp” into this company"]

    def test_both_ids_are_audited_as_merged(self, crm):
        http, fake, made = crm
        _merge(http, made["acme"]["id"], made["dup"]["id"])
        targets = {a["target_id"] for a in fake.audit if a["action"] == "company.merged"}
        assert targets == {made["acme"]["id"], made["dup"]["id"]}


class TestTvaLinks:
    """FR-CRM-DUP-04 — TVA projects stay linked, to the survivor."""

    def test_a_project_linked_to_the_merged_company_moves(self, crm):
        http, fake, made = crm
        project = fake.add_project("Acme Corp pilot", crm_company_id=made["dup"]["id"])
        preview = _preview(http, made["acme"]["id"], made["dup"]["id"]).json()
        assert preview["moves"]["projects"] == 1
        assert _merge(http, made["acme"]["id"], made["dup"]["id"]).status_code == 200
        moved = fake.rows["projects"][project["id"]]
        assert (moved["crm_company_id"], moved["name"]) == (made["acme"]["id"], "Acme Corp pilot")
        [row] = [a for a in fake.audit if a["target_table"] == "projects"]
        assert row["workspace_id"] is None
        assert (row["before"], row["after"]) == (
            {"crm_company_id": made["dup"]["id"]},
            {"crm_company_id": made["acme"]["id"]},
        )

    def test_the_survivors_own_project_stays(self, crm):
        http, fake, made = crm
        project = fake.add_project("Acme work", crm_company_id=made["acme"]["id"])
        _merge(http, made["acme"]["id"], made["dup"]["id"])
        assert fake.rows["projects"][project["id"]]["crm_company_id"] == made["acme"]["id"]


class TestPureRules:
    def test_a_filled_value_beats_an_empty_one(self):
        a = {"id": "a", "city": None}
        b = {"id": "b", "city": "Pune"}
        assert rules.default_side("city", a, b, []) == "b"
        assert rules.default_side("city", b, a, []) == "a"

    def test_multi_selects_in_another_order_are_equal(self):
        a = {"id": "a", "channel_ids": ["x", "y"]}
        b = {"id": "b", "channel_ids": ["y", "x"]}
        assert "channel_ids" not in rules.differing(a, b, [], tva_linked=True)

    @pytest.mark.parametrize(
        ("chosen", "deals", "expected"),
        [
            ("qualified", True, ("deal_open", None)),
            ("customer", True, ("customer", None)),
            ("nurturing", False, ("nurturing", None)),
        ],
    )
    def test_lead_stage_after(self, chosen, deals, expected):
        got = rules.lead_stage_after(chosen, has_open_deals=deals, name="Acme", chosen_label="X")
        assert got == expected

    def test_an_early_stage_with_open_deals_is_refused(self):
        stage, why = rules.lead_stage_after(
            "nurturing", has_open_deals=True, name="Acme", chosen_label="Nurturing"
        )
        assert why and "cannot be Nurturing" in why
