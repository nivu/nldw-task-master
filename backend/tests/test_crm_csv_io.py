"""CSV import and export — spec 007 FR-CRM-IO-01 to -05, FR-CRM-DUP-02,
FR-CRM-VIEW-07 (export), FR-CRM-ACC-11; spec §11 AC "CSV import catches
duplicate domains and emails before writing" and "A CSV export downloads as
a CSV file with its name"; quickstart §3 S11 (export a view, import it back
in update mode with no changes); contracts §2.9 (CSV), §2.10.

Routes run against `tests/crm_fake.py`. A CSV import is the importer's
dry run and real run with `format: csv`, one object per file.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import date

import pytest
from crm_fake import MEMBER, OWNER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.domain.crm import csv_io
from app.mcp import crm as tools
from app.services.crm import deals, importer, records, tva
from app.services.crm import views as view_service

API = "/api/v1/crm"
LABS = "?workspace=labs"
ACADEMY = "?workspace=academy"
DRY_RUN = f"{API}/imports/dry-run"


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


@pytest.fixture
def crm(monkeypatch):
    """Academy with a qualified college (domain psg.edu) and Priya at it; a
    USD rate for October."""
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    for ws in (labs, academy):
        fake.add_deal_lists(ws)
        fake.add(
            "crm_fx_rates", workspace_id=ws["id"], currency="USD", month="2026-10-01", rate="84"
        )
    fake.add_member(academy, VIEWER["id"], "viewer")
    for module in (records, deals, importer):
        monkeypatch.setattr(module, "today_in_company_tz", lambda: date(2026, 10, 15))
    monkeypatch.setattr(view_service, "today_for", lambda user: date(2026, 10, 15))
    http = crm_client(monkeypatch, fake=fake)
    college = _post(http, "companies", {"name": "PSG Tech", "domain": "psg.edu"})
    _patch(http, "companies", college["id"], {"lead_stage": "qualified"})
    priya = _post(
        http,
        "people",
        {
            "first_name": "Priya",
            "last_name": "Raman",
            "company_id": college["id"],
            "primary_email": "priya@psg.edu",
        },
    )
    return {
        "http": http,
        "fake": fake,
        "labs": labs,
        "academy": academy,
        "college": college,
        "priya": priya,
    }


def _post(http, plural, body, who=OWNER["id"], query=ACADEMY):
    r = http.post(f"{API}/{plural}{query}", json=body, headers=as_(who))
    assert r.status_code == 201, r.text
    return r.json()


def _patch(http, plural, record_id, body, who=OWNER["id"], query=ACADEMY):
    r = http.patch(f"{API}/{plural}/{record_id}{query}", json=body, headers=as_(who))
    assert r.status_code == 200, r.text
    return r.json()


def _csv(rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue()


def _dry(http, content, obj, who=OWNER["id"], query=ACADEMY, **extra):
    return http.post(
        f"{DRY_RUN}{query}",
        json={"format": "csv", "content": content, "object": obj, **extra},
        headers=as_(who),
    )


def _apply(http, run_id, content, obj, who=OWNER["id"], query=ACADEMY, **extra):
    return http.post(
        f"{API}/imports/{run_id}/apply{query}",
        json={"format": "csv", "content": content, "object": obj, **extra},
        headers=as_(who),
    )


def _rows(fake, table, ws):
    return [r for r in fake.rows[table].values() if r["workspace_id"] == ws["id"]]


def _option(fake, ws, label):
    return next(
        o["id"]
        for o in fake.rows["crm_select_options"].values()
        if o["workspace_id"] == ws["id"] and o["label"] == label
    )


class TestDuplicates:
    """FR-CRM-IO-03; AC "CSV import catches duplicate domains and emails
    before writing"."""

    def test_domain_and_email_duplicates_are_caught_before_any_write(self, crm):
        http, fake = crm["http"], crm["fake"]
        companies_before = len(fake.rows["crm_companies"])
        content = _csv(
            [
                ["Name", "Domain"],
                ["PSG College of Technology", "https://www.psg.edu/about"],
                ["KCT", "kct.ac.in"],
                ["KCT again", "kct.ac.in"],
            ]
        )
        r = _dry(http, content, "companies")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["counts"] == {
            "companies": {"in_file": 3, "create": 2, "update": 0, "unchanged": 0, "skipped": 1}
        }
        first, second, third = body["rows"]
        assert first["match"] == {"id": crm["college"]["id"], "name": "PSG Tech", "by": "domain"}
        assert first["action"] == "skip"
        assert second["action"] == "create" and second["errors"] == []
        assert third["errors"] == ["Row 2 has the same domain."]
        assert body["blocking"] == ["Row 3: Row 2 has the same domain."]
        assert len(fake.rows["crm_companies"]) == companies_before

        people = _csv([["First name", "Email"], ["P", "PRIYA@psg.edu"]])
        row = _dry(http, people, "people").json()["rows"][0]
        assert row["match"]["by"] == "email" and row["match"]["name"] == "Priya Raman"

    def test_the_real_run_is_refused_while_a_row_blocks(self, crm):
        http = crm["http"]
        content = _csv([["Name", "Domain"], ["KCT", "kct.ac.in"], ["KCT 2", "kct.ac.in"]])
        run = _dry(http, content, "companies").json()["run_id"]
        r = _apply(http, run, content, "companies")
        assert r.status_code == 422
        assert r.json()["detail"] == "The import cannot run yet: Row 2: Row 1 has the same domain."
        # Skipping the row lets the rest through.
        r = _apply(http, run, content, "companies", actions={"2": "skip"})
        assert r.status_code == 200, r.text
        assert r.json()["created"] == 1

    def test_creating_a_record_a_domain_already_names_is_refused(self, crm):
        http = crm["http"]
        content = _csv([["Name", "Domain"], ["PSG again", "psg.edu"]])
        body = _dry(http, content, "companies", mode="create").json()
        assert body["rows"][0]["errors"] == [
            "A record with this domain or email exists; update it or skip the row."
        ]

    def test_an_id_matched_row_created_again_clashes_on_its_own_domain(self, crm):
        http, college = crm["http"], crm["college"]
        content = _csv([["ID", "Name", "Domain"], [college["id"], "PSG Tech", "psg.edu"]])
        body = _dry(http, content, "companies", actions={"1": "create"}).json()
        [row] = body["rows"]
        assert row["action"] == "create" and row["match"]["by"] == "id"
        assert row["errors"] and "psg.edu" in row["errors"][0]
        assert body["blocking"]

    def test_an_additional_domain_twice_in_one_file(self, crm):
        http = crm["http"]
        content = _csv(
            [
                ["Name", "Domain", "Additional domains"],
                ["KCT", "kct.ac.in", ""],
                ["KCT Online", "kctonline.in", "kct.ac.in"],
            ]
        )
        body = _dry(http, content, "companies").json()
        assert body["rows"][1]["errors"] == ["Row 1 has the same domain."]
        assert body["blocking"]

    def test_similar_names_are_warnings(self, crm):
        http = crm["http"]
        body = _dry(http, _csv([["Name"], ["PSG Tech College"]]), "companies").json()
        [warning] = [c for c in body["conflicts"] if c["kind"] == "similar_name"]
        assert warning["blocking"] is False and "PSG Tech" in warning["detail"]
        assert body["blocking"] == []


