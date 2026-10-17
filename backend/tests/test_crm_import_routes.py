"""The Twenty importer through its routes — spec 007 FR-CRM-MIG-01, -03,
-04, -06 to -08, FR-CRM-DUP-02, FR-CRM-MCP-08, FR-CRM-CHG-08; contracts
§2.9, §3 (tool 15); spec §11 AC "The Twenty importer's dry run reports
counts… A second run changes nothing"; journey 4.6's independent test.

The fixtures in `tests/fixtures/twenty/` are hand-written from the shape
contracts §2.9 declares, with known counts and deliberate conflicts: a
domain and an email given twice, an owner who is not a member, a deal on a
company that is nowhere, an open deal with no next step, a USD deal whose
month has no rate yet, and a task linked to nothing known. Routes run
against `tests/crm_fake.py`, whose `crm_apply` keeps 030's CHECKs.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from crm_fake import MEMBER, OUTSIDER, OWNER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.mcp import crm as tools
from app.services.crm import importer

FIXTURES = Path(__file__).parent / "fixtures" / "twenty"
LABS_JSON = (FIXTURES / "labs.json").read_text()
ACADEMY_JSON = (FIXTURES / "academy.json").read_text()
CSV_ORDER = ("companies", "people", "opportunities", "tasks", "notes", "attachments")
LABS = "?workspace=labs"
DRY_RUN = "/api/v1/crm/imports/dry-run"


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.add_deal_lists(labs)
    fake.add_deal_lists(academy)
    fake.add(
        "crm_select_options",
        workspace_id=labs["id"],
        list_key="company.account_type",
        system_key="technology_partner",
        label="Technology partner",
        color=None,
        position=9,
        is_default=False,
        is_active=True,
        is_fixed=False,
    )
    monkeypatch.setattr(importer, "today_in_company_tz", lambda: date(2026, 10, 15))
    return crm_client(monkeypatch, fake=fake), fake, labs, academy


def _usd_rate(fake, labs) -> None:
    fake.add(
        "crm_fx_rates", workspace_id=labs["id"], currency="USD", month="2026-09-01", rate="83.5"
    )


def _dry(http, content=LABS_JSON, query=LABS, who=OWNER["id"], **extra):
    return http.post(
        f"{DRY_RUN}{query}",
        json={"format": "twenty_json", "content": content, **extra},
        headers=as_(who),
    )


def _apply(http, run_id, content=LABS_JSON, query=LABS, fixup=True, who=OWNER["id"], **extra):
    return http.post(
        f"/api/v1/crm/imports/{run_id}/apply{query}",
        json={"format": "twenty_json", "content": content, "fixup_next_step": fixup, **extra},
        headers=as_(who),
    )


def _imported(http, fake, labs) -> dict:
    """Dry run and real run of labs.json, with the USD rate set."""
    _usd_rate(fake, labs)
    run = _dry(http).json()["run_id"]
    applied = _apply(http, run)
    assert applied.status_code == 200, applied.text
    return applied.json()


def _by_twenty(fake, table, ws) -> dict:
    return {
        r["twenty_id"]: r
        for r in fake.rows[table].values()
        if r["workspace_id"] == ws["id"] and r.get("twenty_id")
    }


class TestWho:
    """Contracts §2.9 — Admins only."""

    @pytest.mark.parametrize("who", [MEMBER["id"], VIEWER["id"]])
    def test_a_member_or_viewer_is_refused(self, crm, who):
        http, *_ = crm
        r = _dry(http, who=who)
        assert r.status_code == 403
        assert r.json()["detail"] == "Only a workspace admin can do that."

    def test_a_member_cannot_list_the_runs(self, crm):
        http, *_ = crm
        assert http.get(f"/api/v1/crm/imports{LABS}", headers=as_(MEMBER["id"])).status_code == 403


class TestDryRun:
    """FR-CRM-MIG-04 — counts, derived lead stages, conflicts, attachments."""

    def test_the_counts_match_the_fixture(self, crm):
        http, *_ = crm
        r = _dry(http)
        assert r.status_code == 200, r.text
        counts = r.json()["counts"]
        assert {obj: (c["in_file"], c["create"], c["skipped"]) for obj, c in counts.items()} == {
            "companies": (6, 6, 0),
            "people": (4, 4, 0),
            "opportunities": (5, 4, 1),
            "tasks": (3, 2, 1),
            "notes": (2, 2, 0),
        }

    def test_each_company_has_its_derived_lead_stage(self, crm):
        http, *_ = crm
        stages = {s["company"]: s["derived"] for s in _dry(http).json()["lead_stages"]}
        assert stages == {
            "Acme Robotics": "deal_open",
            "Globex": "partner",
            "Initech": "customer",
            "Umbrella Corp": "nurturing",
            "Hooli": "qualified",
            "Acme Robotics Pvt": "new",
        }

    def test_every_deliberate_conflict_is_listed(self, crm):
        http, fake, labs, _ = crm
        fake.add(
            "crm_companies",
            workspace_id=labs["id"],
            name="Hooli Inc",
            owner_id=OWNER["id"],
            lead_stage="new",
            deleted_at=None,
        )
        conflicts = _dry(http).json()["conflicts"]
        assert {(c["object"], c["row"], c["kind"], c["blocking"]) for c in conflicts} == {
            ("companies", 5, "unknown_owner", False),
            ("companies", 5, "similar_name", False),
            ("companies", 6, "duplicate_domain", False),
            ("people", 4, "duplicate_email", False),
            ("opportunities", 1, "missing_fx_rate", True),
            ("opportunities", 4, "no_next_step", True),
            ("opportunities", 5, "missing_company", False),
            ("tasks", 3, "missing_target", False),
            ("tasks", 3, "no_link", False),
        }
        detail = next(c["detail"] for c in conflicts if c["kind"] == "missing_fx_rate")
        assert detail == "No USD rate for 2026-09 in Nunnari Labs."

    def test_attachments_are_listed_never_fetched(self, crm):
        """FR-CRM-MIG-03."""
        http, *_ = crm
        listed = _dry(http).json()["attachments"]
        assert [(a["name"], a["record_type"], a["record"]) for a in listed] == [
            ("Acme proposal v2.pdf", "opportunity", "Acme pilot"),
            ("Globex logo.png", "company", "Globex"),
        ]

    def test_a_dry_run_writes_only_its_run_and_audit_row(self, crm):
        http, fake, labs, _ = crm
        run_id = _dry(http).json()["run_id"]
        assert fake.applied == []
        assert all(not fake.rows[t] for t in ("crm_companies", "crm_people", "crm_opportunities"))
        [audit] = [a for a in fake.audit if a["target_table"] == "crm_import_runs"]
        assert (audit["action"], audit["target_id"], audit["actor_label"]) == (
            "import.dry_run",
            run_id,
            "import",
        )
        assert audit["after"]["counts"]["companies"]["in_file"] == 6
        assert "Acme Robotics" not in json.dumps(audit)

    def test_the_runs_list_has_counts_and_no_rows(self, crm):
        http, *_ = crm
        run_id = _dry(http).json()["run_id"]
        [run] = http.get(f"/api/v1/crm/imports{LABS}", headers=as_(OWNER["id"])).json()
        assert (run["id"], run["status"], run["counts"]["people"]["in_file"]) == (
            run_id,
            "dry_run",
            4,
        )
        assert "report" not in run and "conflicts" not in json.dumps(run)

    def test_csv_as_a_format_is_csv_import(self, crm):
        # S11b: a CSV of one object is CSV import's (tests/test_crm_csv_io.py).
        http, *_ = crm
        r = http.post(
            f"{DRY_RUN}{LABS}",
            json={"format": "csv", "content": "name\nAcme\n", "object": "companies"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["counts"] == {
            "companies": {"in_file": 1, "create": 1, "update": 0, "unchanged": 0, "skipped": 0}
        }

    def test_a_file_that_is_not_json_is_refused(self, crm):
        http, *_ = crm
        r = _dry(http, content="{nope")
        assert (r.status_code, r.json()["detail"]) == (422, "The file is not valid JSON.")


class TestApply:
    """FR-CRM-MIG-04, -07 — the real run needs the dry run's file."""

    def test_the_real_run_waits_for_what_blocks_it(self, crm):
        http, fake, labs, _ = crm
        run_id = _dry(http).json()["run_id"]
        r = _apply(http, run_id)
        assert r.status_code == 422
        assert (
            r.json()["detail"]
            == "The import cannot run yet: No USD rate for 2026-09 in Nunnari Labs."
        )
        assert fake.applied == []

    def test_another_file_is_refused(self, crm):
        http, fake, labs, _ = crm
        _usd_rate(fake, labs)
        run_id = _dry(http).json()["run_id"]
        r = _apply(http, run_id, content=LABS_JSON.replace("Acme pilot", "Acme pilot!"))
        assert (r.status_code, r.json()["detail"]) == (409, importer.NOT_THE_FILE)

    def test_a_dry_run_older_than_a_day_is_refused(self, crm):
        http, fake, labs, _ = crm
        _usd_rate(fake, labs)
        run_id = _dry(http).json()["run_id"]
        stale = datetime.now(UTC) - timedelta(hours=25)
        fake.rows["crm_import_runs"][run_id]["created_at"] = stale.isoformat()
        r = _apply(http, run_id)
        assert (r.status_code, r.json()["detail"]) == (409, importer.TOO_OLD)

    def test_another_workspaces_run_is_not_found(self, crm):
        http, fake, labs, _ = crm
        run_id = _dry(http, content=ACADEMY_JSON, query="?workspace=academy").json()["run_id"]
        r = _apply(http, run_id)
        assert (r.status_code, r.json()["detail"]) == (404, "No such import run.")

    def test_the_real_run_reports_its_counts_and_fixups(self, crm):
        http, fake, labs, _ = crm
        done = _imported(http, fake, labs)
        assert done["fixups_used"] == 1
        assert {obj: c["create"] for obj, c in done["counts"].items()} == {
            "companies": 6,
            "people": 4,
            "opportunities": 4,
            "tasks": 2,
            "notes": 2,
        }
        [run] = fake.rows["crm_import_runs"].values()
        assert (run["status"], run["options"]["fixup_next_step"]) == ("applied", True)

    def test_a_second_run_changes_nothing(self, crm):
        http, fake, labs, _ = crm
        _imported(http, fake, labs)
        rows = {t: json.dumps(fake.rows[t], sort_keys=True, default=str) for t in fake.rows}
        audit = len(fake.audit)
        run_id = _dry(http).json()["run_id"]
        again = _apply(http, run_id)
        assert again.status_code == 200, again.text
        assert all(c["create"] == c["update"] == 0 for c in again.json()["counts"].values())
        assert again.json()["fixups_used"] == 0
        changed = [
            t
            for t in fake.rows
            if t != "crm_import_runs"
            and json.dumps(fake.rows[t], sort_keys=True, default=str) != rows[t]
        ]
        assert changed == []
        # Only the second run's own two rows: its dry run and its real run.
        assert [a["action"] for a in fake.audit[audit:]] == ["import.dry_run", "import.applied"]

    def test_another_admin_rerunning_keeps_the_owners(self, crm):
        """FR-CRM-MIG-07 — the importer stands in for a blank or unknown owner
        only on a new record: a second Admin's re-run, or one after a
        reassignment in the CRM, moves no owner."""
        http, fake, labs, _ = crm
        fake.add_member(labs, OUTSIDER["id"], "admin")
        _imported(http, fake, labs)
        hooli = _by_twenty(fake, "crm_companies", labs)["c-hooli"]
        hooli["owner_id"] = MEMBER["id"]
        owners = {
            t: {k: (r.get("owner_id"), r.get("assignee_id")) for k, r in rows.items()}
            for t in ("crm_companies", "crm_people", "crm_opportunities", "tasks")
            for rows in [_by_twenty(fake, t, labs)]
        }
        dry = _dry(http, who=OUTSIDER["id"]).json()
        assert all(c["update"] == 0 for c in dry["counts"].values())
        again = _apply(http, dry["run_id"], who=OUTSIDER["id"])
        assert again.status_code == 200, again.text
        assert all(c["create"] == c["update"] == 0 for c in again.json()["counts"].values())
        assert {
            t: {k: (r.get("owner_id"), r.get("assignee_id")) for k, r in rows.items()}
            for t in owners
            for rows in [_by_twenty(fake, t, labs)]
        } == owners

    def test_a_rerun_does_not_reopen_a_task_completed_in_the_crm(self, crm):
        http, fake, labs, _ = crm
        _imported(http, fake, labs)
        task = _by_twenty(fake, "tasks", labs)["t-call-asha"]
        task.update(status="done", completed_at="2026-10-14T10:00:00+00:00")
        run_id = _dry(http).json()["run_id"]
        again = _apply(http, run_id)
        assert again.status_code == 200, again.text
        assert again.json()["counts"]["tasks"]["update"] == 0
        task = _by_twenty(fake, "tasks", labs)["t-call-asha"]
        assert (task["status"], task["completed_at"]) == ("done", "2026-10-14T10:00:00+00:00")

    def test_an_imported_task_is_import_sourced(self, crm):
        """008 FR-TASK-DATA-12 — as 047 marks the Twenty tasks it found."""
        http, fake, labs, _ = crm
        _imported(http, fake, labs)
        tasks = _by_twenty(fake, "tasks", labs)
        assert tasks
        assert {t["source"] for t in tasks.values()} == {"import"}

    def test_the_links_are_reproduced_with_the_twenty_ids(self, crm):
        """Journey 4.6 — every company, person, deal, task and note with its
        links and its Twenty id."""
        http, fake, labs, _ = crm
        _imported(http, fake, labs)
        companies = _by_twenty(fake, "crm_companies", labs)
        people = _by_twenty(fake, "crm_people", labs)
        deals = _by_twenty(fake, "crm_opportunities", labs)
        tasks = _by_twenty(fake, "tasks", labs)
        notes = _by_twenty(fake, "crm_notes", labs)
        assert set(companies) == {
            "c-acme",
            "c-globex",
            "c-initech",
            "c-umbrella",
            "c-hooli",
            "c-acme-pvt",
        }
        assert people["p-asha"]["company_id"] == companies["c-acme"]["id"]
        assert people["p-dana"]["company_id"] == companies["c-umbrella"]["id"]
        assert deals["o-acme-pilot"]["company_id"] == companies["c-acme"]["id"]
        assert deals["o-acme-pilot"]["point_of_contact_id"] == people["p-asha"]["id"]
        task_links = {
            (link["record_type"], link["record_id"])
            for link in fake.rows["task_links"].values()
            if link["task_id"] == tasks["t-call-asha"]["id"]
        }
        assert task_links == {
            ("person", people["p-asha"]["id"]),
            ("opportunity", deals["o-acme-pilot"]["id"]),
        }
        note_links = {
            (link["record_type"], link["record_id"])
            for link in fake.rows["crm_note_links"].values()
            if link["note_id"] == notes["n-kickoff"]["id"]
        }
        assert note_links == {
            ("company", companies["c-acme"]["id"]),
            ("opportunity", deals["o-acme-pilot"]["id"]),
        }
        domains = {d["domain"]: d["company_id"] for d in fake.rows["crm_company_domains"].values()}
        assert domains["acme.io"] == companies["c-acme"]["id"]
        assert companies["c-acme-pvt"]["id"] not in domains.values()

    def test_the_imported_values(self, crm):
        http, fake, labs, _ = crm
        _imported(http, fake, labs)
        companies = _by_twenty(fake, "crm_companies", labs)
        people = _by_twenty(fake, "crm_people", labs)
        deals = _by_twenty(fake, "crm_opportunities", labs)
        tasks = _by_twenty(fake, "tasks", labs)
        acme = companies["c-acme"]
        assert (acme["lead_stage"], acme["city"], acme["owner_id"]) == (
            "deal_open",
            "Chennai",
            OWNER["id"],
        )
        # FR-CRM-MIG-02: Last inbound and outbound from the people's timestamps.
        assert (acme["last_inbound_at"], acme["last_outbound_at"]) == (
            "2026-10-01T10:00:00+00:00",
            "2026-10-02T11:30:00+00:00",
        )
        assert companies["c-hooli"]["owner_id"] == OWNER["id"]
        assert people["p-asha"]["primary_email"] == "asha@acme.io"
        assert people["p-dana"]["additional_emails"] == ["asha@acme.io"]
        pilot = deals["o-acme-pilot"]
        assert (pilot["stage"], pilot["amount"], pilot["amount_in_inr"], pilot["probability"]) == (
            "proposal",
            "50000.00",
            "4175000.00",
            50,
        )
        lost = deals["o-umbrella-audit"]
        assert (lost["stage"], lost["lost_on"]) == ("closed_lost", "2026-07-15")
        assert fake.rows["crm_select_options"][lost["lost_reason_id"]]["label"] == "Price"
        assert deals["o-initech-rollout"]["won_on"] == "2026-08-31"
        phase2 = deals["o-acme-phase2"]
        assert (phase2["next_step"], phase2["next_step_on"], phase2["owner_id"]) == (
            "Review after import",
            "2026-10-15",
            MEMBER["id"],
        )
        assert tasks["t-globex-deck"]["completed_at"] == "2026-09-05T09:00:00+00:00"
        assert tasks["t-call-asha"]["due_on"] == "2026-10-16"
        # FR-CRM-OPP-02: an imported deal's first stage is in its history.
        history = [
            h for h in fake.rows["crm_stage_history"].values() if h["opportunity_id"] == pilot["id"]
        ]
        assert [(h["to_stage"], h["actor_kind"]) for h in history] == [("proposal", "import")]

    def test_labs_deals_at_proposal_or_later_create_no_project(self, crm):
        """FR-CRM-MIG-06."""
        http, fake, labs, _ = crm
        _imported(http, fake, labs)
        assert fake.rows["projects"] == {}

    def test_every_write_is_audited_as_the_import(self, crm):
        http, fake, labs, _ = crm
        _imported(http, fake, labs)
        rows = [a for a in fake.audit if a["target_table"] == "crm_companies"]
        assert rows and {(a["actor_label"], a["actor_id"]) for a in rows} == {
            ("import", OWNER["id"])
        }
        assert fake.audit[-1]["action"] == "import.applied"

    def test_a_re_run_refreshes_from_the_file_and_never_blanks_a_field(self, crm):
        http, fake, labs, _ = crm
        _imported(http, fake, labs)
        globex = _by_twenty(fake, "crm_companies", labs)["c-globex"]
        globex["city"] = "Pune"
        run_id = _dry(http).json()["run_id"]
        counts = _apply(http, run_id).json()["counts"]
        assert counts["companies"]["update"] == 0  # the file has no city for Globex: Pune stays
        globex["name"] = "Globex Renamed"
        counts = _apply(http, run_id).json()["counts"]
        assert counts["companies"]["update"] == 1
        globex = _by_twenty(fake, "crm_companies", labs)["c-globex"]
        assert (globex["name"], globex["city"]) == ("Globex", "Pune")

    def test_the_same_twenty_ids_in_two_workspaces_do_not_meet(self, crm):
        """FR-CRM-MIG-01, -WS-02 — each Twenty workspace maps to its own."""
        http, fake, labs, academy = crm
        _imported(http, fake, labs)
        query = "?workspace=academy"
        run_id = _dry(http, content=ACADEMY_JSON, query=query).json()["run_id"]
        r = _apply(http, run_id, content=ACADEMY_JSON, query=query)
        assert r.status_code == 200, r.text
        assert r.json()["counts"]["companies"]["create"] == 2
        assert _by_twenty(fake, "crm_companies", academy)["c-acme"]["name"] == "Acme College"
        assert _by_twenty(fake, "crm_companies", labs)["c-acme"]["name"] == "Acme Robotics"


