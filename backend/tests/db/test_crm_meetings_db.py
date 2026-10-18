"""Meeting notes in the real database — spec 009 FR-CRMAI-MEET-04 to -07;
P-5, Q-41; data-model §3.4, §5; migration 063.

What only Postgres can prove: one meeting note per transcript and workspace
(the partial unique index on `source_ref`); `crm_store_meeting_note` writes
the note through the audited insert path, so its audit row is `note.created`
with actor kind `sync`; it holds every link to the workspace; it updates the
note for a newer transcript, and refuses to once a person's edit is audited
(Q-41); it stores the meeting's items through `crm_review_upsert`, a
`deal_link` item targeting the note.

The store wrappers of `services/crm/store.py` run against the local stack.
Each test works in workspaces of its own.
"""

from __future__ import annotations

import json
import uuid

import pytest
from supabase import create_client

from app.services.crm import CrmRefused, store

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}


def _j(stack, value) -> str:
    return f"{stack.q(json.dumps(value))}::jsonb"


def _as_system(stack, statement: str) -> list[dict]:
    return stack.sql(
        f"SELECT set_config('crm.actor', {_j(stack, SYSTEM)}::text, true); {statement}"
    )


def _actor(ws: str, owner: str) -> dict:
    return {"user_id": owner, "kind": "user", "channel": "web", "workspace_id": ws}


def _sync(ws: str) -> dict:
    return {"user_id": None, "kind": "sync", "channel": None, "workspace_id": ws}


def _apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r")[0]["r"]


@pytest.fixture
def owner(make_profile) -> str:
    return make_profile(keep=True)


def _workspace(stack, owner) -> str:
    key = f"m{uuid.uuid4().hex[:12]}"
    ws = _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Meetings ' + key)}) RETURNING id",
    )[-1]["id"]
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(ws)}, {stack.q(owner)}, 'admin')",
    )
    return ws


@pytest.fixture
def ws(stack, owner) -> str:
    return _workspace(stack, owner)


@pytest.fixture
def other(stack, owner) -> str:
    return _workspace(stack, owner)


@pytest.fixture
def real(stack, monkeypatch):
    """The real store against the local stack."""
    monkeypatch.setattr(store, "supabase", create_client(stack.url, stack.service_key))
    return store


def _company(stack, ws, owner, name="Acme") -> str:
    return _apply(
        stack,
        _actor(ws, owner),
        [
            {
                "op": "insert",
                "table": "crm_companies",
                "values": {"name": name, "owner_id": owner, "lead_stage": "deal_open"},
            }
        ],
    )[0]["id"]


def _deal(stack, ws, owner, company, name="Pilot") -> str:
    return _apply(
        stack,
        _actor(ws, owner),
        [
            {
                "op": "insert",
                "table": "crm_opportunities",
                "values": {
                    "name": name,
                    "company_id": company,
                    "owner_id": owner,
                    "probability": 10,
                    "next_step": "Call",
                    "next_step_on": "2026-10-21",
                },
            }
        ],
    )[0]["id"]


def _plan(links: list[dict], *, updated: str = "2026-10-18T09:30:00+00:00", **values) -> dict:
    return {
        "source_ref": "fireflies:t1",
        "occurred_on": "2026-10-18",
        "body": "Meeting: Pilot review\n\nSummary\nPriya asked for a plan.",
        "ai_provenance": {
            "source_kind": "meeting",
            "model": "claude-sonnet-5-5",
            "generated_at": "2026-10-18T12:00:00+00:00",
            "transcript_updated_at": updated,
            "transcript_url": "https://app.fireflies.ai/view/t1",
        },
        "links": links,
        "items": [],
        **values,
    }


def _note(stack, note_id: str) -> dict:
    [row] = stack.sql(
        "SELECT id, kind, occurred_on::text AS occurred_on, body, source_ref, ai_provenance "
        f"FROM crm_notes WHERE id = {stack.q(note_id)}"
    )
    return row


def _links(stack, note_id: str) -> set[tuple[str, str]]:
    rows = stack.sql(
        "SELECT record_type, record_id::text AS record_id FROM crm_note_links "
        f"WHERE note_id = {stack.q(note_id)}"
    )
    return {(r["record_type"], r["record_id"]) for r in rows}


@pytest.fixture
def acme(stack, ws, owner) -> dict:
    company = _company(stack, ws, owner)
    deal = _deal(stack, ws, owner, company)
    return {
        "company": company,
        "deal": deal,
        "links": [
            {"record_type": "company", "record_id": company},
            {"record_type": "opportunity", "record_id": deal},
        ],
    }


class TestOnePerTranscript:
    """FR-CRMAI-MEET-07, P-5."""

    def test_meetings_db_source_ref_is_unique_per_workspace(self, stack, ws, other, real, acme):
        made = real.store_meeting_note(_sync(ws), _plan(acme["links"]))
        assert made["created"] is True
        with pytest.raises(stack.SqlError) as raised:
            _as_system(
                stack,
                "INSERT INTO crm_notes (workspace_id, body, kind, occurred_on, source_ref) VALUES "
                f"({stack.q(ws)}, 'Again', 'meeting', '2026-10-18', 'fireflies:t1')",
            )
        assert raised.value.code == "23505"
        # The same transcript in another workspace is another note.
        elsewhere = real.store_meeting_note(_sync(other), _plan([]))
        assert elsewhere["created"] is True and elsewhere["note_id"] != made["note_id"]

    def test_meetings_db_a_re_poll_changes_nothing(self, stack, ws, real, acme):
        made = real.store_meeting_note(_sync(ws), _plan(acme["links"]))
        again = real.store_meeting_note(_sync(ws), _plan(acme["links"], body="Other."))
        assert again == {
            "note_id": made["note_id"],
            "created": False,
            "updated": False,
            "items": [],
        }
        assert _note(stack, made["note_id"])["body"].startswith("Meeting: Pilot review")