class TestRowActions:
    """FR-CRM-IO-03 — skip, update or create, per row."""

    CONTENT = _csv(
        [
            ["Name", "Domain", "City"],
            ["PSG Tech", "psg.edu", "Coimbatore"],
            ["KCT", "kct.ac.in", "Coimbatore"],
            ["Amrita", "amrita.edu", ""],
        ]
    )

    def test_skip_update_and_create(self, crm):
        http, fake, academy = crm["http"], crm["fake"], crm["academy"]
        body = _dry(http, self.CONTENT, "companies", mode="update", actions={"3": "skip"}).json()
        assert [r["action"] for r in body["rows"]] == ["update", "create", "skip"]
        r = _apply(
            http, body["run_id"], self.CONTENT, "companies", mode="update", actions={"3": "skip"}
        )
        assert r.status_code == 200, r.text
        assert (r.json()["created"], r.json()["updated"], r.json()["failed"]) == (1, 1, [])
        names = {c["name"]: c for c in _rows(fake, "crm_companies", academy)}
        assert names["PSG Tech"]["city"] == "Coimbatore"
        assert "Amrita" not in names
        assert names["KCT"]["lead_stage"] == "new"
        assert names["KCT"]["owner_id"] == OWNER["id"]
        domains = {d["domain"] for d in _rows(fake, "crm_company_domains", academy)}
        assert "kct.ac.in" in domains

    def test_the_default_skips_a_matching_row(self, crm):
        http = crm["http"]
        body = _dry(http, self.CONTENT, "companies").json()
        assert [r["action"] for r in body["rows"]] == ["skip", "create", "create"]

    def test_an_empty_cell_never_blanks_a_field(self, crm):
        http, fake = crm["http"], crm["fake"]
        _patch(http, "companies", crm["college"]["id"], {"city": "Coimbatore"})
        content = _csv(
            [["Name", "Domain", "City", "Country"], ["PSG Tech", "psg.edu", "", "India"]]
        )
        run = _dry(http, content, "companies", mode="update").json()["run_id"]
        assert _apply(http, run, content, "companies", mode="update").status_code == 200
        row = fake.rows["crm_companies"][crm["college"]["id"]]
        assert (row["city"], row["country"]) == ("Coimbatore", "India")

    def test_imported_rows_are_written_as_the_import_and_audited(self, crm):
        http, fake = crm["http"], crm["fake"]
        content = _csv([["Name"], ["Karunya"]])
        run = _dry(http, content, "companies").json()["run_id"]
        _apply(http, run, content, "companies")
        [created] = [a for a in fake.audit if a["action"] == "company.created"][-1:]
        assert created["actor_label"] == "import"
        assert [a["action"] for a in fake.audit if a["target_table"] == "crm_import_runs"] == [
            "import.dry_run",
            "import.applied",
        ]

    def test_a_run_applied_once_is_not_applied_again(self, crm):
        http, fake, academy = crm["http"], crm["fake"], crm["academy"]
        content = _csv(
            [
                ["Name", "Company", "Next step", "Next step date"],
                ["PSG - AI", "PSG Tech", "Call", "2026-10-20"],
            ]
        )
        run = _dry(http, content, "opportunities").json()["run_id"]
        r = _apply(http, run, content, "opportunities")
        assert r.status_code == 200, r.text
        assert r.json()["created"] == 1
        deals_before = len(_rows(fake, "crm_opportunities", academy))
        r = _apply(http, run, content, "opportunities")
        assert r.status_code == 409
        assert r.json()["detail"] == "This import has already run; dry-run the file again."
        assert len(_rows(fake, "crm_opportunities", academy)) == deals_before

    def test_a_failed_run_is_not_applied_again(self, crm, monkeypatch):
        http, fake = crm["http"], crm["fake"]
        content = _csv([["Name"], ["Karunya"]])
        run = _dry(http, content, "companies").json()["run_id"]

        write_new = importer._write_new
        failing = [True]

        def flaky(*args, **kwargs):
            if failing:
                raise RuntimeError("write failed")
            return write_new(*args, **kwargs)

        monkeypatch.setattr(importer, "_write_new", flaky)
        with pytest.raises(RuntimeError):
            _apply(http, run, content, "companies")
        assert fake.rows["crm_import_runs"][run]["status"] == "failed"
        failing.clear()
        assert _apply(http, run, content, "companies").status_code == 409

    def test_a_failure_to_record_the_failure_keeps_the_write_error(self, crm, monkeypatch):
        http = crm["http"]
        content = _csv([["Name"], ["Karunya"]])
        run = _dry(http, content, "companies").json()["run_id"]

        def boom(*args, **kwargs):
            raise RuntimeError("write failed")

        def unrecorded(*args, **kwargs):
            raise ConnectionError("store down")

        monkeypatch.setattr(importer, "_write_new", boom)
        monkeypatch.setattr(importer.store, "record_import", unrecorded)
        with pytest.raises(RuntimeError, match="write failed"):
            _apply(http, run, content, "companies")

    def test_the_file_must_be_the_dry_runs(self, crm):
        http = crm["http"]
        run = _dry(http, self.CONTENT, "companies").json()["run_id"]
        r = _apply(http, run, self.CONTENT + "x,y.edu,\n", "companies")
        assert r.status_code == 409