class TestUploads:
    """FR-CRM-CHG-08 — multipart uploads up to 4 MB, here as in the proxy."""

    def test_an_upload_dry_runs_and_applies(self, crm):
        http, fake, labs, _ = crm
        _usd_rate(fake, labs)
        files = {"file": ("labs.json", LABS_JSON.encode(), "application/json")}
        r = http.post(f"{DRY_RUN}{LABS}", files=files, headers=as_(OWNER["id"]))
        assert r.status_code == 200, r.text
        r = http.post(
            f"/api/v1/crm/imports/{r.json()['run_id']}/apply{LABS}",
            files=files,
            data={"fixup_next_step": "true"},
            headers=as_(OWNER["id"]),
        )
        assert (r.status_code, r.json()["fixups_used"]) == (200, 1)

    def test_csv_files_one_object_at_a_time_link_through_their_twenty_ids(self, crm):
        http, fake, labs, _ = crm
        for obj in CSV_ORDER:
            content = (FIXTURES / "labs_csv" / f"{obj}.csv").read_bytes()
            files = {"file": (f"{obj}.csv", content, "text/csv")}
            dry = http.post(f"{DRY_RUN}{LABS}", files=files, headers=as_(OWNER["id"]))
            assert dry.status_code == 200, dry.text
            assert dry.json()["blocking"] == [], obj
            if obj == "attachments":
                assert [a["record"] for a in dry.json()["attachments"]] == ["Zenith Analytics"]
                continue
            done = http.post(
                f"/api/v1/crm/imports/{dry.json()['run_id']}/apply{LABS}",
                files=files,
                headers=as_(OWNER["id"]),
            )
            assert done.status_code == 200, done.text
            assert done.json()["counts"][obj]["skipped"] == 0
        companies = _by_twenty(fake, "crm_companies", labs)
        people = _by_twenty(fake, "crm_people", labs)
        deals = _by_twenty(fake, "crm_opportunities", labs)
        assert people["p-zara"]["additional_emails"] == ["zara.iyer@gmail.com", "zi@zenith.ai"]
        assert deals["o-zenith-poc"]["point_of_contact_id"] == people["p-zara"]["id"]
        # The deals arrived after their companies: the lead stage follows them.
        assert {t: companies[t]["lead_stage"] for t in companies} == {
            "c-zenith": "deal_open",
            "c-orbit": "nurturing",
            "c-pixel": "customer",
        }
        assert companies["c-pixel"]["name"] == "Pixel, Paint & Co"
        orbit_task = _by_twenty(fake, "tasks", labs)["t-orbit"]
        links = {
            link["record_type"]
            for link in fake.rows["task_links"].values()
            if link["task_id"] == orbit_task["id"]
        }
        assert links == {"person", "company"}

    def test_an_export_posted_as_it_is(self, crm):
        """Quickstart §3 S8 — `-d @labs.json` with a JSON content type."""
        http, fake, labs, _ = crm
        _usd_rate(fake, labs)
        headers = {**as_(OWNER["id"]), "Content-Type": "application/json"}
        dry = http.post(f"{DRY_RUN}{LABS}", content=LABS_JSON.encode(), headers=headers)
        assert dry.status_code == 200, dry.text
        url = f"/api/v1/crm/imports/{dry.json()['run_id']}/apply{LABS}&fixup_next_step=true"
        for creates in (6, 0):
            done = http.post(url, content=LABS_JSON.encode(), headers=headers)
            assert done.json()["counts"]["companies"]["create"] == creates

    def test_a_csv_must_name_its_object(self, crm):
        http, *_ = crm
        files = {"file": ("export.csv", b"id,name\nc1,Acme\n", "text/csv")}
        r = http.post(f"{DRY_RUN}{LABS}", files=files, headers=as_(OWNER["id"]))
        assert r.status_code == 422 and "Name the object" in r.json()["detail"]

    def test_a_file_over_4_mb_is_refused(self, crm):
        http, *_ = crm
        big = b"x" * (4 * 1024 * 1024 + 1)
        r = http.post(
            f"{DRY_RUN}{LABS}",
            files={"file": ("companies.csv", big, "text/csv")},
            headers=as_(OWNER["id"]),
        )
        assert (r.status_code, r.json()["detail"]) == (413, importer.TOO_BIG)

    def test_json_content_over_4_mb_is_refused(self, crm):
        http, *_ = crm
        r = _dry(http, content="x" * (4 * 1024 * 1024 + 1))
        assert r.status_code == 413

    def test_a_chunked_body_with_no_length_is_counted_too(self, crm):
        http, *_ = crm

        def chunks():
            for _ in range(10):
                yield b"x" * (1024 * 1024)

        r = http.post(
            f"{DRY_RUN}{LABS}",
            content=chunks(),
            headers={**as_(OWNER["id"]), "Content-Type": "application/json"},
        )
        assert r.status_code == 413

    def test_another_content_type_is_refused(self, crm):
        http, *_ = crm
        r = http.post(
            f"{DRY_RUN}{LABS}",
            content=b"hello",
            headers={**as_(OWNER["id"]), "Content-Type": "text/plain"},
        )
        assert r.status_code == 415


