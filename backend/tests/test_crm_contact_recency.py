"""Contact recency — spec 009 FR-CRMAI-REL-01 to -03, FR-CRMAI-FLAG-03;
Q-17, Q-21; spec §15 "Contact recency":

- with fixed clocks, the levels match FR-CRMAI-REL-01 at 30, 31, 90 and 91
  days and for one-sided email (outbound only, inbound only);
- a company includes its people's contacts;
- with Gmail sync off the level is computed from meeting notes only and the
  record says "Meetings only";
- recomputed with the flags job while phase 4 is on, stored through
  `crm_set_contact_recency` and never `crm_apply`; nothing while it is off;
- the record read carries `contact_recency` while phase 4 is on; a view
  filters by it and cannot sort or group by it.

The clock is fixed at 2026-10-18 06:30 UTC, noon in Chennai. The service
runs on `tests/crm_fake.py`; `tests/db/test_crm_contact_recency_db.py`
checks the real writer and `crm_query`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from crm_fake import DEFAULTS, OWNER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.config import settings as config
from app.domain.crm import objects
from app.domain.crm import recency as rules
from app.services.crm import flags, jobs
from app.tasks import crm as tasks

API = "/api/v1/crm"
LABS = {"workspace": "labs"}
NOW = datetime(2026, 10, 18, 6, 30, tzinfo=UTC)


def _ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def _on(days: int) -> str:
    return (date(2026, 10, 18) - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# The levels — FR-CRMAI-REL-01, -03 (pure)
# ---------------------------------------------------------------------------


class TestTheLevels:
    @pytest.mark.parametrize(
        ("days", "level"), [(0, "recent"), (30, "recent"), (31, "fading"), (90, "fading")]
    )
    def test_recency_both_directions_at_the_edges(self, days, level):
        facts = {"in": _ago(days), "out": _ago(days), "meeting": None}
        assert rules.level(facts, now=NOW) == level

    @pytest.mark.parametrize(
        ("days", "level"), [(30, "recent"), (31, "fading"), (90, "fading"), (91, "lapsed")]
    )
    def test_recency_a_meeting_note_at_the_edges(self, days, level):
        assert rules.level({"in": None, "out": None, "meeting": _on(days)}, now=NOW) == level

    @pytest.mark.parametrize(("days", "level"), [(90, "fading"), (91, "lapsed")])
    def test_recency_one_email_at_the_ninety_day_edge(self, days, level):
        assert rules.level({"in": _ago(days), "out": None, "meeting": None}, now=NOW) == level
        assert rules.level({"in": None, "out": _ago(days), "meeting": None}, now=NOW) == level

    @pytest.mark.parametrize("side", ["in", "out"])
    def test_recency_one_sided_email_is_fading(self, side):
        assert rules.level({side: _ago(1)}, now=NOW) == "fading"

    def test_recency_both_directions_but_one_older_than_thirty_days_is_fading(self):
        assert rules.level({"in": _ago(2), "out": _ago(31)}, now=NOW) == "fading"

    def test_recency_nothing_is_lapsed(self):
        assert rules.level({"in": None, "out": None, "meeting": None}, now=NOW) == "lapsed"
        assert rules.level(None, now=NOW) == "lapsed"

    def test_recency_a_meeting_dated_after_today_does_not_count(self):
        assert rules.level({"meeting": "2026-10-19"}, now=NOW) == "lapsed"

    def test_recency_days_are_chennai_days(self):
        """23:00 UTC on 17 September is 18 September in Chennai: 30 days."""
        late = datetime(2026, 9, 17, 23, 0, tzinfo=UTC).isoformat()
        assert rules.level({"in": late, "out": late}, now=NOW) == "recent"

    def test_recency_meetings_only_reads_no_email(self):
        both = {"in": _ago(1), "out": _ago(1), "meeting": None}
        assert rules.level(both, now=NOW, meetings_only=True) == "lapsed"
        assert rules.level({**both, "meeting": _on(10)}, now=NOW, meetings_only=True) == "recent"
        assert rules.level({**both, "meeting": _on(60)}, now=NOW, meetings_only=True) == "fading"

    def test_recency_the_labels_are_not_warm_or_cold(self):
        assert rules.LABELS == {"recent": "Recent", "fading": "Fading", "lapsed": "Lapsed"}
        assert rules.MEETINGS_ONLY == "Meetings only"


# ---------------------------------------------------------------------------
# The service and the flags job — FR-CRMAI-REL-01 to -03, FR-CRMAI-FLAG-03
# ---------------------------------------------------------------------------


class Recency:
    """Labs, phase 4 on, Gmail sync live; Acme with Priya and Ravi, and
    Deepika's mailbox."""

    def __init__(self, monkeypatch) -> None:
        self.fake = fake = FakeCrmStore().install(monkeypatch)
        self.labs, self.academy = two_workspaces(fake)
        fake.set_ai(self.labs, phases={"4": True})
        self.setting("email_sync", {"enabled": True})
        monkeypatch.setattr(config, "CRM_GMAIL_CONFIGURED", True)
        monkeypatch.setattr(jobs, "_now", lambda: NOW)
        self.http = crm_client(monkeypatch, fake=fake)
        self.mailbox = fake.add(
            "crm_mailboxes",
            workspace_id=self.labs["id"],
            email="deepika@nunnarilabs.com",
            owner_id=OWNER["id"],
            labels=["Clients"],
            is_active=True,
        )
        self.acme = self.row("crm_companies", name="Acme")
        self.priya = self.row("crm_people", first_name="Priya", company_id=self.acme["id"])
        self.ravi = self.row("crm_people", first_name="Ravi", company_id=self.acme["id"])

    def setting(self, key: str, value: dict) -> None:
        found = next(
            (
                s
                for s in self.fake.rows["crm_workspace_settings"].values()
                if s["workspace_id"] == self.labs["id"] and s["key"] == key
            ),
            None,
        )
        if found is not None:
            found["value"] = value
        else:
            self.fake.add(
                "crm_workspace_settings", workspace_id=self.labs["id"], key=key, value=value
            )

    def row(self, table: str, **values) -> dict:
        return self.fake.add(
            table,
            **{
                **DEFAULTS.get(table, {}),
                "workspace_id": self.labs["id"],
                "created_at": _ago(200),
                "created_by": OWNER["id"],
                "updated_at": _ago(100),
                "updated_by": OWNER["id"],
                **values,
            },
        )

    def email(self, record: dict, record_type: str, days: float, direction: str, **values):
        message = self.fake.add(
            "crm_email_messages",
            **{
                "workspace_id": self.labs["id"],
                "mailbox_id": self.mailbox["id"],
                "gmail_message_id": f"g-{len(self.fake.rows['crm_email_messages'])}",
                "thread_id": "t1",
                "subject": "Pilot scope",
                "sent_at": _ago(days),
                "direction": direction,
                "from_email": "x@acme.in",
                "participants": [],
                "is_internal": False,
                "is_auto": False,
                **values,
            },
        )
        self.fake.add(
            "crm_email_links",
            workspace_id=self.labs["id"],
            message_id=message["id"],
            record_type=record_type,
            record_id=record["id"],
            created_at=_ago(days),
        )

    def meeting(self, record: dict, record_type: str, days: int, **values) -> None:
        note = self.row(
            "crm_notes",
            **{"body": "Met.", "kind": "meeting", "occurred_on": _on(days), **values},
        )
        self.fake.add(
            "crm_note_links",
            workspace_id=self.labs["id"],
            note_id=note["id"],
            record_type=record_type,
            record_id=record["id"],
            created_at=_ago(days),
            created_by=OWNER["id"],
        )

    def run(self) -> dict:
        return flags.recompute_workspace(self.labs["id"], NOW)

    def level(self, table: str, record: dict) -> str | None:
        return self.fake.rows[table][record["id"]].get("contact_recency")

    def get(self, plural: str, record: dict, who=OWNER) -> dict:
        r = self.http.get(f"{API}/{plural}/{record['id']}", params=LABS, headers=as_(who["id"]))
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def made(monkeypatch) -> Recency:
    return Recency(monkeypatch)


