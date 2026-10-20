"""Settings, Email capture — spec 010 contracts §1.2, §2.1, §2.2;
FR-CAP-DIS-01, -10, FR-CAP-RPT-01 to -03; plan §5.7, PD-13.

`GET /crm/capture` and `POST /crm/capture/dry-run` are for workspace Admins
only; `PATCH /crm/settings/capture` takes Off (cancelling an open dry run)
and refuses Dry run (started from Email capture) and, in S2, Live. On the
fake store; no Gmail or Claude is reached from the API.

S3 (tasks T316): contracts §2.3 to §2.5; FR-CAP-DIS-03, FR-CAP-RPT-04 —
applying a dry run (and each refusal), Live accepted once one was applied,
and the ignore list's add and remove, Admin only.

R1 (tasks T426): contracts §7.1; FR-CAP-RPT-08, -10 — the recall's summary,
and a run stored before 074 still read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from crm_fake import DEFAULTS, MEMBER, OUTSIDER, OWNER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.config import settings as config
from app.services.crm import capture

API = "/api/v1/crm"
LABS = {"workspace": "labs"}
NOW = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.add(
        "crm_workspace_settings",
        workspace_id=labs["id"],
        key="email_sync",
        value={"enabled": True},
    )
    monkeypatch.setattr(config, "CRM_GMAIL_CONFIGURED", True)
    monkeypatch.setattr(capture, "_now", lambda: NOW)
    http = crm_client(monkeypatch, fake=fake)
    mailbox = fake.add(
        "crm_mailboxes",
        workspace_id=labs["id"],
        email="owner@nunnarilabs.com",
        owner_id=OWNER["id"],
        labels=["Sales"],
        **{**DEFAULTS["crm_mailboxes"], "capture_source": "primary_sent"},
    )
    return http, fake, labs, mailbox


def _get(http, user=OWNER["id"]):
    return http.get(f"{API}/capture", params=LABS, headers=as_(user))


def _start(http, user=OWNER["id"]):
    return http.post(f"{API}/capture/dry-run", params=LABS, headers=as_(user))


def _mode(http, value, user=OWNER["id"]):
    return http.patch(
        f"{API}/settings/capture", params=LABS, json={"value": value}, headers=as_(user)
    )


def _runs(fake, labs):
    return [
        r for r in fake.rows["crm_capture_dry_runs"].values() if r["workspace_id"] == labs["id"]
    ]


class TestTheOverview:
    """Contracts §2.1."""

    def test_capture_overview_before_any_dry_run(self, crm):
        http, _, _, mailbox = crm
        r = _get(http)
        assert r.status_code == 200, r.text
        assert r.json() == {
            "mode": "off",
            "live_allowed": False,
            "mailboxes": [
                {
                    "id": mailbox["id"],
                    "email": "owner@nunnarilabs.com",
                    "capture_source": "primary_sent",
                    "capture_start_date": "2026-04-01",
                    "backfilled_at": None,
                }
            ],
            "ignores": [],
            "dry_run": None,
        }

    def test_capture_overview_live_allowed_follows_any_applied_run(self, crm):
        # FE-1: an applied run A, then a newer run B never applied; the page
        # still offers Live, as PATCH accepts it.
        http, fake, labs, mailbox = crm
        assert _get(http).json()["live_allowed"] is False
        fake.add_dry_run(
            labs,
            started_at="2026-10-10T05:00:00+00:00",
            finished_at="2026-10-10T09:00:00+00:00",
            applied_at="2026-10-10T10:00:00+00:00",
        )
        fake.add_dry_run(
            labs,
            started_at="2026-10-19T05:00:00+00:00",
            finished_at="2026-10-19T06:00:00+00:00",
            cancelled=True,
        )
        fake.set_capture(labs, "off")
        got = _get(http).json()
        assert got["dry_run"]["applied_at"] is None
        assert got["live_allowed"] is True
        assert _mode(http, {"mode": "live"}).status_code == 200
        # Without a Primary and Sent mailbox, PATCH refuses Live, and so does the page.
        fake.rows["crm_mailboxes"][mailbox["id"]]["capture_source"] = "labels"
        assert _get(http).json()["live_allowed"] is False

    def test_capture_overview_of_a_finished_dry_run(self, crm):
        http, fake, labs, mailbox = crm
        fake.set_capture(labs, "dry_run")
        run = fake.add_dry_run(
            labs,
            started_at="2026-10-19T05:00:00+00:00",
            finished_at="2026-10-19T09:00:00+00:00",
            recall=[
                {
                    "mailbox_id": mailbox["id"],
                    "thread_id": "s1",
                    "latest_at": None,
                    "fate": "found",
                },
                {
                    "mailbox_id": mailbox["id"],
                    "thread_id": "s2",
                    "latest_at": None,
                    "fate": "outside_scope",
                },
            ],
        )
        fake.store_emails(
            labs["id"],
            mailbox["id"],
            [
                {
                    "gmail_message_id": "g1",
                    "thread_id": "s1",
                    "subject": "Re: Proposal",
                    "sent_at": "2026-10-10T09:00:00+00:00",
                    "direction": "inbound",
                    "from_email": "priya@acme.com",
                    "participants": [],
                    "is_internal": False,
                    "is_auto": False,
                    "links": [],
                }
            ],
        )
        for thread, outcome, confidence in (("c1", "new_lead", "high"), ("c2", "new_lead", "low")):
            fake.mark_candidates(
                labs["id"],
                mailbox["id"],
                [{"thread_id": thread, "message_id": f"{thread}-m", "sent_at": NOW.isoformat()}],
            )
            cand = next(
                c for c in fake.rows["crm_capture_candidates"].values() if c["thread_id"] == thread
            )
            fake.record_candidate(
                labs["id"],
                cand["id"],
                f"{thread}-m",
                {
                    "status": "judged",
                    "outcome": outcome,
                    "confidence": confidence,
                    "dry_run": True,
                    "finding": {
                        "company_name": "Acme",
                        "domain": "acme.com",
                        "domain_from_text": False,
                        "person_name": "Priya",
                        "person_email": "priya@acme.com",
                        "first_at": None,
                        "latest_at": None,
                        "message_count": 1,
                        "owner_replied": False,
                        "reason": "Asked for pricing.",
                    },
                },
            )
        fake.insert_ai_usage(
            labs["id"],
            {
                "job": "capture",
                "model": "claude-sonnet-5-5",
                "input_tokens": 1000,
                "output_tokens": 100,
                "cost_usd": "1.234",
                "outcome": "ok",
                "at": "2026-10-19T06:00:00+00:00",
            },
        )
        got = _get(http).json()
        dry = got["dry_run"]
        assert got["mode"] == "dry_run"
        assert (dry["id"], dry["applicable"], dry["cancelled"]) == (run["id"], True, False)
        assert dry["estimated_cost_usd"] == "1.23"
        assert dry["counts"]["considered"] == 2 and dry["counts"]["judged"] == 2
        assert dry["counts"]["by_outcome"]["new_lead"] == {"high": 1, "medium": 0, "low": 1}
        assert dry["counts"]["filed_known_threads"] == 1
        # Every confidence is listed; the page groups low ones apart.
        assert sorted(f["confidence"] for f in dry["findings"]) == ["high", "low"]
        assert {f["company_name"] for f in dry["findings"]} == {"Acme"}
        assert dry["recall"] == {
            "line": "Of the 1 that could be tested, the dry run found 1. 1 could not be "
            "tested: 1 is outside Primary and Sent.",
            "rows": [
                {"thread_id": "s1", "latest_at": None, "subject": "Re: Proposal", "fate": "found"},
                {"thread_id": "s2", "latest_at": None, "subject": None, "fate": "outside_scope"},
            ],
            "summary": {
                "total": 2,
                "tested": 1,
                "found": 1,
                "capped": False,
                "untested": {
                    "known": 0,
                    "ignored": 0,
                    "internal_only": 0,
                    "calendar": 0,
                    "automated": 0,
                    "outside_scope": 1,
                    "not_judged": 0,
                    "skipped_rules": 0,
                },
                "academy_unrouted": 0,
            },
        }

    def test_capture_overview_reads_a_run_before_074(self, crm):
        # FR-CAP-RPT-10: a run stored with the old fates validates (no 500)
        # and keeps `skipped_rules` unsplit; a cap marker is not a row.
        http, fake, labs, mailbox = crm
        old = ("filed_known", "found", "judged_otherwise", "skipped_rules", "known_now")
        fake.add_dry_run(
            labs,
            started_at="2026-10-19T05:00:00+00:00",
            finished_at="2026-10-19T09:00:00+00:00",
            recall=[
                *(
                    {
                        "mailbox_id": mailbox["id"],
                        "thread_id": f"o{n}",
                        "latest_at": None,
                        "fate": f,
                    }
                    for n, f in enumerate(old)
                ),
                {"capped": True},
            ],
        )
        r = _get(http)
        assert r.status_code == 200, r.text
        recall = r.json()["dry_run"]["recall"]
        assert [row["fate"] for row in recall["rows"]] == list(old)
        assert recall["line"] == (
            "Of the 2 that could be tested, the dry run found 1. 3 could not be tested: "
            "2 involve contacts now in the CRM (filed automatically), 1 was skipped by the "
            "rules. Only the first 500 were checked."
        )
        assert recall["summary"]["untested"]["skipped_rules"] == 1
        assert (recall["summary"]["total"], recall["summary"]["capped"]) == (5, True)

    def test_capture_a_report_older_than_seven_days_is_not_applicable(self, crm):
        http, fake, labs, _ = crm
        fake.add_dry_run(
            labs,
            started_at=(NOW - timedelta(days=9)).isoformat(),
            finished_at=(NOW - timedelta(days=8)).isoformat(),
        )
        assert _get(http).json()["dry_run"]["applicable"] is False

    @pytest.mark.parametrize(
        ("user", "status"), [(MEMBER["id"], 403), (VIEWER["id"], 403), (OUTSIDER["id"], 404)]
    )
    def test_capture_admin_only(self, crm, user, status):
        http, *_ = crm
        assert _get(http, user).status_code == status
        assert _start(http, user).status_code == status


class TestStartingADryRun:
    """Contracts §2.2; FR-CAP-DIS-10."""

    def test_capture_start_opens_a_run_and_sets_the_mode(self, crm):
        http, fake, labs, _ = crm
        r = _start(http)
        assert r.status_code == 202, r.text
        [run] = _runs(fake, labs)
        assert r.json() == {"dry_run": {"id": run["id"], "started_at": run["started_at"]}}
        assert run["started_by"] == OWNER["id"]
        assert fake.capture_setting(labs["id"]) == {"mode": "dry_run"}
        assert fake.audit[-1]["action"] == "setting.updated"
        assert fake.audit[-1]["after"] == {"value": {"mode": "dry_run"}}

    def test_capture_start_needs_a_primary_and_sent_mailbox(self, crm):
        http, fake, _, mailbox = crm
        fake.rows["crm_mailboxes"][mailbox["id"]]["capture_source"] = "labels"
        r = _start(http)
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Set a mailbox to Primary and Sent first.",
        )

    def test_capture_start_needs_email_sync(self, crm, monkeypatch):
        http, *_ = crm
        monkeypatch.setattr(config, "CRM_GMAIL_CONFIGURED", False)
        r = _start(http)
        assert (r.status_code, r.json()["detail"]) == (422, "Switch email sync on first.")

    def test_capture_a_second_start_is_refused(self, crm):
        http, fake, labs, _ = crm
        assert _start(http).status_code == 202
        r = _start(http)
        assert (r.status_code, r.json()["detail"]) == (409, "A dry run is already running.")
        assert len(_runs(fake, labs)) == 1

    def test_capture_start_reopens_what_the_last_dry_run_judged(self, crm):
        http, fake, labs, mailbox = crm
        fake.mark_candidates(
            labs["id"],
            mailbox["id"],
            [{"thread_id": "t1", "message_id": "m1", "sent_at": NOW.isoformat()}],
        )
        [cand] = fake.rows["crm_capture_candidates"].values()
        fake.record_candidate(
            labs["id"],
            cand["id"],
            "m1",
            {"status": "judged", "outcome": "not_relevant", "confidence": "high", "dry_run": True},
        )
        assert _start(http).status_code == 202
        assert (cand["status"], cand["outcome"], cand["dry_run"]) == ("pending", None, False)


class TestTheMode:
    """Contracts §1.2, PD-13."""

    @pytest.mark.parametrize(
        ("value", "detail"),
        [
            ({"mode": "dry_run"}, "Start a dry run from Email capture."),
            ({"mode": "live"}, "Run and apply a dry run first."),
            ({"mode": "on"}, "Email capture is off, dry run or live."),
            ({"mode": "off", "x": 1}, "Email capture is off, dry run or live."),
        ],
    )
    def test_capture_mode_refusals(self, crm, value, detail):
        http, fake, labs, _ = crm
        r = _mode(http, value)
        assert (r.status_code, r.json()["detail"]) == (422, detail)
        assert fake.capture_setting(labs["id"]) == {"mode": "off"}

    def test_capture_off_during_a_dry_run_cancels_it_and_a_new_start_succeeds(self, crm):
        http, fake, labs, _ = crm
        run_id = _start(http).json()["dry_run"]["id"]
        r = _mode(http, {"mode": "off"})
        assert r.status_code == 200, r.text
        assert r.json() == {
            "key": "capture",
            "value": {"mode": "off"},
            "cancelled_dry_run_id": run_id,
        }
        run = fake.rows["crm_capture_dry_runs"][run_id]
        assert run["cancelled"] is True and run["finished_at"] is not None
        assert fake.capture_setting(labs["id"]) == {"mode": "off"}
        assert fake.audit[-1]["action"] == "setting.updated"
        assert _start(http).status_code == 202
        assert len(_runs(fake, labs)) == 2

    def test_capture_off_with_no_run_open_cancels_nothing(self, crm):
        http, *_ = crm
        assert _mode(http, {"mode": "off"}).json()["cancelled_dry_run_id"] is None

    def test_capture_the_settings_read_shows_it(self, crm):
        http, *_ = crm
        got = http.get(f"{API}/settings", params=LABS, headers=as_(OWNER["id"])).json()
        assert got["settings"]["capture"] == {"mode": "off"}

    def test_capture_members_cannot_change_it(self, crm):
        http, *_ = crm
        assert _mode(http, {"mode": "off"}, MEMBER["id"]).status_code == 403


class TestTheJobsList:
    """Contracts §1.4 — the job is listed and can be chosen."""

    def test_capture_is_a_job_of_the_log(self, crm):
        http, *_ = crm
        got = http.get(f"{API}/jobs", params={**LABS, "job": "capture"}, headers=as_(OWNER["id"]))
        assert got.status_code == 200, got.text
        assert {"job": "capture", "label": "Email capture", "every_minutes": 60} in got.json()[
            "jobs"
        ]


def _apply(http, run_id, user=OWNER["id"]):
    return http.post(f"{API}/capture/dry-runs/{run_id}/apply", params=LABS, headers=as_(user))


def _finished(fake, labs, **values):
    return fake.add_dry_run(
        labs,
        **{
            "started_at": (NOW - timedelta(days=1, hours=2)).isoformat(),
            "finished_at": (NOW - timedelta(days=1)).isoformat(),
            **values,
        },
    )


class TestApplying:
    """Contracts §2.3; FR-CAP-RPT-04."""

    def test_capture_apply_sets_live(self, crm):
        http, fake, labs, _ = crm
        fake.set_capture(labs, "dry_run")
        run = _finished(fake, labs)
        r = _apply(http, run["id"])
        assert r.status_code == 200, r.text
        assert r.json()["mode"] == "live" and r.json()["items_created"] == 0
        assert fake.capture_setting(labs["id"]) == {"mode": "live"}
        setting = [
            a
            for a in fake.audit
            if a["action"] == "setting.updated" and a["after"] == {"value": {"mode": "live"}}
        ]
        assert [a["actor_id"] for a in setting] == [OWNER["id"]]
        assert _get(http).json()["dry_run"]["applicable"] is False

    @pytest.mark.parametrize(
        ("values", "status", "detail"),
        [
            ({"finished_at": None}, 422, "This dry run has not finished."),
            (
                {"cancelled": True},
                422,
                "This dry run was stopped before it finished; run it again.",
            ),
            (
                {
                    "started_at": (NOW - timedelta(days=9)).isoformat(),
                    "finished_at": (NOW - timedelta(days=8)).isoformat(),
                },
                422,
                "This dry run finished more than 7 days ago; run it again.",
            ),
            ({"applied_at": NOW.isoformat()}, 409, "This dry run was already applied."),
        ],
    )
    def test_capture_apply_refusals(self, crm, values, status, detail):
        http, fake, labs, _ = crm
        run = _finished(fake, labs, **values)
        r = _apply(http, run["id"])
        assert (r.status_code, r.json()["detail"]) == (status, detail)
        assert fake.capture_setting(labs["id"]) == {"mode": "off"}

    def test_capture_only_the_latest_run_applies(self, crm):
        http, fake, labs, _ = crm
        old = _finished(fake, labs)
        _finished(
            fake,
            labs,
            started_at=(NOW - timedelta(hours=3)).isoformat(),
            finished_at=(NOW - timedelta(hours=1)).isoformat(),
        )
        r = _apply(http, old["id"])
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "A newer dry run exists; apply that one.",
        )

    def test_capture_a_run_paused_long_but_finished_recently_applies(self, crm):
        # Data-model §3.7: the window counts from the report's end.
        http, fake, labs, _ = crm
        run = _finished(
            fake,
            labs,
            started_at=(NOW - timedelta(days=10)).isoformat(),
            finished_at=(NOW - timedelta(days=1)).isoformat(),
        )
        assert _apply(http, run["id"]).status_code == 200

    @pytest.mark.parametrize("run_id", ["not-a-uuid", "00000000-0000-0000-0000-000000000000"])
    def test_capture_an_unknown_run_is_not_found(self, crm, run_id):
        http, *_ = crm
        r = _apply(http, run_id)
        assert (r.status_code, r.json()["detail"]) == (404, "No such dry run.")

    def test_capture_live_is_accepted_after_an_apply(self, crm):
        http, fake, labs, _ = crm
        run = _finished(fake, labs)
        assert _apply(http, run["id"]).status_code == 200
        assert _mode(http, {"mode": "off"}).status_code == 200
        r = _mode(http, {"mode": "live"})
        assert r.status_code == 200, r.text
        assert fake.capture_setting(labs["id"]) == {"mode": "live"}

    @pytest.mark.parametrize("second", ["open", "finished"])
    def test_capture_live_by_patch_rejudges_a_second_dry_runs_decisions(self, crm, second):
        # CAP-INT-2: Live after run 1 was applied; run 2 judged a lead and
        # was cancelled (or finished, never applied) by a PATCH to Live: the
        # decision goes back to pending, so the live job judges it again.
        http, fake, labs, mailbox = crm
        run = _finished(fake, labs)
        assert _apply(http, run["id"]).status_code == 200
        assert _start(http).status_code == 202
        fake.mark_candidates(
            labs["id"],
            mailbox["id"],
            [{"thread_id": "t1", "message_id": "m1", "sent_at": NOW.isoformat()}],
        )
        [cand] = fake.rows["crm_capture_candidates"].values()
        fake.record_candidate(
            labs["id"],
            cand["id"],
            "m1",
            {"status": "judged", "outcome": "new_lead", "confidence": "high", "dry_run": True},
        )
        if second == "finished":
            [open_run] = [r for r in _runs(fake, labs) if r["finished_at"] is None]
            open_run["finished_at"] = NOW.isoformat()
        r = _mode(http, {"mode": "live"})
        assert r.status_code == 200, r.text
        assert (cand["status"], cand["outcome"], cand["dry_run"]) == ("pending", None, False)

    def test_capture_live_still_needs_a_primary_and_sent_mailbox(self, crm):
        http, fake, labs, mailbox = crm
        run = _finished(fake, labs)
        assert _apply(http, run["id"]).status_code == 200
        fake.rows["crm_mailboxes"][mailbox["id"]]["capture_source"] = "labels"
        r = _mode(http, {"mode": "live"})
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "Set a mailbox to Primary and Sent first.",
        )

    @pytest.mark.parametrize(
        ("user", "status"), [(MEMBER["id"], 403), (VIEWER["id"], 403), (OUTSIDER["id"], 404)]
    )
    def test_capture_apply_is_admin_only(self, crm, user, status):
        http, fake, labs, _ = crm
        run = _finished(fake, labs)
        assert _apply(http, run["id"], user).status_code == status
        assert fake.rows["crm_capture_dry_runs"][run["id"]]["applied_at"] is None


def _add(http, kind, value, user=OWNER["id"]):
    return http.post(
        f"{API}/capture/ignores",
        params=LABS,
        json={"kind": kind, "value": value},
        headers=as_(user),
    )


class TestTheIgnoreList:
    """Contracts §2.4, §2.5; FR-CAP-DIS-03."""

    def test_capture_add_lists_and_removes_an_entry(self, crm):
        http, fake, labs, _ = crm
        r = _add(http, "domain", "  Vendor.IO ")
        assert r.status_code == 201, r.text
        made = r.json()["ignore"]
        assert (made["kind"], made["value"]) == ("domain", "vendor.io")
        [shown] = _get(http).json()["ignores"]
        assert shown["id"] == made["id"] and shown["created_by_name"] == "Owner"
        assert shown["from_item_id"] is None
        [audit] = [a for a in fake.audit if a["target_table"] == "crm_capture_ignores"]
        assert (audit["action"], audit["actor_id"]) == ("capture_ignore.created", OWNER["id"])
        r = http.delete(
            f"{API}/capture/ignores/{made['id']}", params=LABS, headers=as_(OWNER["id"])
        )
        assert r.status_code == 204, r.text
        assert _get(http).json()["ignores"] == []
        assert [a["action"] for a in fake.audit if a["target_table"] == "crm_capture_ignores"] == [
            "capture_ignore.created",
            "capture_ignore.purged",
        ]

    @pytest.mark.parametrize(
        ("kind", "value", "status", "detail"),
        [
            ("address", "nobody", 422, "Enter an email address."),
            ("domain", "a@b.io", 422, "Enter a domain such as example.com."),
            (
                "domain",
                "gmail.com",
                422,
                "A public email domain cannot be ignored; ignore the sender instead.",
            ),
            ("domain", "nunnarilabs.com", 422, "Your own domains cannot be ignored."),
        ],
    )
    def test_capture_add_refusals(self, crm, kind, value, status, detail):
        http, fake, labs, _ = crm
        r = _add(http, kind, value)
        assert (r.status_code, r.json()["detail"]) == (status, detail)
        assert fake.list_ignores(labs["id"]) == []

    def test_capture_a_second_entry_for_the_same_value_is_refused(self, crm):
        http, *_ = crm
        assert _add(http, "address", "priya@acme.com").status_code == 201
        r = _add(http, "address", "Priya@Acme.com")
        assert (r.status_code, r.json()["detail"]) == (409, "That is already on the ignore list.")

    def test_capture_an_unknown_entry_is_not_found(self, crm):
        http, *_ = crm
        r = http.delete(
            f"{API}/capture/ignores/00000000-0000-0000-0000-000000000000",
            params=LABS,
            headers=as_(OWNER["id"]),
        )
        assert (r.status_code, r.json()["detail"]) == (404, "No such ignore entry.")

    @pytest.mark.parametrize(
        ("user", "status"), [(MEMBER["id"], 403), (VIEWER["id"], 403), (OUTSIDER["id"], 404)]
    )
    def test_capture_editing_the_list_is_admin_only(self, crm, user, status):
        http, fake, labs, _ = crm
        assert _add(http, "domain", "vendor.io", user).status_code == status
        made = _add(http, "domain", "vendor.io").json()["ignore"]
        r = http.delete(f"{API}/capture/ignores/{made['id']}", params=LABS, headers=as_(user))
        assert r.status_code == status
        assert len(fake.list_ignores(labs["id"])) == 1
