"""Prompt isolation — spec 009 NFR-CRMAI-02; FR-CRMAI-MEET-02, -03; Q-26;
`007` FR-CRM-WS-02; spec §15 "No workspace B data in A's prompts or output".

Workspace B (Academy) holds a twin of workspace A's (Labs') client: a
company with the same name and domain, a person with the same name and
email, a deal, a member of B's only, and B's own summary, meeting note,
enrichment and outbound email, each carrying a unique marker. Every AI job
runs for A (summaries, meetings, enrichment, AI fields, follow-up drafts),
with B switched on too, and `FakeClaude` records each call's prompt and
workspace. Nothing of B, no marker, record id or name, reaches A's prompts,
nor any row A's run writes (its items, notes, summaries, values, links, job
runs and audit), and B's prompts carry nothing of A's. A meeting attended by
the email both workspaces hold is processed in neither (Q-26).

Spec 010 R1 (tasks T448; FR-CAP-ACAD-02, -04, -08; plan R1.4 "Isolation
argument", PD-42): email capture's routing of a course enquiry from Labs to
the Academy goes one way. The Labs candidate, its job run and its report
hold no Academy id and read the same whether the Academy made an item or
skipped the enquiry for its own ignore list; the usage row is Labs'.

Every job runs on `tests/crm_fake.py` with the fakes of
`tests/crm_ai_fakes.py`: no network.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from crm_ai_fakes import FakeClaude
from crm_fake import DEFAULTS, OUTSIDER, OWNER, as_
from test_crm_ai_fields_job import ANSWERS as FIELD_ANSWERS
from test_crm_ai_fields_job import Fields
from test_crm_enrichment_job import ANSWER as ENRICHED
from test_crm_enrichment_job import Sites
from test_crm_followup_rules import DRAFT, ON, Rules
from test_crm_followup_rules import NOW as RULES_NOW
from test_crm_meetings_job import ANSWER as MEETING_ANSWER
from test_crm_meetings_job import PRIYA, Calls
from test_crm_summaries_job import ANSWER, Mail

from app.config import settings as config
from app.domain.crm import flags as flag_rules
from app.services.crm import capture, followups
from app.tasks import crm_ai

API = "/api/v1/crm"
ACADEMY = {"workspace": "academy"}
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
DOMAIN = "acme.in"

B_PERSON = "ZQXBPERSON"
B_CITY = "ZQXBCITY"
B_DEAL = "ZQXBDEAL"
B_EMAIL = "ZQXBEMAILTEXT"
B_SUMMARY = "ZQXBSUMMARY"
B_TRANSCRIPT = "ZQXBTRANSCRIPT"
B_NOTE = "ZQXBNOTE"
B_INDUSTRY = "ZQXBINDUSTRY"
B_ENRICHED = "ZQXBENRICHED"
B_DRAFT = "ZQXBDRAFT"
#: B's only member, on B's team roster and nowhere in A.
B_TEAM = OUTSIDER["display_name"]
B_MARKERS = (
    B_PERSON,
    B_CITY,
    B_DEAL,
    B_EMAIL,
    B_SUMMARY,
    B_TRANSCRIPT,
    B_NOTE,
    B_INDUSTRY,
    B_ENRICHED,
    B_DRAFT,
    B_TEAM,
)


class ByWorkspace(FakeClaude):
    """`claude.messages`, answered by a fake per workspace key; every call,
    with its workspace, recorded here too."""

    def __init__(self, **by_key: FakeClaude) -> None:
        super().__init__()
        self.by_key = by_key

    def messages(self, *, model, system, user, schema, job, workspace):
        self.calls.append(
            {
                "model": model,
                "system": system,
                "user": user,
                "schema": schema,
                "job": job,
                "workspace": workspace,
            }
        )
        return self.by_key[workspace].messages(
            model=model, system=system, user=user, schema=schema, job=job, workspace=workspace
        )


def _prompts(claude: FakeClaude, key: str) -> str:
    return "\n".join(f"{c['system']}\n{c['user']}" for c in claude.calls if c["workspace"] == key)


def _written(fake, ws: dict) -> str:
    """Every row of one workspace, in every table, and its audit entries."""
    rows = [
        r
        for table in fake.rows.values()
        for r in table.values()
        if r.get("workspace_id") == ws["id"]
    ]
    audit = [a for a in fake.audit if a.get("workspace_id") == ws["id"]]
    return json.dumps([rows, audit], default=str)


def _post(made, plural: str, body: dict, params: dict = ACADEMY) -> dict:
    r = made.http.post(f"{API}/{plural}", params=params, json=body, headers=as_(OWNER["id"]))
    assert r.status_code == 201, r.text
    return r.json()


def _twin(made, *, email: str | None = PRIYA, website: bool = False) -> dict[str, Any]:
    """B's twin of A's client: Acme on the same domain, Priya (with A's
    Priya's email unless `email` says otherwise), a deal, and Vinita, a
    member of B only, owning them."""
    fake, academy = made.fake, made.academy
    fake.add_deal_lists(academy)
    fake.add_member(academy, OUTSIDER["id"], "member")
    body = {"name": "Acme", "domain": DOMAIN, "city": B_CITY, "owner_id": OUTSIDER["id"]}
    if website:
        body["website"] = {"primary": {"url": f"https://{DOMAIN}"}}
    company = _post(made, "companies", body)
    r = made.http.patch(
        f"{API}/companies/{company['id']}",
        params=ACADEMY,
        json={"lead_stage": "qualified"},
        headers=as_(OWNER["id"]),
    )
    assert r.status_code == 200, r.text
    person = {"first_name": "Priya", "last_name": B_PERSON, "company_id": company["id"]}
    if email:
        person["primary_email"] = email
    person = _post(made, "people", person)
    deal = _post(
        made,
        "opportunities",
        {
            "name": f"{B_DEAL} pilot",
            "company_id": company["id"],
            "owner_id": OUTSIDER["id"],
            "next_step": f"Call about {B_DEAL}",
            "next_step_on": "2026-10-21",
        },
    )
    return {"company": company, "person": person, "deal": deal}


def _ids(fake, ws: dict) -> set[str]:
    """The ids of one workspace's records, notes, summaries and items."""
    tables = (
        "crm_companies",
        "crm_people",
        "crm_opportunities",
        "crm_notes",
        "crm_email_summaries",
        "crm_email_messages",
        "crm_mailboxes",
        "crm_review_items",
        "crm_company_enrichment",
        "crm_ai_field_values",
    )
    return {r["id"] for t in tables for r in fake.rows[t].values() if r["workspace_id"] == ws["id"]}


def _isolated(made, claude: FakeClaude) -> None:
    """Nothing of B in A's prompts or in any row of A's."""
    prompts = _prompts(claude, "labs")
    assert prompts, "A made no model call"
    written = _written(made.fake, made.labs)
    for marker in B_MARKERS:
        assert marker not in prompts, marker
        assert marker not in written, marker
    for record_id in _ids(made.fake, made.academy):
        assert record_id not in prompts, record_id
        assert record_id not in written, record_id


def _targets(made, table: str, column: str = "record_id") -> set[str]:
    return {
        r[column] for r in made.fake.rows[table].values() if r["workspace_id"] == made.labs["id"]
    }


def _a_records(made) -> set[str]:
    return {
        r["id"]
        for t in ("crm_companies", "crm_people", "crm_opportunities", "crm_notes")
        for r in made.fake.rows[t].values()
        if r["workspace_id"] == made.labs["id"]
    }


def _mailbox(made, email: str = OUTSIDER["email"]) -> dict:
    return made.fake.add(
        "crm_mailboxes",
        workspace_id=made.academy["id"],
        email=email,
        owner_id=OUTSIDER["id"],
        labels=["Clients"],
        is_active=True,
        backfilled_at="2026-01-01T00:00:00+00:00",
    )


def _setting(made, key: str, value: dict) -> None:
    made.fake.add("crm_workspace_settings", workspace_id=made.academy["id"], key=key, value=value)


# ---------------------------------------------------------------------------
# Summaries — FR-CRMAI-SUM-01 to -05
# ---------------------------------------------------------------------------

B_SUMMARY_ANSWER = {
    **ANSWER,
    "summary": f"{B_SUMMARY} asked for a quote. Vinita agreed to send it.",
    "asks": [f"A {B_SUMMARY} quote"],
    "commitments": [],
    "next_step": f"Send the {B_SUMMARY} quote",
}


@pytest.fixture
def mail(monkeypatch) -> Mail:
    made = Mail(monkeypatch)
    r = made.http.patch(
        f"{API}/companies/{made.acme['id']}",
        params={"workspace": "labs"},
        json={"domain": DOMAIN},
        headers=as_(OWNER["id"]),
    )
    assert r.status_code == 200, r.text
    made.twin = _twin(made)
    made.fake.set_ai(made.academy, budget_usd=10, phases={"1": True})
    _setting(made, "email_sync", {"enabled": True})
    made.b_mailbox = _mailbox(made)
    made.message("g1")
    # The same thread id in B's mailbox; its own Gmail id.
    made.gmail.texts["b1"] = f"{B_EMAIL}: please send the {B_DEAL} quote."
    made.fake.store_emails(
        made.academy["id"],
        made.b_mailbox["id"],
        [
            {
                "gmail_message_id": "b1",
                "thread_id": "t1",
                "subject": "Pilot scope",
                "sent_at": "2026-10-16T09:30:00+00:00",
                "direction": "inbound",
                "from_email": PRIYA,
                "participants": [{"email": PRIYA, "name": "Priya", "role": "from"}],
                "is_internal": False,
                "is_auto": False,
                "links": [
                    {"record_type": kind, "record_id": made.twin[key]["id"]}
                    for kind, key in (
                        ("company", "company"),
                        ("person", "person"),
                        ("opportunity", "deal"),
                    )
                ],
            }
        ],
    )
    return made


class TestSummaries:
    def test_isolation_no_b_record_reaches_as_summary_prompt_or_rows(self, mail):
        claude = ByWorkspace(labs=FakeClaude(ANSWER), academy=FakeClaude(B_SUMMARY_ANSWER)).install(
            mail.monkeypatch
        )
        runs = crm_ai._summaries(NOW)["runs"]
        assert runs["labs"]["counts"]["summarised"] == 1, runs
        assert runs["academy"]["counts"]["summarised"] == 1, runs
        assert B_EMAIL in _prompts(claude, "academy")
        _isolated(mail, claude)
        # A's summary is linked, and its items aimed, at A's records only.
        assert _targets(mail, "crm_email_summary_links") <= _a_records(mail)
        assert _targets(mail, "crm_review_items") <= _a_records(mail)
        [summary] = [
            s
            for s in mail.fake.rows["crm_email_summaries"].values()
            if s["workspace_id"] == mail.labs["id"]
        ]
        assert summary["mailbox_id"] == mail.mailbox["id"]

    def test_isolation_bs_prompt_carries_nothing_of_as(self, mail):
        claude = ByWorkspace(labs=FakeClaude(ANSWER), academy=FakeClaude(B_SUMMARY_ANSWER)).install(
            mail.monkeypatch
        )
        crm_ai._summaries(NOW)
        theirs = _prompts(claude, "academy")
        assert "Could you send the pilot plan" not in theirs
        assert "Tamil" not in theirs and "Devansh" not in theirs


# ---------------------------------------------------------------------------
# Meetings — FR-CRMAI-MEET-02 to -06; Q-26
# ---------------------------------------------------------------------------

KUMAR = f"kumar@{DOMAIN}"
B_MEETING_ANSWER = {
    **MEETING_ANSWER,
    "summary": f"Kumar raised {B_NOTE}.",
    "decisions": [f"Hold {B_NOTE}"],
    "action_items": [],
    "next_step": f"Answer {B_NOTE}",
}


@pytest.fixture
def calls(monkeypatch) -> Calls:
    made = Calls(monkeypatch)
    made.fake.set_ai(made.academy, budget_usd=10, phases={"2": True})
    return made


class TestMeetings:
    def test_isolation_a_meeting_both_workspaces_match_is_processed_in_neither(self, calls):
        calls.twin = _twin(calls, email=PRIYA)
        calls.fireflies.meeting("t1", [PRIYA], sentences=[("Priya", f"About {B_TRANSCRIPT}.")])
        claude = calls.answer()
        runs = calls.tick()
        for key in ("labs", "academy"):
            assert runs[key]["counts"]["skipped_multi_workspace"] == 1, runs
            assert runs[key]["counts"]["notes"] == 0, runs
        assert calls.fireflies.fetched == []
        assert claude.calls == []
        assert calls.notes() == [] and calls.notes(calls.academy) == []
        assert calls.items() == [] and calls.items(workspace=calls.academy) == []

    def test_isolation_the_same_domain_brings_nothing_of_b_into_as_note(self, calls):
        calls.twin = _twin(calls, email=None)
        kumar = _post(
            calls,
            "people",
            {
                "first_name": "Kumar",
                "last_name": B_PERSON,
                "company_id": calls.twin["company"]["id"],
                "primary_email": KUMAR,
            },
        )
        calls.fireflies.meeting("t1", [PRIYA])
        calls.fireflies.meeting(
            "t2",
            [KUMAR],
            date="2026-10-18T10:30:00+00:00",
            sentences=[("Kumar", f"Our code is {B_TRANSCRIPT}.")],
        )
        claude = ByWorkspace(
            labs=FakeClaude(MEETING_ANSWER), academy=FakeClaude(B_MEETING_ANSWER)
        ).install(calls.monkeypatch)
        runs = calls.tick()
        assert runs["labs"]["counts"]["notes"] == 1, runs
        assert runs["academy"]["counts"]["notes"] == 1, runs
        assert B_TRANSCRIPT in _prompts(claude, "academy")
        _isolated(calls, claude)
        assert "Kumar" not in _prompts(claude, "labs")
        assert kumar["id"] not in _written(calls.fake, calls.labs)
        assert _targets(calls, "crm_note_links") <= _a_records(calls)
        assert _targets(calls, "crm_review_items") <= _a_records(calls)
        assert "Could you send the pilot plan" not in _prompts(claude, "academy")


# ---------------------------------------------------------------------------
# Enrichment — FR-CRMAI-ENR-02 to -05
# ---------------------------------------------------------------------------

B_ENRICHED_ANSWER = {
    **ENRICHED,
    "industry": B_INDUSTRY,
    "description": f"{B_ENRICHED} makes tools.",
}


@pytest.fixture
def sites(monkeypatch) -> Sites:
    made = Sites(monkeypatch)
    made.fake.set_ai(made.academy, budget_usd=10, phases={"2": True})
    made.fake.add(
        "crm_select_options",
        workspace_id=made.academy["id"],
        list_key="company.industry",
        system_key=None,
        label=B_INDUSTRY,
        color=None,
        position=1,
        is_default=False,
        is_active=True,
        is_fixed=False,
    )
    made.acme = made.company("Acme", f"https://{DOMAIN}", domain=DOMAIN)
    made.twin = _twin(made, website=True)
    made.site(DOMAIN)
    return made


class TestEnrichment:
    def test_isolation_the_same_site_enriches_each_workspace_from_its_own_record(self, sites):
        claude = ByWorkspace(
            labs=FakeClaude(ENRICHED), academy=FakeClaude(B_ENRICHED_ANSWER)
        ).install(sites.monkeypatch)
        sites.run()
        assert sites.last_run()["counts"]["enriched"] == 1
        assert sites.last_run(sites.academy)["counts"]["enriched"] == 1
        assert B_INDUSTRY in _prompts(claude, "academy")
        _isolated(sites, claude)
        assert _targets(sites, "crm_company_enrichment", "company_id") == {sites.acme["id"]}
        assert sites.row(sites.acme)["values"]["description"] == ENRICHED["description"]
        assert sites.row(sites.twin["company"])["workspace_id"] == sites.academy["id"]
        assert _targets(sites, "crm_review_items") <= _a_records(sites)
        # A's own options never reach B's prompt.
        assert "Education" not in _prompts(claude, "academy")


# ---------------------------------------------------------------------------
# AI fields — FR-CRMAI-FIELD-03, -04
# ---------------------------------------------------------------------------


@pytest.fixture
def fields(monkeypatch) -> Fields:
    made = Fields(monkeypatch)
    made.patch("companies", made.acme, {"domain": DOMAIN})
    made.fake.set_ai(made.academy, budget_usd=10, phases={"3": True})
    made.twin = twin = _twin(made)
    links = [
        {"record_type": "company", "record_id": twin["company"]["id"]},
        {"record_type": "opportunity", "record_id": twin["deal"]["id"]},
    ]
    _post(
        made,
        "notes",
        {
            "body": f"Met about {B_NOTE}.",
            "kind": "meeting",
            "occurred_on": "2026-10-15",
            "links": links,
        },
    )
    mailbox = _mailbox(made)
    summary = made.fake.add(
        "crm_email_summaries",
        workspace_id=made.academy["id"],
        mailbox_id=mailbox["id"],
        thread_id="t1",
        summary=f"{B_SUMMARY} asked for a quote.",
        asks=[f"A {B_SUMMARY} quote"],
        commitments=[],
        next_step=None,
        next_step_on=None,
        sentiment="neutral",
        model="claude-sonnet-5-5",
        generated_at="2026-10-17T06:00:00+00:00",
        created_at="2026-10-17T06:00:00+00:00",
    )
    for link in links:
        made.fake.add(
            "crm_email_summary_links",
            workspace_id=made.academy["id"],
            summary_id=summary["id"],
            **link,
        )
    made.fake.add_enrichment(
        made.academy, twin["company"], values={"description": f"{B_ENRICHED} makes tools."}
    )
    made.fake.add_enrichment(
        made.labs, made.acme, values={"description": "Acme makes precision machine tools."}
    )
    return made


class TestAiFields:
    def test_isolation_no_b_summary_note_or_enrichment_reaches_as_fields(self, fields):
        claude = fields.answer(FIELD_ANSWERS)
        runs = crm_ai._ai_fields(NOW)["runs"]
        assert runs["labs"]["counts"]["computed"] == 4, runs
        assert runs["academy"]["counts"]["computed"] >= 1, runs
        theirs = _prompts(claude, "academy")
        for marker in (B_NOTE, B_SUMMARY, B_ENRICHED):
            assert marker in theirs, marker
        assert "Acme makes precision machine tools." in _prompts(claude, "labs")
        _isolated(fields, claude)
        assert _targets(fields, "crm_ai_field_values") <= _a_records(fields)
        assert "Acme makes precision machine tools." not in theirs


# ---------------------------------------------------------------------------
# Follow-up drafts — FR-CRMAI-RULE-02
# ---------------------------------------------------------------------------

B_DRAFT_ANSWER = {"draft": f"Hi Priya, about the {B_DRAFT} quote."}


def _ago(days: float) -> str:
    return (RULES_NOW - timedelta(days=days)).isoformat()


@pytest.fixture
def rules(monkeypatch) -> Rules:
    made = Rules(monkeypatch)
    made.fake.rows["crm_people"][made.priya["id"]]["primary_email"] = PRIYA
    made.fake.add_campaign_lists(made.academy)
    made.fake.set_ai(made.academy, budget_usd=10, phases={"1": True, "3": True})
    _setting(made, "automations", dict(ON))
    _setting(made, "flag_thresholds", dict(flag_rules.DEFAULT_THRESHOLDS))
    _setting(made, "email_sync", {"enabled": True})
    monkeypatch.setattr(config, "CRM_GMAIL_CONFIGURED", True)
    made.twin = twin = _twin(made)
    mailbox = _mailbox(made)
    made.gmail.texts["b1"] = f"{B_EMAIL} the quote."
    made.fake.store_emails(
        made.academy["id"],
        mailbox["id"],
        [
            {
                "gmail_message_id": "b1",
                "thread_id": "t1",
                "subject": "Pilot scope",
                "sent_at": _ago(6),
                "direction": "outbound",
                "from_email": OUTSIDER["email"],
                "participants": [],
                "is_internal": False,
                "is_auto": False,
                "links": [
                    {"record_type": "company", "record_id": twin["company"]["id"]},
                    {"record_type": "person", "record_id": twin["person"]["id"]},
                ],
            }
        ],
    )
    made.fake.add(
        "crm_email_summaries",
        **{
            **DEFAULTS.get("crm_email_summaries", {}),
            "workspace_id": made.academy["id"],
            "mailbox_id": mailbox["id"],
            "thread_id": "t1",
            "summary": f"Vinita sent the {B_SUMMARY} quote.",
            "asks": [],
            "commitments": [],
            "next_step": None,
            "next_step_on": None,
            "sentiment": "neutral",
            "model": "claude-sonnet-5-5",
            "generated_at": _ago(5),
            "created_at": _ago(5),
        },
    )
    made.message("g1")
    made.summary()
    return made


class TestFollowUpDrafts:
    def test_isolation_as_draft_carries_nothing_of_bs_twin(self, rules):
        rules.automations()
        queued = rules.celery.named("crm_ai.followup_draft")
        by_ws = {q["args"][0]["workspace_id"]: q["args"][0] for q in queued}
        assert set(by_ws) == {rules.labs["id"], rules.academy["id"]}
        assert {m["mailbox_id"] for m in by_ws[rules.labs["id"]]["messages"]} == {
            rules.mailbox["id"]
        }
        claude = ByWorkspace(labs=FakeClaude(DRAFT), academy=FakeClaude(B_DRAFT_ANSWER)).install(
            rules.monkeypatch
        )
        rules.celery.sent.clear()
        for args in by_ws.values():
            assert followups.drafts(args, RULES_NOW)["counts"]["drafted"] == 1
        assert B_SUMMARY in _prompts(claude, "academy")
        _isolated(rules, claude)
        [item] = rules.items("R-1")
        assert (item["record_type"], item["record_id"]) == ("person", rules.priya["id"])
        assert _targets(rules, "crm_review_items") <= _a_records(rules)
        assert "Deepika sent the pilot plan" not in _prompts(claude, "academy")


# ---------------------------------------------------------------------------
# Spec 010 R1 I3 — a course enquiry routed to the Academy (tasks T448)
# ---------------------------------------------------------------------------


def test_isolation_academy_routing_stays_one_way(monkeypatch):
    """Two course enquiries judged live in Labs (test_crm_capture_job.py):
    the Academy takes one and skips the other for its own ignore list."""
    from test_crm_capture_job import COURSE, STUDENT, Capture, _Ctx

    cap = Capture(monkeypatch)
    cap.fake.rows["crm_capture_dry_runs"][cap.run["id"]].update(
        finished_at="2026-10-19T00:00:00+00:00", applied_at="2026-10-19T01:00:00+00:00"
    )
    cap.fake.set_capture(cap.labs, "live")
    cap.fake.add(
        "crm_capture_ignores",
        workspace_id=cap.academy["id"],
        kind="domain",
        value="school.example.edu",
        from_item_id=None,
        created_by=OWNER["id"],
        created_at="2026-10-01T00:00:00+00:00",
    )
    cap.conversation("c1", sender=STUDENT, at=datetime(2026, 10, 18, 6, 0, tzinfo=UTC))
    cap.conversation(
        "c2", sender="Ravi <ravi@school.example.edu>", at=datetime(2026, 10, 17, 6, 0, tzinfo=UTC)
    )
    cap.answer(
        COURSE,
        {
            **COURSE,
            "company_name": "City School",
            "person_name": "Ravi",
            "person_email": "ravi@school.example.edu",
        },
    )
    got = cap.tick()
    assert got["outcome"] == "ok", got
    # Both handed over (PD-42); the Academy made an item for one only.
    assert got["counts"]["academy_routed"] == 2 and got["counts"]["items"] == 0
    [item] = [
        i
        for i in cap.fake.rows["crm_review_items"].values()
        if i["workspace_id"] == cap.academy["id"]
    ]
    assert item["source_link"]["threads"][0]["thread_id"] == "c1"
    academy_ids = {cap.academy["id"]} | {
        r["id"]
        for table in cap.fake.rows.values()
        for r in table.values()
        if r.get("workspace_id") == cap.academy["id"]
    }
    labs_rows = _written(cap.fake, cap.labs)
    report = json.dumps(capture.overview(_Ctx(cap)), default=str)
    for academy_id in academy_ids:
        assert academy_id not in labs_rows
        assert academy_id not in report
        assert academy_id not in json.dumps(got["counts"])
    # Labs cannot tell what the Academy did: the two candidates differ only
    # in what came from Gmail.
    gmail_values = (
        "company_name",
        "person_name",
        "person_email",
        "domain",
        "reason",
        "first_at",
        "latest_at",
    )
    taken, skipped = (
        {
            **{k: cap.candidate(t)[k] for k in ("status", "outcome", "confidence", "attempts")},
            "finding": {
                k: v for k, v in cap.candidate(t)["finding"].items() if k not in gmail_values
            },
        }
        for t in ("c1", "c2")
    )
    assert taken == skipped
    assert taken["finding"]["routed"] is True and taken["finding"]["business"] == "academy"
    assert taken["finding"]["duplicate_company_id"] is None
    # FR-CAP-ACAD-08: both calls are Labs', paid by Labs.
    assert {u["workspace_id"] for u in cap.fake.rows["crm_ai_usage"].values()} == {cap.labs["id"]}