class TestRelationsAndRules:
    """FR-CRM-IO-01 relations by name, domain or email; FR-CRM-IO-03 every
    pipeline rule applies."""

    def test_relations_by_name_domain_and_email(self, crm):
        http, fake, academy = crm["http"], crm["fake"], crm["academy"]
        content = _csv(
            [
                ["First name", "Company", "Owner", "Email"],
                ["Ravi", "psg.edu", "deepika@nunnarilabs.com", "ravi@psg.edu"],
                ["Asha", "psg tech", "Owner", "asha@psg.edu"],
                ["Nobody", "Unknown College", "someone@else.com", "n@x.edu"],
            ]
        )
        body = _dry(http, content, "people").json()
        assert body["rows"][0]["errors"] == [] and body["rows"][1]["errors"] == []
        assert body["rows"][2]["errors"] == [
            "Company: no company called “Unknown College”.",
            "Owner: “someone@else.com” is not a member of this workspace; use their email.",
        ]
        r = _apply(http, body["run_id"], content, "people", actions={"3": "skip"})
        assert r.status_code == 200, r.text
        people = {p["first_name"]: p for p in _rows(fake, "crm_people", academy)}
        assert people["Ravi"]["company_id"] == crm["college"]["id"]
        assert people["Ravi"]["owner_id"] == MEMBER["id"]
        assert people["Asha"]["owner_id"] == OWNER["id"]

    def test_options_by_label(self, crm):
        http, fake, academy = crm["http"], crm["fake"], crm["academy"]
        content = _csv(
            [["Name", "Account type", "Channels"], ["Amrita", "client", "Referral; inbound"]]
        )
        body = _dry(http, content, "companies").json()
        assert body["rows"][0]["errors"] == []
        _apply(http, body["run_id"], content, "companies")
        amrita = next(c for c in _rows(fake, "crm_companies", academy) if c["name"] == "Amrita")
        assert amrita["account_type_id"] == _option(fake, academy, "Client")
        assert amrita["channel_ids"] == [
            _option(fake, academy, "Referral"),
            _option(fake, academy, "Inbound"),
        ]

    def test_a_deal_follows_the_pipeline_rules(self, crm):
        http, fake, academy = crm["http"], crm["fake"], crm["academy"]
        new = _post(http, "companies", {"name": "Fresh College"})
        content = _csv(
            [
                [
                    "Name",
                    "Company",
                    "Point of contact",
                    "Amount",
                    "Currency",
                    "Next step",
                    "Next step date",
                ],
                ["PSG - AI", "PSG Tech", "priya@psg.edu", "1000", "USD", "Call", "2026-10-20"],
                ["Fresh - AI", "Fresh College", "", "", "", "Call", "2026-10-20"],
                ["PSG - Data", "psg.edu", "", "", "", "", ""],
                ["PSG - Cloud", "psg.edu", "", "500", "", "Call", "2026-10-20"],
            ]
        )
        body = _dry(http, content, "opportunities").json()
        errors = [r["errors"] for r in body["rows"]]
        assert errors[0] == []
        assert errors[1] == [
            "A deal needs its company at Qualified or later; Fresh College is at New."
        ]
        assert errors[2] == ["Discovery needs next step and next step date."]
        assert errors[3] == ["An amount needs a currency."]
        assert body["fixup_candidates"] == 1
        r = _apply(
            http,
            body["run_id"],
            content,
            "opportunities",
            actions={"2": "skip", "4": "skip"},
            fixup_next_step=True,
        )
        assert r.status_code == 200, r.text
        assert r.json()["fixups_used"] == 1
        made = {d["name"]: d for d in _rows(fake, "crm_opportunities", academy)}
        assert made["PSG - AI"]["stage"] == "discovery"
        assert made["PSG - AI"]["amount_in_inr"] == "84000.00"
        assert made["PSG - AI"]["probability"] == 10
        assert made["PSG - Data"]["next_step"] == "Review after import"
        assert "Fresh - AI" not in made
        assert fake.rows["crm_companies"][crm["college"]["id"]]["lead_stage"] == "deal_open"
        assert new["id"] in fake.rows["crm_companies"]
        history = [h for h in _rows(fake, "crm_stage_history", academy)]
        assert len(history) == 2

    def test_a_lead_stage_that_follows_deals_is_refused(self, crm):
        http = crm["http"]
        content = _csv([["Name", "Lead stage"], ["A", "Customer"], ["B", "Nurturing"]])
        rows = _dry(http, content, "companies").json()["rows"]
        assert rows[0]["errors"][0].startswith("Deal open, Customer and Partner follow")
        assert rows[1]["errors"] == []

    def test_a_cell_that_cannot_be_read(self, crm):
        http = crm["http"]
        content = _csv([["Name", "Next touch"], ["A", "soon"]])
        [row] = _dry(http, content, "companies").json()["rows"]
        assert row["errors"] == ["Next touch: “soon” is not a date; use YYYY-MM-DD."]

    def test_the_mapping_is_checked(self, crm):
        http = crm["http"]
        content = _csv([["Org", "Web"], ["A", "a.com"]])
        r = _dry(http, content, "companies")
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Map a column to Name (or to ID, to update records).",
        )
        r = _dry(http, content, "companies", mapping={"Org": "name", "Web": "domain"})
        assert r.status_code == 200, r.text
        r = _dry(http, content, "companies", mapping={"Org": "name", "Web": "name"})
        assert r.json()["detail"] == "“Org” and “Web” both fill Name."
        r = _dry(http, content, "companies", mapping={"Org": "stage"})
        assert r.json()["detail"] == "stage is not a field a company imports."

    def test_a_multipart_upload_with_its_mapping(self, crm):
        http, fake, academy = crm["http"], crm["fake"], crm["academy"]
        content = _csv([["Org"], ["Uploaded College"]]).encode()
        r = http.post(
            f"{DRY_RUN}{ACADEMY}",
            files={"file": ("colleges.csv", content, "text/csv")},
            data={"format": "csv", "object": "companies", "mapping": json.dumps({"Org": "name"})},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        r = http.post(
            f"{API}/imports/{r.json()['run_id']}/apply{ACADEMY}",
            files={"file": ("colleges.csv", content, "text/csv")},
            data={"format": "csv", "object": "companies"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert any(c["name"] == "Uploaded College" for c in _rows(fake, "crm_companies", academy))

    def test_admins_only(self, crm):
        http = crm["http"]
        r = _dry(http, _csv([["Name"], ["A"]]), "companies", who=MEMBER["id"])
        assert r.status_code == 403


class TestSavedMappings:
    """FR-CRM-IO-01 — saved mappings."""

    def test_save_use_and_delete(self, crm):
        http, fake = crm["http"], crm["fake"]
        me = as_(OWNER["id"])
        r = http.post(
            f"{API}/csv-mappings{ACADEMY}",
            json={"object": "companies", "name": "Fair list", "mapping": {"Org": "name"}},
            headers=me,
        )
        assert r.status_code == 201, r.text
        saved = r.json()
        assert saved["object"] == "company"
        # Saving under the same name replaces it.
        again = http.post(
            f"{API}/csv-mappings{ACADEMY}",
            json={
                "object": "company",
                "name": "fair LIST",
                "mapping": {"Org": "name", "Web": "domain"},
            },
            headers=me,
        ).json()
        assert again["id"] == saved["id"]
        listed = http.get(f"{API}/csv-mappings{ACADEMY}&object=companies", headers=me).json()
        assert [m["name"] for m in listed] == ["fair LIST"]
        body = _dry(
            http, _csv([["Org", "Web"], ["A", "a.com"]]), "companies", mapping_id=saved["id"]
        ).json()
        assert body["mapping"] == {"Org": "name", "Web": "domain"}
        r = _dry(http, _csv([["First"], ["A"]]), "people", mapping_id=saved["id"])
        assert (r.status_code, r.json()["detail"]) == (422, "That mapping is for companies.")
        assert (
            http.delete(f"{API}/csv-mappings/{saved['id']}{ACADEMY}", headers=me).status_code == 200
        )
        actions = [a["action"] for a in fake.audit if a["target_table"] == "crm_csv_mappings"]
        assert actions == ["csv_mapping.created", "csv_mapping.updated", "csv_mapping.purged"]

    def test_another_workspaces_mapping_is_not_found(self, crm):
        http = crm["http"]
        me = as_(OWNER["id"])
        saved = http.post(
            f"{API}/csv-mappings{LABS}",
            json={"object": "companies", "name": "Labs", "mapping": {"Org": "name"}},
            headers=me,
        ).json()
        r = _dry(http, _csv([["Org"], ["A"]]), "companies", mapping_id=saved["id"])
        assert (r.status_code, r.json()["detail"]) == (404, "No such saved mapping.")
        r = http.delete(f"{API}/csv-mappings/{saved['id']}{ACADEMY}", headers=me)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _export(http, body, who=OWNER["id"], query=ACADEMY):
    return http.post(f"{API}/export/view{query}", json=body, headers=as_(who))


def _read_csv(response) -> list[list[str]]:
    text = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


class TestExport:
    """FR-CRM-IO-04, -05; AC "A CSV export downloads as a CSV file…"."""

    def test_the_view_keeps_its_filters_and_columns_and_sets_the_headers(self, crm):
        http = crm["http"]
        _post(http, "companies", {"name": "KCT", "city": "Coimbatore"})
        _post(http, "companies", {"name": "Amrita", "city": "Kochi"})
        spec = {
            "filters": {
                "op": "and",
                "items": [{"field": "city", "operator": "is", "value": "Coimbatore"}],
            },
            "sorts": [{"field": "name", "dir": "asc"}],
        }
        r = _export(
            http,
            {
                "object": "companies",
                "spec": spec,
                "columns": [{"field": "name", "width": 200}, "city", "owner_id"],
            },
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "text/csv; charset=utf-8"
        assert r.headers["content-disposition"] == 'attachment; filename="companies-2026-10-15.csv"'
        rows = _read_csv(r)
        assert rows[0] == ["ID", "Name", "City", "Owner"]
        assert [row[1:] for row in rows[1:]] == [["KCT", "Coimbatore", "Owner"]]

    def test_a_saved_view_by_name(self, crm):
        http = crm["http"]
        r = _export(http, {"object": "companies", "view": "All companies"})
        assert r.status_code == 200, r.text
        header = _read_csv(r)[0]
        assert header[:2] == ["ID", "Name"]

    def test_a_formula_is_neutralised(self, crm):
        http = crm["http"]
        _post(http, "companies", {"name": '=HYPERLINK("http://x","y")'})
        r = _export(
            http,
            {
                "object": "companies",
                "columns": ["name"],
                "spec": {"sorts": [{"field": "name", "dir": "asc"}]},
            },
        )
        names = [row[1] for row in _read_csv(r)[1:]]
        assert '\'=HYPERLINK("http://x","y")' in names
        assert '=HYPERLINK("http://x","y")' not in names

    @pytest.mark.parametrize("text", ["+1", "-2", "@cmd", "\tx", "\rx", "=1"])
    def test_every_formula_start(self, text):
        assert csv_io.neutralise(text) == f"'{text}"
        assert csv_io.neutralise("Acme") == "Acme"
        assert csv_io.unneutralise(csv_io.neutralise(text)) == text
        assert csv_io.unneutralise("'quoted") == "'quoted"

    def test_tva_projects_are_read_in_batches_of_ids(self, monkeypatch):
        """A whole-workspace or 500-row selection export of Labs deals reads
        their projects `ID_BATCH` ids per URL (414 otherwise)."""
        seen: list[int] = []

        def projects(*, opportunity_ids=None, company_ids=None):
            ids = opportunity_ids or company_ids or []
            seen.append(len(ids))
            return [{"id": f"p-{i}", "name": f"P {i}", "source_opportunity_id": i} for i in ids[:1]]

        def phases(project_ids):
            seen.append(len(project_ids))
            return []

        monkeypatch.setattr(tva.db, "list_crm_projects", projects)
        monkeypatch.setattr(tva.db, "list_project_phases", phases)
        found = tva.blocks(opportunity_ids=[str(i) for i in range(250)])
        assert seen == [100, 100, 50, 3]
        assert [b["source_opportunity_id"] for b in found] == ["0", "100", "200"]

    def test_past_five_megabytes_is_413_with_the_sentence(self, crm, monkeypatch):
        http = crm["http"]
        monkeypatch.setattr(csv_io, "EXPORT_MAX_BYTES", 100)
        for n in range(5):
            _post(http, "companies", {"name": f"College number {n}"})
        r = _export(http, {"object": "companies", "columns": ["name"]})
        assert (r.status_code, r.json()["detail"]) == (
            413,
            "This export is larger than 5 MB; export one object, or a narrower view.",
        )
        r = http.get(f"{API}/export/workspace{ACADEMY}", headers=as_(OWNER["id"]))
        assert r.status_code == 413

    def test_the_bulk_bar_exports_its_selection(self, crm):
        http, college = crm["http"], crm["college"]
        other = _post(http, "companies", {"name": "KCT"})
        r = _export(
            http,
            {
                "object": "companies",
                "columns": ["name"],
                "ids": [other["id"], college["id"], "not-an-id"],
            },
        )
        assert [row[1] for row in _read_csv(r)[1:]] == ["KCT", "PSG Tech"]

    def test_a_viewer_cannot_export(self, crm):
        http = crm["http"]
        r = _export(http, {"object": "companies"}, who=VIEWER["id"])
        assert (r.status_code, r.json()["detail"]) == (403, "Viewers cannot export.")

    def test_a_member_exports_a_view(self, crm):
        http = crm["http"]
        r = _export(http, {"object": "companies", "columns": ["name"]}, who=MEMBER["id"])
        assert r.status_code == 200

    def test_the_workspace_as_json_admin_only_whole_or_per_object(self, crm):
        http = crm["http"]
        me = as_(OWNER["id"])
        r = http.get(f"{API}/export/workspace{ACADEMY}", headers=me)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/json"
        assert r.headers["content-disposition"] == (
            'attachment; filename="academy-workspace-2026-10-15.json"'
        )
        data = json.loads(r.content)
        assert set(data["objects"]) == {
            "companies",
            "people",
            "opportunities",
            "tasks",
            "notes",
            "campaigns",
            "campaign_members",
            "drive_links",
            # Spec 009 FR-CRMAI-CHG-11.
            "review_items",
            "email_summaries",
            "enrichment",
        }
        assert [c["name"] for c in data["objects"]["companies"]] == ["PSG Tech"]
        r = http.get(f"{API}/export/workspace{ACADEMY}&object=people", headers=me)
        assert set(json.loads(r.content)["objects"]) == {"people"}
        assert 'filename="academy-people-2026-10-15.json"' in r.headers["content-disposition"]
        r = http.get(f"{API}/export/workspace{ACADEMY}", headers=as_(MEMBER["id"]))
        assert r.status_code == 403

    def test_both_are_audited_without_values(self, crm):
        http, fake = crm["http"], crm["fake"]
        _export(http, {"object": "companies", "columns": ["name"]})
        http.get(f"{API}/export/workspace{ACADEMY}&object=companies", headers=as_(OWNER["id"]))
        view, whole = [a for a in fake.audit if a["action"].startswith("export.")]
        assert (view["action"], view["target_table"]) == ("export.view", "crm_companies")
        assert view["after"]["rows"] == 1 and view["after"]["columns"] == ["name"]
        assert "PSG Tech" not in json.dumps(view) + json.dumps(whole)
        assert whole["action"] == "export.workspace"
        assert whole["after"]["rows"] == {"companies": 1}


class TestRoundTrip:
    """Quickstart §3 S11: export a view to CSV; import it back in update mode
    with no changes."""

    def test_an_exported_view_imports_back_unchanged(self, crm):
        http, fake = crm["http"], crm["fake"]
        _patch(
            http,
            "companies",
            crm["college"]["id"],
            {
                "city": "Coimbatore",
                "next_touch_on": "2026-11-01",
                "account_type_id": _option(fake, crm["academy"], "Client"),
            },
        )
        columns = [
            "name",
            "domain",
            "lead_stage",
            "account_type_id",
            "owner_id",
            "city",
            "next_touch_on",
            "created_at",
        ]
        exported = _export(http, {"object": "companies", "columns": columns})
        content = exported.content.decode("utf-8-sig")
        body = _dry(http, content, "companies", mode="update").json()
        assert body["mapping"]["ID"] == "id" and body["mapping"]["Created at"] is None
        assert body["counts"]["companies"] == {
            "in_file": 1,
            "create": 0,
            "update": 0,
            "unchanged": 1,
            "skipped": 0,
        }
        audit_before = len(fake.audit)
        r = _apply(http, body["run_id"], content, "companies", mode="update")
        assert r.status_code == 200, r.text
        assert (r.json()["created"], r.json()["updated"]) == (0, 0)
        assert [a["action"] for a in fake.audit[audit_before:]] == ["import.applied"]

    def test_a_neutralised_cell_reads_back_as_it_was(self, crm):
        http = crm["http"]
        _patch(
            http,
            "companies",
            crm["college"]["id"],
            {"name": '=HYPERLINK("http://x","y")', "description": "- follow up in Nov"},
        )
        exported = _export(
            http, {"object": "companies", "columns": ["name", "domain", "description"]}
        )
        content = exported.content.decode("utf-8-sig")
        assert "'=HYPERLINK" in content and "'- follow up" in content
        body = _dry(http, content, "companies", mode="update").json()
        assert body["counts"]["companies"]["unchanged"] == 1, body["rows"]

    def test_people_and_deals_too(self, crm):
        http = crm["http"]
        _post(
            http,
            "opportunities",
            {
                "name": "PSG - AI",
                "company_id": crm["college"]["id"],
                "amount": "1000",
                "currency": "USD",
                "next_step": "Call",
                "next_step_on": "2026-10-20",
            },
        )
        for obj, columns in (
            ("people", ["name", "company_id", "primary_email", "owner_id"]),
            (
                "opportunities",
                [
                    "name",
                    "company_id",
                    "amount",
                    "currency",
                    "next_step",
                    "next_step_on",
                    "owner_id",
                    "stage",
                    "probability",
                ],
            ),
        ):
            content = _export(http, {"object": obj, "columns": columns}).content.decode()
            counts = _dry(http, content, obj, mode="update").json()["counts"]
            assert counts[obj]["unchanged"] == 1, (obj, counts)


class TestTheTool:
    """FR-CRM-MCP-01 — `crm_import` with CSV."""

    def test_a_csv_dry_run_and_run_through_the_bridge(self, crm):
        fake, academy = crm["fake"], crm["academy"]
        me = Ctx(OWNER["id"])
        content = _csv([["Name", "Domain"], ["PSG Tech", "psg.edu"], ["KCT", "kct.ac.in"]])
        dry = asyncio.run(
            tools.crm_import(me, content, "csv", object="companies", workspace="academy")
        )
        assert dry["counts"]["companies"]["create"] == 1
        done = asyncio.run(
            tools.crm_import(
                me,
                content,
                "csv",
                mode="run",
                run_id=dry["run_id"],
                object="companies",
                on_match="update",
                workspace="academy",
            )
        )
        assert done["created"] == 1
        assert any(c["name"] == "KCT" for c in _rows(fake, "crm_companies", academy))


class TestTheReviewQueueInTheExport:
    """Spec 009 FR-CRMAI-CHG-11, data-model §4 — the workspace export holds
    the Review Queue's items with their deal choices, ids only, and leaves
    out an item whose target is in the Trash; never the AI usage log."""

    def _item(self, crm, **values):
        return crm["fake"].add_review_item(
            crm["academy"],
            type="enrichment_conflict",
            source_kind="enrichment",
            record_type="company",
            record_id=crm["college"]["id"],
            suggestion={"field": "city", "value": "Coimbatore", "label": "City"},
            current_values={"city": None},
            **values,
        )

    def _export(self, crm, query=""):
        r = crm["http"].get(f"{API}/export/workspace{ACADEMY}{query}", headers=as_(OWNER["id"]))
        assert r.status_code == 200, r.text
        return json.loads(r.content)["objects"]

    def test_items_with_their_deal_choices_ids_only(self, crm):
        item = self._item(crm)
        [row] = self._export(crm)["review_items"]
        assert (row["id"], row["record_id"], row["deal_choices"]) == (
            item["id"],
            crm["college"]["id"],
            [],
        )
        assert "PSG Tech" not in json.dumps(row)
        assert "crm_ai_usage" not in self._export(crm)

    def test_one_object_and_its_audit_row(self, crm):
        self._item(crm)
        objects = self._export(crm, "&object=review_items")
        assert set(objects) == {"review_items"}
        [audit] = [a for a in crm["fake"].audit if a["action"] == "export.workspace"]
        assert audit["target_table"] == "crm_review_items"

    def test_a_trashed_target_is_left_out(self, crm):
        self._item(crm)
        crm["fake"].rows["crm_companies"][crm["college"]["id"]]["deleted_at"] = (
            "2026-10-15T00:00:00+00:00"
        )
        assert self._export(crm)["review_items"] == []


class TestTheEmailSummariesInTheExport:
    """Spec 009 FR-CRMAI-CHG-11, data-model §4 — the workspace export holds
    the email thread summaries with their links, ids only, and leaves out a
    summary linked only to records in the Trash."""

    def _summary(self, crm):
        fake = crm["fake"]
        mailbox = fake.add(
            "crm_mailboxes",
            workspace_id=crm["academy"]["id"],
            email="owner@nunnari.academy",
            owner_id=OWNER["id"],
            labels=["Clients"],
            is_active=True,
        )
        summary = fake.add(
            "crm_email_summaries",
            workspace_id=crm["academy"]["id"],
            mailbox_id=mailbox["id"],
            thread_id="t1",
            summary="They asked for a pilot. We will send the plan.",
            asks=["A pilot plan"],
            commitments=[],
            next_step=None,
            next_step_on=None,
            sentiment="positive",
            watermark_message_id="g1",
            watermark_at="2026-10-15T00:00:00+00:00",
            model="claude-sonnet-5-5",
            generated_at="2026-10-15T00:00:00+00:00",
            created_at="2026-10-15T00:00:00+00:00",
        )
        fake.add(
            "crm_email_summary_links",
            workspace_id=crm["academy"]["id"],
            summary_id=summary["id"],
            record_type="company",
            record_id=crm["college"]["id"],
        )
        return summary

    def _export(self, crm, query=""):
        r = crm["http"].get(f"{API}/export/workspace{ACADEMY}{query}", headers=as_(OWNER["id"]))
        assert r.status_code == 200, r.text
        return json.loads(r.content)["objects"]

    def test_summaries_with_their_links_ids_only(self, crm):
        summary = self._summary(crm)
        [row] = self._export(crm)["email_summaries"]
        assert (row["id"], row["thread_id"], row["links"]) == (
            summary["id"],
            "t1",
            [{"record_type": "company", "record_id": crm["college"]["id"]}],
        )
        assert "PSG Tech" not in json.dumps(row)

    def test_one_object_and_its_audit_row(self, crm):
        self._summary(crm)
        objects = self._export(crm, "&object=email_summaries")
        assert set(objects) == {"email_summaries"}
        [audit] = [a for a in crm["fake"].audit if a["action"] == "export.workspace"]
        assert audit["target_table"] == "crm_email_summaries"

    def test_a_summary_linked_only_to_the_trash_is_left_out(self, crm):
        self._summary(crm)
        crm["fake"].rows["crm_companies"][crm["college"]["id"]]["deleted_at"] = (
            "2026-10-15T00:00:00+00:00"
        )
        assert self._export(crm)["email_summaries"] == []


class TestTheEnrichmentInTheExport:
    """Spec 009 FR-CRMAI-CHG-11, data-model §4 — the workspace export holds
    the company enrichment rows, ids only, and leaves out one of a company
    in the Trash."""

    def _export(self, crm, query=""):
        r = crm["http"].get(f"{API}/export/workspace{ACADEMY}{query}", headers=as_(OWNER["id"]))
        assert r.status_code == 200, r.text
        return json.loads(r.content)["objects"]

    def _enriched(self, crm):
        return crm["fake"].add_enrichment(
            crm["academy"],
            crm["college"],
            website="https://psgtech.edu/",
            values={"city": "Coimbatore", "products": ["Engineering degrees"]},
        )

    def test_enrichment_rows_ids_only(self, crm):
        made = self._enriched(crm)
        [row] = self._export(crm)["enrichment"]
        assert (row["id"], row["company_id"], row["website"], row["status"]) == (
            made["id"],
            crm["college"]["id"],
            "https://psgtech.edu/",
            "done",
        )
        assert row["values"]["city"] == "Coimbatore"
        assert "PSG Tech" not in json.dumps(row)

    def test_one_object_and_its_audit_row(self, crm):
        self._enriched(crm)
        assert set(self._export(crm, "&object=enrichment")) == {"enrichment"}
        [audit] = [a for a in crm["fake"].audit if a["action"] == "export.workspace"]
        assert audit["target_table"] == "crm_company_enrichment"

    def test_a_company_in_the_trash_is_left_out(self, crm):
        self._enriched(crm)
        crm["fake"].rows["crm_companies"][crm["college"]["id"]]["deleted_at"] = (
            "2026-10-15T00:00:00+00:00"
        )
        assert self._export(crm)["enrichment"] == []