class TestThroughMcp:
    """FR-CRM-MCP-01 (S8: crm_import), -08; contracts §3 tool 15."""

    def test_a_dry_run_then_the_run(self, crm):
        http, fake, labs, _ = crm
        _usd_rate(fake, labs)
        dry = asyncio.run(
            tools.crm_import(Ctx(OWNER["id"]), LABS_JSON, "twenty_json", workspace="labs")
        )
        assert dry["counts"]["companies"]["create"] == 6
        done = asyncio.run(
            tools.crm_import(
                Ctx(OWNER["id"]),
                LABS_JSON,
                "twenty_json",
                mode="run",
                run_id=dry["run_id"],
                fixup_next_step=True,
                workspace="labs",
            )
        )
        assert done["counts"]["opportunities"]["create"] == 4
        assert {a["channel"] for a in fake.audit if a["target_table"] == "crm_companies"} == {"mcp"}

    def test_the_run_needs_a_run_id(self, crm):
        with pytest.raises(tools.ToolError, match="needs the dry run's run_id"):
            asyncio.run(tools.crm_import(Ctx(OWNER["id"]), "{}", "twenty_json", mode="run"))

    def test_a_member_is_refused(self, crm):
        with pytest.raises(tools.ToolError, match="403"):
            asyncio.run(
                tools.crm_import(Ctx(MEMBER["id"]), LABS_JSON, "twenty_json", workspace="labs")
            )

    def test_the_description_says_dry_run_first(self):
        doc = tools.crm_import.__doc__ or ""
        assert "Always dry-run first" in doc and "4 MB" in doc


class TestBatches:
    """An export's ids go into PostgREST URLs: never so many at once that the
    request line is refused as too long (414)."""

    def test_values_are_split_to_fit_a_request_line(self):
        from app.services.crm import store

        values = [f"{n:04d}-" + "x" * 95 for n in range(500)]
        batches = store.text_batches(values + values[:10])
        assert sorted(v for b in batches for v in b) == sorted(values)
        assert all(sum(len(v) + 1 for v in b) <= 2000 for b in batches)
