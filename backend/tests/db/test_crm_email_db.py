"""Gmail sync's tables in the real database — spec 007 FR-CRM-SYNC-01, -07,
-09, -10, FR-CRM-FLAG-04, FR-CRM-DUP-03, FR-CRM-DEL-01; data-model §3.22,
§5.4, §5.7, §5.14; migration 039.

What only Postgres can prove: `UNIQUE (mailbox_id, gmail_message_id)` makes
a second store of a message a no-op; `crm_purge` removes a record's email
links; `crm_merge_companies` moves them to the survivor; a link to another
workspace's record is refused (`crm_check_refs`); a mailbox's address is
unique across workspaces and must be at a delegated domain; its cursor is
written with no audit row while an admin's change is audited; and the flag
facts read the latest email that counts, never an auto-reply.

Each test works in workspaces of its own, so leftover rows from earlier runs
never meet its assertions.
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


def _apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r")[0]["r"]


def _actor(ws: str, owner: str) -> dict:
    return {"user_id": owner, "kind": "user", "channel": "web", "workspace_id": ws}


@pytest.fixture
def owner(make_profile) -> str:
    return make_profile(keep=True)


def _workspace(stack, owner) -> str:
    key = f"t{uuid.uuid4().hex[:12]}"
    workspace = _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Email ' + key)}) RETURNING id",
    )[-1]["id"]
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(workspace)}, {stack.q(owner)}, 'admin')",
    )
    return workspace


@pytest.fixture
def ws(stack, owner) -> str:
    return _workspace(stack, owner)


@pytest.fixture
def real(stack, monkeypatch):
    """The real store against the local stack."""
    monkeypatch.setattr(store, "supabase", create_client(stack.url, stack.service_key))
    return store


def _address() -> str:
    return f"t{uuid.uuid4().hex[:12]}@nunnarilabs.com"


def _mailbox(stack, ws, owner, email=None) -> dict:
    return _apply(
        stack,
        _actor(ws, owner),
        [
            {
                "op": "insert",
                "table": "crm_mailboxes",
                "values": {"email": email or _address(), "owner_id": owner, "labels": ["Sales"]},
            }
        ],
    )[0]["row"]


def _company(stack, ws, owner, name="Acme") -> dict:
    return _apply(
        stack,
        _actor(ws, owner),
        [{"op": "insert", "table": "crm_companies", "values": {"name": name, "owner_id": owner}}],
    )[0]["row"]


def _message(gmail_id: str, links: list[dict], **values) -> dict:
    return {
        "gmail_message_id": gmail_id,
        "thread_id": "t1",
        "subject": "Proposal",
        "sent_at": "2026-10-13T09:00:00+00:00",
        "direction": "inbound",
        "from_email": "priya@acme.com",
        "participants": [{"email": "priya@acme.com", "name": "Priya", "role": "from"}],
        "is_internal": False,
        "is_auto": False,
        "links": links,
        **values,
    }


def _links_of(stack, record_id: str) -> list[dict]:
    return stack.sql(
        "SELECT record_type, record_id, message_id FROM crm_email_links "
        f"WHERE record_id = {stack.q(record_id)}"
    )


def _audit(stack, target: str) -> int:
    return stack.sql(f"SELECT count(*) AS n FROM audit_log WHERE target_id = {stack.q(target)}")[0][
        "n"
    ]


class TestStoringOnce:
    """FR-CRM-SYNC-07."""

    def test_a_double_insert_is_a_no_op(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner)
        acme = _company(stack, ws, owner)
        links = [{"record_type": "company", "record_id": acme["id"]}]
        assert real.store_emails(ws, box["id"], [_message("m1", links)]) == ["m1"]
        assert real.store_emails(ws, box["id"], [_message("m1", links)]) == []
        [row] = stack.sql(
            f"SELECT count(*) AS n FROM crm_email_messages WHERE mailbox_id = {stack.q(box['id'])}"
        )
        assert row["n"] == 1
        assert len(_links_of(stack, acme["id"])) == 1
        assert real.stored_message_ids(ws, box["id"], ["m1", "m2"]) == {"m1"}

    def test_the_raw_unique_constraint(self, stack, ws, owner):
        box = _mailbox(stack, ws, owner)
        insert = (
            "INSERT INTO crm_email_messages (workspace_id, mailbox_id, gmail_message_id, "
            "thread_id, sent_at, direction) VALUES "
            f"({stack.q(ws)}, {stack.q(box['id'])}, 'dup', 't', now(), 'inbound')"
        )
        stack.sql(insert)
        with pytest.raises(stack.SqlError) as raised:
            stack.sql(insert)
        assert raised.value.code == "23505"

    def test_no_body_column(self, stack):
        columns = {
            r["column_name"]
            for r in stack.sql(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'crm_email_messages'"
            )
        }
        assert not columns & {"body", "snippet", "html", "text"}

    def test_removal_returns_what_it_was_linked_to(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner)
        acme = _company(stack, ws, owner)
        real.store_emails(
            ws, box["id"], [_message("m1", [{"record_type": "company", "record_id": acme["id"]}])]
        )
        gone = real.remove_emails(ws, box["id"], ["m1", "never-stored"])
        assert gone == {
            "removed": 1,
            "records": [{"record_type": "company", "record_id": acme["id"]}],
        }
        assert _links_of(stack, acme["id"]) == []


class TestOtherWorkspaces:
    """FR-CRM-SYNC-01, -10."""

    def test_a_link_to_another_workspaces_record_is_refused(self, stack, ws, owner, real):
        other = _workspace(stack, owner)
        theirs = _company(stack, other, owner, "Theirs")
        box = _mailbox(stack, ws, owner)
        with pytest.raises(CrmRefused) as refused:
            real.store_emails(
                ws,
                box["id"],
                [_message("m1", [{"record_type": "company", "record_id": theirs["id"]}])],
            )
        assert refused.value.status == 404
        assert real.stored_message_ids(ws, box["id"], ["m1"]) == set()

    def test_another_workspaces_mailbox_is_not_found(self, stack, ws, owner, real):
        other = _workspace(stack, owner)
        theirs = _mailbox(stack, other, owner)
        with pytest.raises(CrmRefused) as refused:
            real.store_emails(ws, theirs["id"], [_message("m1", [])])
        assert refused.value.status == 404

    def test_one_address_in_one_workspace(self, stack, ws, owner, real):
        other = _workspace(stack, owner)
        address = _address()
        _mailbox(stack, ws, owner, address)
        assert real.find_mailbox_by_email(address)["workspace_id"] == ws
        with pytest.raises(stack.SqlError) as raised:
            _mailbox(stack, other, owner, address)
        assert raised.value.code == "23505"

    def test_only_the_delegated_domains(self, stack, ws, owner):
        with pytest.raises(stack.SqlError) as raised:
            _mailbox(stack, ws, owner, f"t{uuid.uuid4().hex[:8]}@gmail.com")
        assert raised.value.code == "23514"


class TestMailboxAudit:
    """039's crm_audit_row: the cursor is the sync's bookkeeping."""

    def test_the_cursor_writes_no_audit_row_an_admins_change_does(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner)
        created = _audit(stack, box["id"])
        assert created == 1
        real.mailbox_progress(
            ws,
            box["id"],
            {"history_id": "42", "page_token": None, "last_synced_at": "2026-10-14T06:30:00Z"},
        )
        assert _audit(stack, box["id"]) == created
        _apply(
            stack,
            _actor(ws, owner),
            [
                {
                    "op": "update",
                    "table": "crm_mailboxes",
                    "id": box["id"],
                    "values": {"labels": ["Sales", "Leads"]},
                }
            ],
        )
        [last] = stack.sql(
            "SELECT action FROM audit_log "
            f"WHERE target_id = {stack.q(box['id'])} ORDER BY id DESC LIMIT 1"
        )
        assert last["action"] == "mailbox.updated"

    def test_a_cursor_write_is_held_to_the_cursor(self, ws, real):
        with pytest.raises(ValueError):
            real.mailbox_progress(ws, str(uuid.uuid4()), {"labels": ["x"]})

    def test_capture_cursors_write_no_audit_row_a_source_change_does(self, stack, ws, owner, real):
        """Spec 010 (071): the deal-document cursor and the switch-back
        reconcile point are bookkeeping; the capture source is a setting."""
        box = _mailbox(stack, ws, owner)
        created = _audit(stack, box["id"])
        real.mailbox_progress(
            ws,
            box["id"],
            {
                "docs_checked_at": "2026-10-14T06:30:00Z",
                "reconcile_after": "2026-04-01T00:00:00+05:30",
            },
        )
        real.mailbox_progress(ws, box["id"], {"reconcile_after": None})
        assert _audit(stack, box["id"]) == created
        _apply(
            stack,
            _actor(ws, owner),
            [
                {
                    "op": "update",
                    "table": "crm_mailboxes",
                    "id": box["id"],
                    "values": {"capture_source": "primary_sent"},
                }
            ],
        )
        [last] = stack.sql(
            "SELECT action, after FROM audit_log "
            f"WHERE target_id = {stack.q(box['id'])} ORDER BY id DESC LIMIT 1"
        )
        assert last["action"] == "mailbox.updated"
        assert last["after"] == {"capture_source": "primary_sent"}


class TestPurgeAndMerge:
    """Data-model §5.4 (039 crm_purge), §5.7 (039 crm_merge_companies)."""

    def test_crm_purge_removes_email_links(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner)
        acme = _company(stack, ws, owner)
        real.store_emails(
            ws, box["id"], [_message("m1", [{"record_type": "company", "record_id": acme["id"]}])]
        )
        _apply(
            stack,
            _actor(ws, owner),
            [{"op": "soft_delete", "table": "crm_companies", "id": acme["id"]}],
        )
        real.purge(_actor(ws, owner), [{"table": "crm_companies", "id": acme["id"]}])
        assert _links_of(stack, acme["id"]) == []
        # The message itself stays (it may name other records).
        assert real.stored_message_ids(ws, box["id"], ["m1"]) == {"m1"}

    def test_crm_merge_companies_moves_them_to_the_survivor(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner)
        keep = _company(stack, ws, owner, "Acme")
        gone = _company(stack, ws, owner, "Acme Ltd")
        real.store_emails(
            ws,
            box["id"],
            [
                _message("m1", [{"record_type": "company", "record_id": gone["id"]}]),
                # Linked to both: keeps one link, the survivor's.
                _message(
                    "m2",
                    [
                        {"record_type": "company", "record_id": gone["id"]},
                        {"record_type": "company", "record_id": keep["id"]},
                    ],
                ),
            ],
        )
        out = real.merge_companies(
            _actor(ws, owner), {"survivor_id": keep["id"], "merged_id": gone["id"]}
        )
        assert out["moved"]["emails"] == 2
        assert _links_of(stack, gone["id"]) == []
        assert len(_links_of(stack, keep["id"])) == 2


class TestFlagFacts:
    """FR-CRM-FLAG-04 — the latest email that counts, per record."""

    def test_auto_replies_never_count(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner)
        acme = _company(stack, ws, owner)
        link = [{"record_type": "company", "record_id": acme["id"]}]
        real.store_emails(
            ws,
            box["id"],
            [
                _message("m1", link, sent_at="2026-10-10T09:00:00+00:00"),
                _message("m2", link, sent_at="2026-10-12T09:00:00+00:00", is_auto=True),
                _message("m3", link, sent_at="2026-10-11T09:00:00+00:00", direction="outbound"),
            ],
        )
        facts = real.flag_facts(ws, [], [acme["id"]], [])
        found = facts["emails"]["company"][acme["id"]]
        assert found["in"].startswith("2026-10-10T09:00:00")
        assert found["out"].startswith("2026-10-11T09:00:00")
        # Narrowed to nothing: no email facts at all.
        assert real.flag_facts(ws, [], [], [])["emails"] == {}

    def test_the_seeded_switch_is_off(self, stack):
        rows = stack.sql(
            "SELECT DISTINCT value FROM crm_workspace_settings WHERE key = 'email_sync' "
            "AND workspace_id IN (SELECT id FROM crm_workspaces WHERE key IN ('labs', 'academy'))"
        )
        assert rows == [{"value": {"enabled": False}}]


class TestOwnerLeaves:
    """040 — removing a membership pauses the person's mailboxes there."""

    def test_their_mailbox_is_paused_and_audited(self, stack, ws, owner, make_profile, real):
        member = make_profile(keep=True)
        membership = _as_system(
            stack,
            "INSERT INTO crm_memberships (workspace_id, user_id, role) "
            f"VALUES ({stack.q(ws)}, {stack.q(member)}, 'member') RETURNING id",
        )[-1]["id"]
        theirs = _mailbox(stack, ws, member)
        mine = _mailbox(stack, ws, owner)
        stack.sql(
            f"SELECT crm_remove_membership({_j(stack, _actor(ws, owner))}, {stack.q(membership)})"
        )
        active = {m["id"]: m["is_active"] for m in real.list_mailboxes(ws)}
        assert active == {theirs["id"]: False, mine["id"]: True}
        [last] = stack.sql(
            "SELECT action, actor_id FROM audit_log "
            f"WHERE target_id = {stack.q(theirs['id'])} ORDER BY id DESC LIMIT 1"
        )
        assert (last["action"], last["actor_id"]) == ("mailbox.updated", owner)


class TestLongReads:
    """More rows than one PostgREST response holds (`max_rows` = 1000)."""

    def test_every_link_and_stored_id_is_read(self, stack, ws, owner, real):
        box = _mailbox(stack, ws, owner)
        acme = _company(stack, ws, owner)
        link = [{"record_type": "company", "record_id": acme["id"]}]
        messages = [_message(f"m{n}", link) for n in range(1001)]
        messages.append(_message("old", link, sent_at="2026-01-01T00:00:00+00:00"))
        assert len(real.store_emails(ws, box["id"], messages)) == 1002
        assert len(real.email_links(ws, "company", [acme["id"]])) == 1002
        found = real.mailbox_message_ids(ws, box["id"], "2026-06-01T00:00:00+00:00")
        assert len(found) == 1001 and "old" not in found