class TestTheFlagsJob:
    def test_recency_every_company_and_person_gets_a_level(self, made):
        made.email(made.priya, "person", 3, "inbound")
        made.email(made.priya, "person", 2, "outbound")
        counts = made.run()
        assert made.level("crm_people", made.priya) == "recent"
        assert made.level("crm_people", made.ravi) == "lapsed"
        assert made.level("crm_companies", made.acme) == "recent"
        assert counts["recency_changed"] == 3

    def test_recency_a_company_includes_its_people(self, made):
        """An inbound to the company and an outbound to one of its people
        make the company Recent; the person alone is Fading."""
        made.email(made.acme, "company", 5, "inbound")
        made.email(made.ravi, "person", 4, "outbound")
        made.run()
        assert made.level("crm_companies", made.acme) == "recent"
        assert made.level("crm_people", made.ravi) == "fading"
        assert made.level("crm_people", made.priya) == "lapsed"

    def test_recency_a_persons_meeting_counts_for_the_company(self, made):
        made.meeting(made.priya, "person", 20)
        made.run()
        assert made.level("crm_companies", made.acme) == "recent"
        assert made.level("crm_people", made.priya) == "recent"

    def test_recency_a_person_in_the_trash_does_not_count(self, made):
        made.meeting(made.priya, "person", 20)
        made.fake.rows["crm_people"][made.priya["id"]]["deleted_at"] = _ago(1)
        made.run()
        assert made.level("crm_companies", made.acme) == "lapsed"

    @pytest.mark.parametrize("flag", ["is_internal", "is_auto"])
    def test_recency_internal_and_automatic_email_does_not_count(self, made, flag):
        made.email(made.acme, "company", 3, "inbound", **{flag: True})
        made.email(made.acme, "company", 2, "outbound", **{flag: True})
        made.run()
        assert made.level("crm_companies", made.acme) == "lapsed"

    @pytest.mark.parametrize("values", [{"kind": "call"}, {"deleted_at": _ago(1)}])
    def test_recency_only_a_live_meeting_note_counts(self, made, values):
        made.meeting(made.acme, "company", 3, **values)
        made.run()
        assert made.level("crm_companies", made.acme) == "lapsed"

    def test_recency_email_older_than_ninety_days_is_lapsed(self, made):
        made.email(made.acme, "company", 91, "inbound")
        made.email(made.acme, "company", 92, "outbound")
        made.run()
        assert made.level("crm_companies", made.acme) == "lapsed"

    @pytest.mark.parametrize("off", ["switch", "credential"])
    def test_recency_with_gmail_sync_off_only_meetings_count(self, made, monkeypatch, off):
        if off == "switch":
            made.setting("email_sync", {"enabled": False})
        else:
            monkeypatch.setattr(config, "CRM_GMAIL_CONFIGURED", False)
        made.email(made.acme, "company", 3, "inbound")
        made.email(made.acme, "company", 2, "outbound")
        made.meeting(made.priya, "person", 45)
        made.run()
        assert made.level("crm_companies", made.acme) == "fading"
        assert made.level("crm_people", made.priya) == "fading"
        assert made.level("crm_people", made.ravi) == "lapsed"

    def test_recency_phase_4_off_computes_nothing(self, made):
        made.fake.set_ai(made.labs, phases={"4": False})
        made.email(made.acme, "company", 3, "inbound")
        counts = made.run()
        assert "recency_changed" not in counts
        assert made.fake.recency_writes == []
        assert made.level("crm_companies", made.acme) is None

    def test_recency_switching_phase_4_off_keeps_what_is_stored(self, made):
        made.meeting(made.acme, "company", 3)
        made.run()
        made.fake.set_ai(made.labs, phases={"4": False})
        made.fake.rows["crm_notes"].clear()
        made.run()
        assert made.level("crm_companies", made.acme) == "recent"

    def test_recency_a_recompute_is_not_an_edit(self, made):
        """Written through `crm_set_contact_recency` only: neither updated
        time, updated by nor the audit moves (FR-CRMAI-REL-02)."""
        made.meeting(made.acme, "company", 3)
        before = dict(made.fake.rows["crm_companies"][made.acme["id"]])
        audit = len(made.fake.audit)
        made.run()
        after = made.fake.rows["crm_companies"][made.acme["id"]]
        assert after["contact_recency"] == "recent"
        assert (after["updated_at"], after["updated_by"]) == (
            before["updated_at"],
            before["updated_by"],
        )
        assert len(made.fake.audit) == audit
        assert ("crm_companies", 1) in made.fake.recency_writes

    def test_recency_an_unchanged_level_counts_no_change(self, made):
        made.meeting(made.acme, "company", 3)
        made.run()
        assert made.run()["recency_changed"] == 0

    def test_recency_runs_in_the_flags_job_and_is_counted(self, made):
        made.meeting(made.acme, "company", 3)
        run = tasks._flags(NOW)["runs"]["labs"]
        assert run["outcome"] == "ok"
        assert run["counts"]["recency_changed"] == 3
        assert made.level("crm_companies", made.acme) == "recent"
        assert "recency_changed" not in tasks._flags(NOW)["runs"]["academy"]["counts"]

    def test_recency_another_workspaces_contacts_do_not_count(self, made):
        other = made.fake.add(
            "crm_companies",
            **{**DEFAULTS["crm_companies"], "workspace_id": made.academy["id"], "name": "Acme"},
        )
        made.fake.set_ai(made.academy, phases={"4": True})
        made.meeting(made.acme, "company", 3)
        flags.recompute_workspace(made.academy["id"], NOW)
        assert made.fake.rows["crm_companies"][other["id"]]["contact_recency"] == "lapsed"
        assert made.level("crm_companies", made.acme) is None