class TestTheNote:
    """FR-CRMAI-MEET-04, -05."""

    def test_meetings_db_note_created_audit_row_carries_actor_kind_sync(
        self, stack, ws, real, acme
    ):
        made = real.store_meeting_note(_sync(ws), _plan(acme["links"]))
        note = _note(stack, made["note_id"])
        assert (note["kind"], note["occurred_on"], note["source_ref"]) == (
            "meeting",
            "2026-10-18",
            "fireflies:t1",
        )
        assert note["ai_provenance"]["transcript_url"] == "https://app.fireflies.ai/view/t1"
        [row] = stack.sql(
            "SELECT action, actor_label, actor_id FROM audit_log "
            f"WHERE target_table = 'crm_notes' AND target_id = {stack.q(made['note_id'])}"
        )
        assert row == {"action": "note.created", "actor_label": "sync", "actor_id": None}
        assert _links(stack, made["note_id"]) == {
            ("company", acme["company"]),
            ("opportunity", acme["deal"]),
        }

    def test_meetings_db_links_refuse_another_workspaces_record(
        self, stack, ws, other, owner, real
    ):
        foreign = _company(stack, other, owner, "Elsewhere")
        with pytest.raises(CrmRefused) as refused:
            real.store_meeting_note(
                _sync(ws), _plan([{"record_type": "company", "record_id": foreign}])
            )
        assert refused.value.status == 404
        assert (
            stack.sql(
                f"SELECT id FROM crm_notes WHERE workspace_id = {stack.q(ws)} "
                "AND source_ref = 'fireflies:t1'"
            )
            == []
        )

    def test_meetings_db_refuses_a_person_actor_and_a_missing_transcript(
        self, stack, ws, owner, real
    ):
        with pytest.raises(CrmRefused):
            real.store_meeting_note(_actor(ws, owner), _plan([]))
        with pytest.raises(CrmRefused):
            real.store_meeting_note(_sync(ws), _plan([], source_ref="manual"))

    def test_meetings_db_items_point_at_the_note(self, stack, ws, owner, real, acme):
        rollout = _deal(stack, ws, owner, acme["company"], "Rollout")
        ask = {
            "type": "deal_link",
            "owner_id": owner,
            "record_type": "note",
            "record_id": None,
            "suggestion": {},
            "current_values": {},
            "source_kind": "meeting",
            "source_key": "fireflies:t1",
            "source_version": "2026-10-18T09:30:00+00:00",
            "source_link": {},
            "dedupe_key": "meeting|fireflies:t1|deal_link|note:$note",
            "model": "claude-sonnet-5-5",
            "generated_at": "2026-10-18T12:00:00+00:00",
            "deal_choices": sorted([acme["deal"], rollout]),
        }
        made = real.store_meeting_note(_sync(ws), _plan(acme["links"][:1], items=[ask]))
        [item] = made["items"]
        assert item["created"] is True
        [row] = stack.sql(
            "SELECT record_type, record_id::text AS record_id, suggestion, source_link, "
            f"dedupe_key FROM crm_review_items WHERE id = {stack.q(item['id'])}"
        )
        note_id = made["note_id"]
        assert row == {
            "record_type": "note",
            "record_id": note_id,
            "suggestion": {"note_id": note_id},
            "source_link": {"record_type": "note", "record_id": note_id},
            "dedupe_key": f"meeting|fireflies:t1|deal_link|note:{note_id}",
        }


class TestUpdatedTranscript:
    """FR-CRMAI-MEET-07, Q-41."""

    def test_meetings_db_a_newer_transcript_updates_the_note(self, stack, ws, real, acme):
        made = real.store_meeting_note(_sync(ws), _plan(acme["links"]))
        again = real.store_meeting_note(
            _sync(ws),
            _plan(acme["links"], updated="2026-10-18T11:00:00+00:00", body="Meeting: v2"),
        )
        assert (again["created"], again["updated"]) == (False, True)
        note = _note(stack, made["note_id"])
        assert note["body"] == "Meeting: v2"
        assert note["ai_provenance"]["transcript_updated_at"] == "2026-10-18T11:00:00+00:00"

    def test_meetings_db_refuses_to_overwrite_after_a_persons_audited_edit(
        self, stack, ws, owner, real, acme
    ):
        made = real.store_meeting_note(_sync(ws), _plan(acme["links"]))
        _apply(
            stack,
            _actor(ws, owner),
            [
                {
                    "op": "update",
                    "table": "crm_notes",
                    "id": made["note_id"],
                    "values": {"body": "My own words."},
                }
            ],
        )
        again = real.store_meeting_note(
            _sync(ws),
            _plan(acme["links"], updated="2026-10-18T11:00:00+00:00", body="Meeting: v2"),
        )
        assert again["edited_by_person"] is True
        assert (again["created"], again["updated"]) == (False, False)
        note = _note(stack, made["note_id"])
        assert note["body"] == "My own words."
        assert note["ai_provenance"]["transcript_updated_at"] == "2026-10-18T09:30:00+00:00"
