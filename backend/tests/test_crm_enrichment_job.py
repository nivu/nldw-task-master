"""The enrichment job — spec 009 FR-CRMAI-ENR-01 to -08, FR-CRMAI-JOB-01,
FR-CRMAI-FLAG-03; NFR-CRMAI-06; Q-32, Q-33; spec §15 "Meetings and
enrichment":

- an import does no enrichment work in its request: the companies are only
  queued, and enriched in the background;
- a company created by email sync is queued;
- every value is marked AI and no person-entered field is written: the
  company's row, its `updated_at` and its audit rows stay as they were;
- an unreadable site, or one resolving to a private address, is failed
  "Website did not respond"; nothing usable, "No company information found";
- the same company and site are not read twice; a website change queues it
  again (Q-32); two companies sharing a site are each read (Q-33);
- at most 10 companies a batch, a minute apart (≤ 10 a minute);
- resumable: a run stopped by the budget or the switch leaves the rest
  queued, and the next tick continues without reading a done company again;
- a field already filled with another value gets an `enrichment_conflict`
  item, not a write;
- the website fetch logs one structured line per page, with no content.

Every job runs on `tests/crm_fake.py` with `FakeClaude` and `FakeWeb`
(`tests/crm_ai_fakes.py`): no network. The batch queue and the run lock are
in memory; each test drains the queue itself.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from crm_ai_fakes import INVALID, PUBLIC, FakeClaude, FakeWeb
from crm_fake import MEMBER, OWNER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.config import settings as config
from app.domain.crm import enrichment as rules
from app.services.crm import enrichment, jobs, notifications, web_fetch
from app.tasks import crm_ai

API = "/api/v1/crm"
LABS = {"workspace": "labs"}
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
HOME = (
    "<html><head><title>Acme Tools</title></head><body>"
    "<p>Acme makes precision machine tools in Coimbatore, India.</p>"
    '<a href="/about">About</a>'
    '<a href="https://www.linkedin.com/company/acme-tools/">LinkedIn</a>'
    "</body></html>"
)
ABOUT = "<html><body><p>About Acme: 120 people since 1998.</p></body></html>"
#: An answer as the model gives it.
ANSWER = {
    "industry": "Manufacturing",
    "sub_industry": "Machine tools",
    "employee_band": "51-200",
    "city": "Coimbatore",
    "country": "India",
    "description": "Acme makes precision machine tools.",
    "linkedin": "https://www.linkedin.com/company/acme-tools/",
    "products": ["Lathes", "CNC mills"],
}
NOTHING = {k: None for k in rules.VALUE_KEYS if k != "products"} | {"products": []}


class Sites:
    """Labs, phase 2 on with a budget, with industry and size options; the
    web, Claude, the batch queue and the run lock faked."""

    def __init__(self, monkeypatch) -> None:
        self.monkeypatch = monkeypatch
        self.fake = fake = FakeCrmStore().install(monkeypatch)
        self.labs, self.academy = two_workspaces(fake)
        fake.add_deal_lists(self.labs)
        fake.set_ai(self.labs, budget_usd=10, phases={"2": True})
        for pos, (list_key, label) in enumerate(
            [
                ("company.industry", "Manufacturing"),
                ("company.industry", "Education"),
                *[("company.size", s) for s in ("1-10", "11-50", "51-200", "201-1000", "1000+")],
            ],
            start=1,
        ):
            fake.add(
                "crm_select_options",
                workspace_id=self.labs["id"],
                list_key=list_key,
                system_key=None,
                label=label,
                color=None,
                position=pos,
                is_default=False,
                is_active=True,
                is_fixed=False,
            )
        monkeypatch.setattr(config, "CRM_AI_INPUT_USD_PER_MTOK", Decimal("3"))
        monkeypatch.setattr(config, "CRM_AI_OUTPUT_USD_PER_MTOK", Decimal("15"))
        monkeypatch.setattr(jobs, "_now", lambda: NOW)
        monkeypatch.setattr(enrichment, "_now", lambda: NOW)
        monkeypatch.setattr(notifications, "deliver", lambda message: ["email"])
        self.http = crm_client(monkeypatch, fake=fake)
        self.web = FakeWeb().install(monkeypatch)
        self.claude = FakeClaude(default=ANSWER).install(monkeypatch)
        self.queue: list[tuple[dict, int]] = []
        self.locks: dict[str, str] = {}
        monkeypatch.setattr(
            enrichment, "enqueue", lambda args, countdown: self.queue.append((args, countdown))
        )
        monkeypatch.setattr(enrichment, "acquire_lock", self._acquire)
        monkeypatch.setattr(enrichment, "extend_lock", lambda ws, token: None)
        monkeypatch.setattr(enrichment, "release_lock", self._release)

    def _acquire(self, ws: str, token: str) -> bool:
        if ws in self.locks:
            return False
        self.locks[ws] = token
        return True

    def _release(self, ws: str, token: str) -> None:
        if self.locks.get(ws) == token:
            del self.locks[ws]

    def answer(self, *answers, default=None) -> FakeClaude:
        self.claude = FakeClaude(*answers, default=default).install(self.monkeypatch)
        return self.claude

    def company(self, name, url=None, *, who=OWNER, params=LABS, **values) -> dict:
        body = {"name": name, **values}
        if url:
            body["website"] = {"primary": {"url": url}}
        r = self.http.post(f"{API}/companies", params=params, json=body, headers=as_(who["id"]))
        assert r.status_code == 201, r.text
        return r.json()

    def patch(self, company, body, who=OWNER) -> dict:
        r = self.http.patch(
            f"{API}/companies/{company['id']}", params=LABS, json=body, headers=as_(who["id"])
        )
        assert r.status_code == 200, r.text
        return r.json()

    def site(self, host="acme.in", home=HOME, about=ABOUT, address=PUBLIC) -> None:
        self.web.site(host, {"/": home, "/about": about}, address=address)

    def tick(self) -> dict:
        return crm_ai._enrichment(NOW)["runs"]

    def drain(self) -> list[dict]:
        done = []
        for _ in range(100):
            if not self.queue:
                return done
            args, _countdown = self.queue.pop(0)
            done.append(crm_ai.enrichment_batch(args))
        raise AssertionError("the batches never ended")

    def run(self) -> dict:
        runs = self.tick()
        self.drain()
        return runs

    def row(self, company) -> dict | None:
        return next(
            (
                e
                for e in self.fake.rows["crm_company_enrichment"].values()
                if e["company_id"] == company["id"]
            ),
            None,
        )

    def last_run(self, ws=None) -> dict:
        ws = ws or self.labs
        runs = [
            r
            for r in self.fake.rows["crm_job_runs"].values()
            if r["job"] == "enrichment" and r["workspace_id"] == ws["id"]
        ]
        return runs[-1]

    def get(self, company, who=OWNER) -> dict:
        r = self.http.get(f"{API}/companies/{company['id']}", params=LABS, headers=as_(who["id"]))
        assert r.status_code == 200, r.text
        return r.json()

    def items(self) -> list[dict]:
        return [
            r
            for r in self.fake.rows["crm_review_items"].values()
            if r["type"] == "enrichment_conflict"
        ]


@pytest.fixture
def sites(monkeypatch) -> Sites:
    return Sites(monkeypatch)


class TestQueuedNotRead:
    """FR-CRMAI-ENR-01, -07 — creating or importing a company only queues it."""

    def test_enrichment_creating_a_company_queues_it_and_reads_nothing(self, sites):
        acme = sites.company("Acme", "https://www.acme.in/home")
        row = sites.row(acme)
        assert (row["status"], row["website"]) == ("queued", "https://www.acme.in/")
        assert sites.web.requests == [] and sites.claude.calls == [] and sites.queue == []

    def test_enrichment_a_domain_alone_queues_its_site(self, sites):
        acme = sites.company("Acme", domain="acme.in")
        assert sites.row(acme)["website"] == "https://acme.in/"

    def test_enrichment_neither_website_nor_domain_is_not_queued(self, sites):
        assert sites.row(sites.company("Acme")) is None

    def test_enrichment_an_import_does_no_enrichment_work_in_the_request(self, sites):
        content = "Name,Website\nAcme,https://acme.in\nBeta,https://beta.in\n"
        body = {"format": "csv", "content": content, "object": "companies"}
        dry = sites.http.post(
            f"{API}/imports/dry-run", params=LABS, json=body, headers=as_(OWNER["id"])
        )
        assert dry.status_code == 200, dry.text
        applied = sites.http.post(
            f"{API}/imports/{dry.json()['run_id']}/apply",
            params=LABS,
            json=body,
            headers=as_(OWNER["id"]),
        )
        assert applied.status_code == 200, applied.text
        rows = list(sites.fake.rows["crm_company_enrichment"].values())
        assert sorted((r["website"], r["status"]) for r in rows) == [
            ("https://acme.in/", "queued"),
            ("https://beta.in/", "queued"),
        ]
        assert sites.web.requests == [] and sites.web.lookups == []
        assert sites.claude.calls == [] and sites.queue == []

    def test_enrichment_a_company_created_by_email_sync_is_queued(self, sites):
        # Automation 2's ops (services/crm/email_sync._create): a company and
        # its primary domain, as the `sync` actor.
        sync = {"user_id": None, "kind": "sync", "channel": None, "workspace_id": sites.labs["id"]}
        [company, _] = sites.fake.apply(
            sync,
            [
                {
                    "op": "insert",
                    "table": "crm_companies",
                    "ref": "c",
                    "values": {"name": "Acme", "owner_id": OWNER["id"]},
                },
                {
                    "op": "insert",
                    "table": "crm_company_domains",
                    "values": {"company_id": "$ref:c", "domain": "acme.in", "is_primary": True},
                },
            ],
        )
        assert sites.row(company)["status"] == "queued"


class TestInTheBackground:
    """FR-CRMAI-ENR-02, -03, -07."""

    def test_enrichment_the_tick_starts_a_run_and_the_batch_enriches(self, sites):
        acme = sites.company("Acme", "https://acme.in")
        sites.site()
        runs = sites.tick()
        assert runs["labs"]["outcome"] == "running"
        assert sites.claude.calls == []
        [(args, countdown)] = sites.queue
        assert countdown == 0 and set(args) == {
            "workspace_id",
            "run_id",
            "cutoff",
            "lock",
            "counts",
        }
        sites.drain()
        row = sites.row(acme)
        assert row["status"] == "done" and row["failure_reason"] is None
        assert row["values"] == {
            "industry": "Manufacturing",
            "sub_industry": "Machine tools",
            "employee_band": "51-200",
            "city": "Coimbatore",
            "country": "India",
            "description": "Acme makes precision machine tools.",
            "linkedin": "https://www.linkedin.com/company/acme-tools/",
            "products": ["Lathes", "CNC mills"],
        }
        assert (row["model"], row["generated_at"]) == ("claude-sonnet-5-5", NOW.isoformat())
        [prompt] = sites.claude.prompts
        assert "precision machine tools" in prompt and "120 people" in prompt
        assert "Industry options: Manufacturing; Education" in prompt
        run = sites.last_run()
        assert run["outcome"] == "ok"
        assert run["counts"] == {
            "companies": 1,
            "enriched": 1,
            "failed": 0,
            "items": 0,
            "failed_items": 0,
        }
        assert sites.locks == {}

    def test_enrichment_the_values_are_marked_ai(self, sites):
        acme = sites.company("Acme", "https://acme.in")
        sites.site()
        sites.run()
        shown = sites.get(acme)["enrichment"]
        assert shown["status"] == "done"
        assert shown["ai"] == {
            "source_kind": "enrichment",
            "model": "claude-sonnet-5-5",
            "generated_at": NOW.isoformat(),
            "source": None,
        }
        assert shown["values"]["city"] == "Coimbatore"
        assert shown["promotable"]["industry_id"] is True

    def test_enrichment_links_on_the_companys_own_site_are_kept(self, sites):
        acme = sites.company("Acme", "https://acme.in")
        sites.site()
        description = (
            "See https://acme.in/products and https://www.acme.in/x, not https://evil.example/x."
        )
        sites.answer(ANSWER | {"description": description})
        sites.run()
        assert sites.row(acme)["values"]["description"] == (
            "See https://acme.in/products and https://www.acme.in/x, not ."
        )

    def test_enrichment_no_person_entered_field_is_written(self, sites):
        acme = sites.company("Acme", "https://acme.in", city="Chennai")
        sites.site()
        before = dict(sites.fake.rows["crm_companies"][acme["id"]])
        audit = len(sites.fake.audit)
        sites.run()
        assert sites.fake.rows["crm_companies"][acme["id"]] == before
        assert sites.fake.audit[audit:] == []

    def test_enrichment_a_filled_field_with_another_value_waits_as_a_conflict(self, sites):
        acme = sites.company("Acme", "https://acme.in", city="Chennai", owner_id=MEMBER["id"])
        sites.site()
        sites.run()
        [item] = sites.items()
        assert item["owner_id"] == MEMBER["id"]
        assert item["suggestion"] == {"field": "city", "value": "Coimbatore", "label": "Coimbatore"}
        assert item["current_values"] == {"city": "Chennai"}
        assert sites.last_run()["counts"]["items"] == 1
        shown = sites.get(acme)["enrichment"]
        assert shown["promotable"]["city"] is False
        assert shown["conflicts"] == [item["id"]]

    def test_enrichment_a_second_run_makes_no_second_conflict(self, sites):
        acme = sites.company("Acme", "https://acme.in", city="Chennai")
        sites.site()
        sites.run()
        r = sites.http.post(
            f"{API}/enrichment/run",
            params=LABS,
            json={"company_ids": [acme["id"]]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200, r.text
        sites.run()
        assert len(sites.items()) == 1


class TestFailures:
    """FR-CRMAI-ENR-06, D-2 — a reason from the fixed list, nothing guessed."""

    def test_enrichment_an_unreadable_site_did_not_respond(self, sites):
        acme = sites.company("Acme", "https://acme.in")
        sites.run()
        row = sites.row(acme)
        assert (row["status"], row["failure_reason"], row["values"]) == (
            "failed",
            "Website did not respond",
            {},
        )
        assert sites.claude.calls == []
        assert sites.last_run()["counts"]["failed"] == 1

    def test_enrichment_a_private_address_did_not_respond(self, sites):
        acme = sites.company("Acme", "https://acme.in")
        sites.site(address="10.0.0.8")
        sites.run()
        assert sites.row(acme)["failure_reason"] == "Website did not respond"
        assert sites.web.requests == [] and sites.claude.calls == []

    def test_enrichment_a_page_with_no_text_found_nothing(self, sites):
        acme = sites.company("Acme", "https://acme.in")
        sites.site(home="<html><script>x()</script></html>")
        sites.run()
        assert sites.row(acme)["failure_reason"] == "No company information found"
        assert sites.claude.calls == []

    def test_enrichment_an_answer_with_nothing_found_nothing(self, sites):
        acme = sites.company("Acme", "https://acme.in")
        sites.site()
        sites.answer(NOTHING)
        sites.run()
        assert sites.row(acme)["failure_reason"] == "No company information found"

    def test_enrichment_invalid_output_twice_is_its_reason(self, sites):
        acme = sites.company("Acme", "https://acme.in")
        sites.site()
        sites.answer(INVALID, INVALID)
        sites.run()
        assert sites.row(acme)["failure_reason"] == "Output did not match the schema"

    def test_enrichment_any_other_failure_is_counted_and_waits_for_the_next_run(
        self, sites, monkeypatch
    ):
        acme = sites.company("Acme", "https://acme.in")

        def broken(*args, **kwargs):
            raise RuntimeError("acme.in said something")

        monkeypatch.setattr(web_fetch, "read_site", broken)
        sites.run()
        row = sites.row(acme)
        assert row["status"] == "queued"
        run = sites.last_run()
        assert run["outcome"] == "ok" and run["counts"]["failed_items"] == 1
        # Not retried in the same run; the next run takes it.
        assert datetime.fromisoformat(row["queued_at"]) > NOW


class TestIdempotent:
    """FR-CRMAI-ENR-08, Q-32, Q-33."""

    def test_enrichment_the_same_company_and_site_are_not_read_again(self, sites):
        acme = sites.company("Acme", "https://acme.in")
        sites.site()
        sites.run()
        sites.patch(acme, {"website": {"primary": {"url": "https://acme.in/"}}})
        reads = len(sites.web.requests)
        runs = sites.run()
        assert len(sites.web.requests) == reads and len(sites.claude.calls) == 1
        assert runs["labs"] == {"outcome": "ok", "counts": dict(enrichment.EMPTY)}

    def test_enrichment_a_website_change_queues_it_again(self, sites):
        acme = sites.company("Acme", "https://acme.in")
        sites.site()
        sites.run()
        sites.patch(acme, {"website": {"primary": {"url": "https://acmetools.com"}}})
        row = sites.row(acme)
        assert (row["status"], row["website"], row["values"]) == (
            "queued",
            "https://acmetools.com/",
            {},
        )
        sites.site("acmetools.com")
        sites.run()
        assert sites.row(acme)["status"] == "done"
        assert [r["host"] for r in sites.web.requests][-2:] == ["acmetools.com", "acmetools.com"]

    def test_enrichment_two_companies_sharing_a_site_are_each_read(self, sites):
        one = sites.company("Acme India", "https://acme.in")
        two = sites.company("Acme Exports", "https://acme.in")
        sites.site()
        sites.run()
        assert sites.row(one)["status"] == sites.row(two)["status"] == "done"
        assert len(sites.claude.calls) == 2

    def test_enrichment_an_admin_asking_again_reads_it_again(self, sites):
        acme = sites.company("Acme", "https://acme.in")
        sites.site()
        sites.run()
        r = sites.http.post(
            f"{API}/enrichment/run",
            params=LABS,
            json={"company_ids": [acme["id"]]},
            headers=as_(OWNER["id"]),
        )
        assert r.json() == {"queued": 1, "skipped_no_website": 0, "already_enriched": 1}
        assert sites.row(acme)["requested_by"] == OWNER["id"]
        assert sites.claude.calls and len(sites.claude.calls) == 1
        sites.run()
        assert len(sites.claude.calls) == 2


class TestRateAndResume:
    """FR-CRMAI-ENR-07."""

    def test_enrichment_at_most_ten_a_batch_a_minute_apart(self, sites):
        companies = [sites.company(f"Acme {i}", "https://acme.in") for i in range(12)]
        sites.site()
        sites.tick()
        args, _ = sites.queue.pop(0)
        assert crm_ai.enrichment_batch(args) == {"done": False}
        assert len(sites.claude.calls) == 10
        [(next_args, countdown)] = sites.queue
        assert countdown == 60 and next_args["run_id"] == args["run_id"]
        assert sites.last_run()["outcome"] == "running"
        sites.drain()
        assert len(sites.claude.calls) == 12
        assert all(sites.row(c)["status"] == "done" for c in companies)
        assert sites.last_run()["counts"]["companies"] == 12

    def test_enrichment_a_tick_during_a_run_is_skipped(self, sites):
        sites.company("Acme", "https://acme.in")
        sites.site()
        sites.tick()
        again = sites.tick()
        assert again["labs"] == {
            "outcome": "skipped",
            "counts": {"skipped": "Enrichment is already running"},
        }

    def test_enrichment_the_budget_stops_the_run_and_the_next_tick_continues(self, sites):
        # Each call is estimated at $0.006: the third finds $0.012 spent.
        sites.fake.set_ai(sites.labs, budget_usd=0.01, phases={"2": True})
        companies = [sites.company(f"Acme {i}", "https://acme.in") for i in range(5)]
        sites.site()
        sites.run()
        run = sites.last_run()
        assert run["outcome"] == "skipped"
        assert run["counts"]["skipped"] == "AI budget reached"
        assert [sites.row(c)["status"] for c in companies] == ["done", "done"] + ["queued"] * 3
        assert sites.locks == {}
        sites.fake.set_ai(sites.labs, budget_usd=10, phases={"2": True})
        sites.run()
        assert [sites.row(c)["status"] for c in companies] == ["done"] * 5
        # Five companies, five calls: the refused call was never made, and no
        # done company was read again.
        assert len(sites.claude.calls) == 5
        assert sites.last_run()["counts"]["enriched"] == 3

    def test_enrichment_switching_phase_two_off_stops_the_run(self, sites):
        companies = [sites.company(f"Acme {i}", "https://acme.in") for i in range(12)]
        sites.site()
        sites.tick()
        args, _ = sites.queue.pop(0)
        crm_ai.enrichment_batch(args)
        sites.fake.set_ai(sites.labs, budget_usd=10, phases={"2": False})
        sites.drain()
        run = sites.last_run()
        assert (run["outcome"], run["counts"]["skipped"]) == ("skipped", "Switched off")
        assert [sites.row(c)["status"] for c in companies].count("queued") == 2
        assert sites.locks == {}


class TestSwitchedOff:
    """FR-CRMAI-FLAG-03."""

    def test_enrichment_phase_two_off_skips_and_reads_nothing(self, sites):
        sites.fake.set_ai(sites.labs, budget_usd=10, phases={"2": False})
        acme = sites.company("Acme", "https://acme.in")
        sites.site()
        runs = sites.run()
        assert runs["labs"] == {"outcome": "skipped", "counts": {"skipped": "Switched off"}}
        assert sites.web.requests == [] and sites.row(acme)["status"] == "queued"

    def test_enrichment_a_spent_budget_starts_no_run_and_reads_nothing(self, sites):
        """A workspace past its budget would otherwise read the first queued
        company's site on every tick and then stop at the call."""
        sites.fake.set_ai(sites.labs, budget_usd=0, phases={"2": True})
        acme = sites.company("Acme", "https://acme.in")
        sites.site()
        for _ in range(3):
            runs = sites.run()
            assert runs["labs"] == {
                "outcome": "skipped",
                "counts": {"skipped": "AI budget reached"},
            }
        assert sites.queue == [] and sites.locks == {}
        assert sites.web.requests == [] and sites.claude.calls == []
        assert sites.row(acme)["status"] == "queued"

    def test_enrichment_unset_rates_start_no_run_and_read_nothing(self, sites):
        sites.monkeypatch.setattr(config, "CRM_AI_OUTPUT_USD_PER_MTOK", None)
        acme = sites.company("Acme", "https://acme.in")
        sites.site()
        runs = sites.run()
        assert runs["labs"] == {
            "outcome": "skipped",
            "counts": {"skipped": "AI rates are not set"},
        }
        assert sites.web.requests == [] and sites.row(acme)["status"] == "queued"

    def test_enrichment_the_budget_reached_mid_run_reads_no_further_site(self, sites):
        # Each call is estimated at $0.006: the third company finds $0.012
        # spent and its site is not read.
        sites.fake.set_ai(sites.labs, budget_usd=0.01, phases={"2": True})
        for i in range(3):
            sites.company(f"Acme {i}", f"https://acme{i}.in")
            sites.web.site(f"acme{i}.in", {"/": HOME, "/about": ABOUT}, address=PUBLIC)
        sites.run()
        hosts = {r["host"] for r in sites.web.requests}
        assert hosts == {"acme0.in", "acme1.in"}

    def test_enrichment_hidden_from_the_record_while_off(self, sites):
        acme = sites.company("Acme", "https://acme.in")
        sites.site()
        sites.run()
        sites.fake.set_ai(sites.labs, budget_usd=10, phases={"2": False})
        assert "enrichment" not in sites.get(acme)


class TestLogging:
    """NFR-CRMAI-06 — one structured line per page, no content."""

    def test_enrichment_the_fetch_logs_a_line_per_page_without_content(self, sites, caplog):
        caplog.set_level(logging.INFO)
        sites.company("Acme", "https://acme.in")
        sites.site()
        sites.run()
        lines = [r.getMessage() for r in caplog.records if "crm_web_fetch" in r.getMessage()]
        assert len(lines) == 2
        for line in lines:
            assert '"workspace": "labs"' in line and '"job": "enrichment"' in line
            for text in ("acme", "Coimbatore", "machine tools", PUBLIC):
                assert text not in line, line