# ---------------------------------------------------------------------------
# The record read and the view filter — FR-CRMAI-REL-02, -03; contracts §1
# ---------------------------------------------------------------------------


class TestTheRecordRead:
    def test_recency_a_company_and_a_person_carry_it(self, made):
        made.meeting(made.acme, "company", 3)
        made.run()
        assert made.get("companies", made.acme)["contact_recency"] == {
            "level": "recent",
            "meetings_only": False,
        }
        assert made.get("people", made.ravi)["contact_recency"] == {
            "level": "lapsed",
            "meetings_only": False,
        }

    def test_recency_before_the_first_recompute_the_level_is_none(self, made):
        assert made.get("companies", made.acme)["contact_recency"]["level"] is None

    def test_recency_gmail_off_says_meetings_only(self, made):
        made.setting("email_sync", {"enabled": False})
        assert made.get("companies", made.acme)["contact_recency"]["meetings_only"] is True

    def test_recency_viewers_see_it(self, made):
        made.meeting(made.acme, "company", 3)
        made.run()
        assert made.get("companies", made.acme, who=VIEWER)["contact_recency"]["level"] == (
            "recent"
        )

    def test_recency_hidden_while_phase_4_is_off(self, made):
        made.meeting(made.acme, "company", 3)
        made.run()
        made.fake.set_ai(made.labs, phases={"4": False})
        assert "contact_recency" not in made.get("companies", made.acme)
        assert "contact_recency" not in made.get("people", made.priya)

    def test_recency_only_companies_and_people_have_a_level(self, made):
        assert set(rules.TABLES) == {"company", "person"}
        deal = made.row("crm_opportunities", name="Acme pilot", company_id=made.acme["id"])
        assert "contact_recency" not in made.get("opportunities", deal)


class TestTheViewFilter:
    def _query(self, made: Recency, spec: dict, obj: str = "companies"):
        return made.http.post(
            f"{API}/query",
            params=LABS,
            json={"object": obj, "spec": spec},
            headers=as_(OWNER["id"]),
        )

    def test_recency_is_a_view_field_for_companies_and_people_filter_only(self):
        for kind in ("company", "person"):
            field = objects.view_field(kind, "contact_recency")
            assert field is not None and field.type == "fixed_select"
            assert (field.filterable, field.sortable, field.groupable) == (True, False, False)
            assert not field.is_amount and field.workspace_scope == "all"
        assert objects.view_field("opportunity", "contact_recency") is None

    @pytest.mark.parametrize("obj", ["companies", "people"])
    def test_recency_filters_a_view(self, made, obj):
        made.meeting(made.acme, "company", 3)
        made.meeting(made.priya, "person", 3)
        made.run()
        recent = {made.acme["id"], made.priya["id"]}
        for operator, value in (("any_of", ["recent"]), ("is", "recent")):
            r = self._query(
                made,
                {
                    "filters": {
                        "op": "and",
                        "items": [
                            {"field": "contact_recency", "operator": operator, "value": value}
                        ],
                    }
                },
                obj,
            )
            assert r.status_code == 200, r.text
            ids = {row["id"] for row in r.json()["rows"]}
            assert ids and ids <= recent

    @pytest.mark.parametrize(
        "spec",
        [{"sorts": [{"field": "contact_recency", "dir": "asc"}]}, {"group_by": "contact_recency"}],
        ids=["sort", "group"],
    )
    def test_recency_cannot_sort_or_group_a_view(self, made, spec):
        assert self._query(made, spec).status_code == 422
