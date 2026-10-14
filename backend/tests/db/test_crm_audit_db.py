"""The CRM's write path in the real database — spec 007 FR-CRM-AUD-01, -02,
-06, FR-CRM-ACC-04, -10, -12, -14, FR-CRM-WS-02, FR-CRM-CHG-02, -15;
data-model §5.1–§5.3, §5.12–§5.15; Q-21, Q-44, Q-75.

What only Postgres can prove: the audit trigger refuses a write with no actor
and records one with its workspace, kind and channel in the same
transaction; `crm_apply` keeps a write inside the actor's workspace; the
membership, invitation and account-type functions hold their rules under a
row lock, including against a concurrent change; and the browser roles can
neither read a CRM table nor execute a CRM function.

Each test that needs a workspace makes its own, so leftovers from an earlier
run (or the seeds) cannot change an admin count. Concurrency is two requests
to the local stack at once: the first holds its transaction open with
pg_sleep while the second runs into its lock.
"""

from __future__ import annotations

import json
import threading
import time
import uuid

import pytest

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}


def _j(stack, value) -> str:
    return f"{stack.q(json.dumps(value))}::jsonb"


def _as_system(stack, statement: str) -> list[dict]:
    """Run a statement with the system actor set, as the migrations' seeds do."""
    return stack.sql(
        f"SELECT set_config('crm.actor', {_j(stack, SYSTEM)}::text, true); {statement}"
    )


def _actor(workspace: str, user: str | None, *, kind: str = "user", channel: str = "web") -> dict:
    return {"user_id": user, "kind": kind, "channel": channel, "workspace_id": workspace}


def _apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r")[0]["r"]


def _refused(stack, query: str, code: str) -> str:
    with pytest.raises(stack.SqlError) as raised:
        stack.sql(query)
    assert raised.value.code == code, raised.value.message
    return raised.value.message


def _audit(stack, target_id: str) -> list[dict]:
    return stack.sql(
        "SELECT actor_id, actor_label, channel, workspace_id, action, target_table, before, after "
        f"FROM audit_log WHERE target_id = {stack.q(target_id)} ORDER BY id"
    )


@pytest.fixture
def workspace(stack) -> str:
    key = f"t{uuid.uuid4().hex[:12]}"
    return _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name, allowed_domains) "
        f"VALUES ({stack.q(key)}, {stack.q('Test ' + key)}, ARRAY['example.com']) RETURNING id",
    )[-1]["id"]


@pytest.fixture
def person(make_profile):
    """A staff profile that may act (and so is never deleted, see conftest)."""

    def make(**profile) -> str:
        return make_profile(keep=True, **profile)

    return make


def _member(stack, workspace: str, user: str, role: str) -> str:
    return _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(workspace)}, {stack.q(user)}, {stack.q(role)}) RETURNING id",
    )[-1]["id"]


def _profile(stack, user: str) -> dict:
    return stack.sql(f"SELECT * FROM profiles WHERE id = {stack.q(user)}")[0]


def _at_once(first: str, second, stack, *, head_start: float = 0.4):
    """Run `first` (which sleeps holding its locks) and, once it has started,
    `second()`; return what `first` returned or raised."""
    outcome: dict = {}

    def run_first():
        try:
            outcome["value"] = stack.sql(first)
        except stack.SqlError as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=run_first)
    thread.start()
    time.sleep(head_start)
    try:
        second()
    finally:
        thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


# ---------------------------------------------------------------------------
# The audit trigger — FR-CRM-AUD-01, -02, -06
# ---------------------------------------------------------------------------


class TestAuditTrigger:
    def test_a_write_without_an_actor_is_refused(self, stack, workspace):
        message = _refused(
            stack,
            "INSERT INTO crm_fx_rates (workspace_id, currency, month, rate) "
            f"VALUES ({stack.q(workspace)}, 'USD', '2026-10-01', 83)",
            "CRM22",
        )
        assert message == "A CRM change must be made through the CRM."

    def test_crm_apply_writes_the_audit_row(self, stack, workspace, person):
        who = person()
        done = _apply(
            stack,
            _actor(workspace, who, channel="mcp"),
            [
                {
                    "op": "insert",
                    "table": "crm_fx_rates",
                    "values": {"currency": "USD", "month": "2026-10-01", "rate": "83.25"},
                    "action": "fx_rate.set",
                }
            ],
        )
        [row] = _audit(stack, done[0]["id"])
        assert row["actor_id"] == who
        assert uuid.UUID(row["actor_id"])
        assert (row["actor_label"], row["channel"], row["action"]) == ("user", "mcp", "fx_rate.set")
        assert row["workspace_id"] == workspace
        assert row["after"]["currency"] == "USD"

    def test_an_update_records_only_what_changed(self, stack, workspace, person):
        who = person()
        rate = _apply(
            stack,
            _actor(workspace, who),
            [
                {
                    "op": "insert",
                    "table": "crm_fx_rates",
                    "values": {"currency": "AUD", "month": "2026-09-01", "rate": "54"},
                }
            ],
        )[0]["id"]
        _apply(
            stack,
            _actor(workspace, who),
            [{"op": "update", "table": "crm_fx_rates", "id": rate, "values": {"rate": "55"}}],
        )
        created, updated = _audit(stack, rate)
        assert (created["action"], updated["action"]) == ("fx_rate.created", "fx_rate.updated")
        assert (updated["before"], updated["after"]) == ({"rate": 54}, {"rate": 55})

    def test_allowed_domains_is_audited_under_the_workspace(self, stack, workspace, person):
        """crm_workspaces has no workspace_id column: the trigger uses its id."""
        _apply(
            stack,
            _actor(workspace, person(role="admin")),
            [
                {
                    "op": "update",
                    "table": "crm_workspaces",
                    "id": workspace,
                    "values": {"allowed_domains": ["example.com", "example.org"]},
                    "action": "workspace.allowed_domains",
                }
            ],
        )
        row = _audit(stack, workspace)[-1]
        assert (row["workspace_id"], row["action"]) == (workspace, "workspace.allowed_domains")
        assert row["after"] == {"allowed_domains": ["example.com", "example.org"]}

    def test_last_used_alone_needs_no_actor_and_writes_no_row(self, stack, workspace, person):
        membership = _member(stack, workspace, person(), "member")
        before = len(_audit(stack, membership))
        stack.sql(
            f"UPDATE crm_memberships SET last_used_at = now() WHERE id = {stack.q(membership)}"
        )
        assert len(_audit(stack, membership)) == before

    def test_any_other_membership_change_needs_an_actor(self, stack, workspace, person):
        membership = _member(stack, workspace, person(), "member")
        _refused(
            stack,
            f"UPDATE crm_memberships SET role = 'viewer' WHERE id = {stack.q(membership)}",
            "CRM22",
        )

    def test_reordering_a_list_is_audited(self, stack, workspace, person):
        """FR-CRM-LIST-01, AUD-01: an option's position is a settings change,
        not kanban order, so it needs an actor and writes a row."""
        who = person()
        option = _apply(
            stack,
            _actor(workspace, who),
            [
                {
                    "op": "insert",
                    "table": "crm_select_options",
                    "values": {"list_key": "channel", "label": "Moved", "position": 3},
                }
            ],
        )[0]["id"]
        _apply(
            stack,
            _actor(workspace, who),
            [
                {
                    "op": "update",
                    "table": "crm_select_options",
                    "id": option,
                    "values": {"position": 1},
                }
            ],
        )
        row = _audit(stack, option)[-1]
        assert (row["action"], row["actor_id"]) == ("option.updated", who)
        assert (row["before"], row["after"]) == ({"position": 3}, {"position": 1})
        message = _refused(
            stack,
            f"UPDATE crm_select_options SET position = 2 WHERE id = {stack.q(option)}",
            "CRM22",
        )
        assert message == "A CRM change must be made through the CRM."

    def test_deleting_a_profile_is_not_refused(self, stack, workspace, make_profile):
        """The cascade to its membership and the SET NULL on what it authored
        run with no actor; they are recorded as the system."""
        seller = make_profile(account_type="crm_only", role="user", logs_time=False)
        membership = _member(stack, workspace, seller, "member")
        option = _as_system(
            stack,
            "INSERT INTO crm_select_options (workspace_id, list_key, label, position, created_by) "
            f"VALUES ({stack.q(workspace)}, 'channel', 'Authored', 1, {stack.q(seller)}) "
            "RETURNING id",
        )[-1]["id"]

        stack.sql(f"DELETE FROM auth.users WHERE id = {stack.q(seller)}")

        assert not stack.sql(f"SELECT 1 FROM profiles WHERE id = {stack.q(seller)}")
        assert not stack.sql(f"SELECT 1 FROM crm_memberships WHERE id = {stack.q(membership)}")
        authored = stack.sql(
            f"SELECT created_by FROM crm_select_options WHERE id = {stack.q(option)}"
        )
        assert authored == [{"created_by": None}]
        gone = _audit(stack, membership)[-1]
        assert (gone["action"], gone["actor_label"], gone["actor_id"]) == (
            "membership.purged",
            "system",
            None,
        )
        cleared = _audit(stack, option)[-1]
        assert (cleared["actor_label"], cleared["after"]) == ("system", {"created_by": None})

    def test_a_direct_membership_delete_is_still_refused(self, stack, workspace, person):
        """Only a membership whose person is gone is let through without an actor."""
        membership = _member(stack, workspace, person(), "member")
        _refused(stack, f"DELETE FROM crm_memberships WHERE id = {stack.q(membership)}", "CRM22")

    def test_the_seeds_are_audited_as_system(self, stack):
        rows = stack.sql(
            "SELECT DISTINCT a.actor_label, a.actor_id, a.action FROM audit_log a "
            "JOIN crm_workspaces w ON w.id = a.workspace_id "
            "WHERE w.key IN ('labs', 'academy') AND a.action IN "
            "('workspace.created', 'option.created', 'setting.created')"
        )
        assert {r["action"] for r in rows} == {
            "workspace.created",
            "option.created",
            "setting.created",
        }
        assert {(r["actor_label"], r["actor_id"]) for r in rows} == {("system", None)}


# ---------------------------------------------------------------------------
# crm_apply — data-model §5.2
# ---------------------------------------------------------------------------


class TestApply:
    def test_another_workspaces_row_is_not_found(self, stack, workspace, person):
        """FR-CRM-WS-02: the id exists, but not in the actor's workspace."""
        labs = stack.sql("SELECT id FROM crm_workspaces WHERE key = 'labs'")[0]["id"]
        option = stack.sql(
            f"SELECT id FROM crm_select_options WHERE workspace_id = {stack.q(labs)} LIMIT 1"
        )[0]["id"]
        ops = [
            {"op": "update", "table": "crm_select_options", "id": option, "values": {"label": "X"}}
        ]
        message = _refused(
            stack,
            f"SELECT crm_apply({_j(stack, _actor(workspace, person()))}, {_j(stack, ops)})",
            "CRM04",
        )
        assert message == "No such record in this workspace."

    def test_an_insert_is_stamped_with_the_actors_workspace(self, stack, workspace, person):
        done = _apply(
            stack,
            _actor(workspace, person()),
            [
                {
                    "op": "insert",
                    "table": "crm_select_options",
                    "values": {"list_key": "channel", "label": "Mine", "position": 1},
                }
            ],
        )
        assert done[0]["row"]["workspace_id"] == workspace

    def test_a_workspace_id_in_the_values_is_refused(self, stack, workspace, person):
        labs = stack.sql("SELECT id FROM crm_workspaces WHERE key = 'labs'")[0]["id"]
        ops = [
            {
                "op": "insert",
                "table": "crm_select_options",
                "values": {
                    "list_key": "channel",
                    "label": "X",
                    "position": 1,
                    "workspace_id": labs,
                },
            }
        ]
        _refused(
            stack,
            f"SELECT crm_apply({_j(stack, _actor(workspace, person()))}, {_j(stack, ops)})",
            "CRM22",
        )

    @pytest.mark.parametrize("table", ["profiles", "audit_log", "crm_nothing"])
    def test_a_table_that_is_not_an_audited_crm_table_is_refused(
        self, stack, workspace, person, table
    ):
        ops = [{"op": "update", "table": table, "id": str(uuid.uuid4()), "values": {"x": 1}}]
        message = _refused(
            stack,
            f"SELECT crm_apply({_j(stack, _actor(workspace, person()))}, {_j(stack, ops)})",
            "CRM22",
        )
        assert message == "That is not a CRM table."

    def test_a_stale_edit_is_refused(self, stack, workspace, person):
        who = person()
        option = _apply(
            stack,
            _actor(workspace, who),
            [
                {
                    "op": "insert",
                    "table": "crm_select_options",
                    "values": {"list_key": "channel", "label": "Old", "position": 1},
                }
            ],
        )[0]["id"]
        ops = [
            {
                "op": "update",
                "table": "crm_select_options",
                "id": option,
                "values": {"label": "New"},
                "expect_updated_at": "2020-01-01T00:00:00+00:00",
            }
        ]
        _refused(
            stack,
            f"SELECT crm_apply({_j(stack, _actor(workspace, who))}, {_j(stack, ops)})",
            "CRM09",
        )

    def test_all_or_nothing(self, stack, workspace, person):
        """A failing op rolls back the ops before it, and their audit rows."""
        ops = [
            {
                "op": "insert",
                "table": "crm_select_options",
                "values": {"list_key": "channel", "label": "Rolled back", "position": 1},
            },
            {"op": "update", "table": "crm_select_options", "id": str(uuid.uuid4()), "values": {}},
        ]
        with pytest.raises(stack.SqlError):
            stack.sql(
                f"SELECT crm_apply({_j(stack, _actor(workspace, person()))}, {_j(stack, ops)})"
            )
        assert not stack.sql(
            f"SELECT 1 FROM crm_select_options WHERE workspace_id = {stack.q(workspace)}"
        )
        assert not stack.sql(
            f"SELECT 1 FROM audit_log WHERE workspace_id = {stack.q(workspace)} "
            "AND target_table = 'crm_select_options'"
        )

    def test_a_role_change_that_leaves_no_admin_is_refused(self, stack, workspace, person):
        who = person()
        only = _member(stack, workspace, who, "admin")
        ops = [
            {"op": "update", "table": "crm_memberships", "id": only, "values": {"role": "member"}}
        ]
        message = _refused(
            stack,
            f"SELECT crm_apply({_j(stack, _actor(workspace, who))}, {_j(stack, ops)})",
            "CRM22",
        )
        assert message == "A workspace needs at least one admin."

    @pytest.mark.parametrize("kind", ["insert", "upsert"])
    def test_a_membership_is_never_added_here(self, stack, workspace, person, kind):
        """Q-68, Q-75: only crm_invite grants access."""
        values = {"user_id": person(), "role": "admin"}
        ops = [{"op": kind, "table": "crm_memberships", "values": values}]
        message = _refused(
            stack,
            f"SELECT crm_apply({_j(stack, _actor(workspace, person(role='admin')))}, "
            f"{_j(stack, ops)})",
            "CRM22",
        )
        assert message == "Invite a member through the members page."

    def test_a_membership_is_never_moved_to_another_person(self, stack, workspace, person):
        who = person()
        _member(stack, workspace, who, "admin")
        membership = _member(stack, workspace, person(), "viewer")
        ops = [
            {
                "op": "update",
                "table": "crm_memberships",
                "id": membership,
                "values": {"user_id": person()},
            }
        ]
        message = _refused(
            stack,
            f"SELECT crm_apply({_j(stack, _actor(workspace, who))}, {_j(stack, ops)})",
            "CRM22",
        )
        assert message == "Invite a member through the members page."

    @pytest.mark.parametrize(
        "values", [{"key": "renamed"}, {"tva_linked": True}, {"has_entity": True}]
    )
    def test_only_a_workspaces_name_and_allow_list_change(self, stack, workspace, person, values):
        ops = [{"op": "update", "table": "crm_workspaces", "id": workspace, "values": values}]
        message = _refused(
            stack,
            f"SELECT crm_apply({_j(stack, _actor(workspace, person(role='admin')))}, "
            f"{_j(stack, ops)})",
            "CRM22",
        )
        assert message == "Only a workspace's name and allow-list can change here."

    def test_a_workspace_admin_cannot_change_the_allow_list(self, stack, workspace, person):
        """FR-CRM-ACC-06: the allow-list limits whom they may invite."""
        ops = [
            {
                "op": "update",
                "table": "crm_workspaces",
                "id": workspace,
                "values": {"allowed_domains": ["gmail.com"]},
            }
        ]
        message = _refused(
            stack,
            f"SELECT crm_apply({_j(stack, _actor(workspace, person()))}, {_j(stack, ops)})",
            "CRM22",
        )
        assert message == "Only a TVA admin can change the domain allow-list."

    def test_a_workspace_can_be_renamed(self, stack, workspace, person):
        name = f"Renamed {workspace[:8]}"
        _apply(
            stack,
            _actor(workspace, person()),
            [
                {
                    "op": "update",
                    "table": "crm_workspaces",
                    "id": workspace,
                    "values": {"name": name},
                }
            ],
        )
        assert _audit(stack, workspace)[-1]["after"] == {"name": name}

    def test_a_workspace_is_never_deleted_here(self, stack, workspace, person):
        ops = [{"op": "delete", "table": "crm_workspaces", "id": workspace}]
        _refused(
            stack,
            f"SELECT crm_apply({_j(stack, _actor(workspace, person(role='admin')))}, "
            f"{_j(stack, ops)})",
            "CRM22",
        )


# ---------------------------------------------------------------------------
# crm_remove_membership — FR-CRM-ACC-12, Q-44
# ---------------------------------------------------------------------------


class TestRemoveMembership:
    def _remove(self, stack, actor: dict, membership: str) -> str:
        return f"SELECT crm_remove_membership({_j(stack, actor)}, {stack.q(membership)}) AS r"

    def test_the_last_membership_deactivates_and_revokes_together(
        self, stack, workspace, person, make_profile
    ):
        admin = person()
        _member(stack, workspace, admin, "admin")
        seller = make_profile(keep=True, account_type="crm_only", role="user", logs_time=False)
        membership = _member(stack, workspace, seller, "member")
        stack.sql(
            "INSERT INTO api_tokens (user_id, name, token_hash, prefix, expires_at) VALUES "
            f"({stack.q(seller)}, 'claude', {stack.q(uuid.uuid4().hex)}, 'nunp_x', "
            "now() + interval '30 days')"
        )

        result = stack.sql(self._remove(stack, _actor(workspace, admin), membership))[0]["r"]

        assert result == {"deactivated": True}
        profile = _profile(stack, seller)
        assert (profile["is_active"], profile["deactivated_reason"]) == (
            False,
            "crm_no_membership",
        )
        tokens = stack.sql(f"SELECT revoked_at FROM api_tokens WHERE user_id = {stack.q(seller)}")
        assert tokens and all(t["revoked_at"] for t in tokens)
        actions = [r["action"] for r in _audit(stack, membership)]
        assert actions[-1] == "membership.removed"
        tva_rows = stack.sql(
            "SELECT action, actor_label, workspace_id FROM audit_log "
            f"WHERE target_id = {stack.q(seller)} ORDER BY id"
        )
        assert [(r["action"], r["actor_label"], r["workspace_id"]) for r in tva_rows] == [
            # The admin's own kind: a `system` row would read "(via CRM deal)".
            ("user.deactivated", "user", None),
            ("token.revoked", "user", None),
        ]
        assert {r["actor_id"] for r in _audit(stack, seller)} == {admin}

    def test_a_staff_member_keeps_their_account(self, stack, workspace, person):
        admin = person()
        _member(stack, workspace, admin, "admin")
        staff = person()
        membership = _member(stack, workspace, staff, "member")
        result = stack.sql(self._remove(stack, _actor(workspace, admin), membership))[0]["r"]
        assert result == {"deactivated": False}
        assert _profile(stack, staff)["is_active"] is True

    def test_the_last_admin_cannot_be_removed(self, stack, workspace, person):
        admin = person()
        only = _member(stack, workspace, admin, "admin")
        message = _refused(stack, self._remove(stack, _actor(workspace, admin), only), "CRM22")
        assert message == "A workspace needs at least one admin."

    def test_two_admins_removing_each_other_at_once_leave_one(self, stack, workspace, person):
        a, b = person(), person()
        ma = _member(stack, workspace, a, "admin")
        mb = _member(stack, workspace, b, "admin")
        demote = [
            {"op": "update", "table": "crm_memberships", "id": ma, "values": {"role": "member"}}
        ]
        first = (
            f"BEGIN; SELECT crm_apply({_j(stack, _actor(workspace, b))}, {_j(stack, demote)}); "
            "SELECT pg_sleep(1.5); COMMIT;"
        )
        refused: list[str] = []

        def second():
            refused.append(_refused(stack, self._remove(stack, _actor(workspace, a), mb), "CRM22"))

        _at_once(first, second, stack)
        assert refused == ["A workspace needs at least one admin."]
        admins = stack.sql(
            f"SELECT id FROM crm_memberships WHERE workspace_id = {stack.q(workspace)} "
            "AND role = 'admin'"
        )
        assert [r["id"] for r in admins] == [mb]

    def test_a_workspace_admin_cannot_remove_a_tva_admin(self, stack, workspace, person):
        admin = person()
        _member(stack, workspace, admin, "admin")
        tva_admin = person(role="admin")
        membership = _member(stack, workspace, tva_admin, "member")
        message = _refused(
            stack, self._remove(stack, _actor(workspace, admin), membership), "CRM22"
        )
        assert message == "Ask a TVA admin to change this membership."

    def test_another_workspaces_membership_is_not_found(self, stack, workspace, person):
        admin = person()
        _member(stack, workspace, admin, "admin")
        labs = stack.sql("SELECT id FROM crm_workspaces WHERE key = 'labs'")[0]["id"]
        elsewhere = _member(stack, labs, person(), "viewer")
        _refused(stack, self._remove(stack, _actor(workspace, admin), elsewhere), "CRM04")


# ---------------------------------------------------------------------------
# crm_invite — FR-CRM-ACC-04, Q-75
# ---------------------------------------------------------------------------


class TestInvite:
    def _invite(self, stack, actor: dict, user: str, **plan) -> str:
        body = {"user_id": user, "role": "member", **plan}
        return f"SELECT crm_invite({_j(stack, actor)}, {_j(stack, body)}) AS r"

    def _inactive_seller(self, make_profile, reason: str) -> str:
        return make_profile(
            keep=True,
            account_type="crm_only",
            role="user",
            logs_time=False,
            is_active=False,
            deactivated_reason=reason,
        )

    def test_creates_a_crm_only_profile_and_membership(self, stack, workspace, person):
        admin = person()
        uid = str(uuid.uuid4())
        email = f"crm-db-invite-{uid[:8]}@example.com"
        stack.sql(
            "INSERT INTO auth.users (id, instance_id, aud, role, email) VALUES "
            f"({stack.q(uid)}, '00000000-0000-0000-0000-000000000000', 'authenticated', "
            f"'authenticated', {stack.q(email)})"
        )
        result = stack.sql(
            self._invite(
                stack,
                _actor(workspace, admin),
                uid,
                email=email,
                display_name="Rep",
                timezone="Australia/Sydney",
                create_profile=True,
            )
        )[0]["r"]
        assert result["created_profile"] is True
        profile = _profile(stack, uid)
        assert (profile["account_type"], profile["logs_time"], profile["timezone"]) == (
            "crm_only",
            False,
            "Australia/Sydney",
        )
        [row] = _audit(stack, result["membership_id"])
        assert (row["action"], row["workspace_id"]) == ("membership.invited", workspace)

    def test_a_workspace_admin_reactivates_only_a_lost_membership(
        self, stack, workspace, person, make_profile
    ):
        admin = person()
        lost = self._inactive_seller(make_profile, "crm_no_membership")
        result = stack.sql(self._invite(stack, _actor(workspace, admin), lost, reactivate=True))
        assert result[0]["r"]["reactivated"] is True
        profile = _profile(stack, lost)
        assert (profile["is_active"], profile["deactivated_reason"]) == (True, None)

    def test_a_workspace_admin_cannot_undo_an_offboarding(
        self, stack, workspace, person, make_profile
    ):
        admin = person()
        offboarded = self._inactive_seller(make_profile, "admin")
        message = _refused(
            stack,
            self._invite(stack, _actor(workspace, admin), offboarded, reactivate=True),
            "CRM22",
        )
        assert message == "This person's portal account is inactive; ask a TVA admin."
        assert _profile(stack, offboarded)["is_active"] is False

    def test_a_tva_admin_may(self, stack, workspace, person, make_profile):
        tva_admin = person(role="admin")
        offboarded = self._inactive_seller(make_profile, "admin")
        stack.sql(self._invite(stack, _actor(workspace, tva_admin), offboarded, reactivate=True))
        assert _profile(stack, offboarded)["is_active"] is True

    def test_an_inactive_staff_account_is_never_reopened_here(self, stack, workspace, person):
        tva_admin = person(role="admin")
        staff = person(is_active=False)
        _refused(
            stack,
            self._invite(stack, _actor(workspace, tva_admin), staff, reactivate=True),
            "CRM22",
        )

    def test_an_existing_member_is_a_conflict(self, stack, workspace, person):
        admin = person()
        staff = person()
        _member(stack, workspace, staff, "viewer")
        _refused(stack, self._invite(stack, _actor(workspace, admin), staff), "23505")


# ---------------------------------------------------------------------------
# crm_convert_account — FR-CRM-ACC-14, FR-CRM-CHG-02
# ---------------------------------------------------------------------------


class TestConvertAccount:
    def _convert(self, stack, actor: str, user: str, to: str = "crm_only") -> str:
        body = {"user_id": actor, "kind": "user", "channel": "web", "workspace_id": None}
        return f"SELECT crm_convert_account({_j(stack, body)}, {stack.q(user)}, {stack.q(to)}) AS r"

    def test_clears_every_tva_field_together(self, stack, person):
        tva_admin = person(role="admin")
        staff = person(role="lead", logs_time=True, dashboard_access=True)
        stack.sql(self._convert(stack, tva_admin, staff))
        profile = _profile(stack, staff)
        assert (
            profile["account_type"],
            profile["role"],
            profile["logs_time"],
            profile["dashboard_access"],
            profile["lead_id"],
        ) == ("crm_only", "user", False, False, None)
        row = stack.sql(
            "SELECT action, after FROM audit_log WHERE target_id = "
            f"{stack.q(staff)} ORDER BY id DESC LIMIT 1"
        )[0]
        assert row["action"] == "user.account_type"
        assert row["after"]["account_type"] == "crm_only"

    def test_refuses_the_owner(self, stack, person):
        owner = stack.sql("SELECT id FROM profiles WHERE is_owner")
        made = not owner
        # A fresh local database has no owner (022 names one by email); make
        # one for the test and hand the flag back after it.
        uid = owner[0]["id"] if owner else person(is_owner=True)
        try:
            message = _refused(stack, self._convert(stack, person(role="admin"), uid), "CRM22")
        finally:
            if made:
                stack.sql(f"UPDATE profiles SET is_owner = false WHERE id = {stack.q(uid)}")
        assert message == "The owner's account cannot be CRM-only."

    def test_refuses_a_lead_of_people(self, stack, person):
        lead = person(role="lead")
        person(lead_id=lead)
        message = _refused(stack, self._convert(stack, person(role="admin"), lead), "CRM22")
        assert "lead" in message

    def test_refuses_when_an_allocation_commits_during_the_change(self, stack, person):
        tva_admin = person(role="admin")
        staff = person()
        project = stack.sql(
            f"INSERT INTO projects (name) VALUES ({stack.q('CRM race ' + staff[:8])}) RETURNING id"
        )[0]["id"]
        first = (
            "BEGIN; INSERT INTO allocations (project_id, user_id, starts_on, ends_on, percent) "
            f"VALUES ({stack.q(project)}, {stack.q(staff)}, '2026-10-01', '2026-10-31', 50); "
            "SELECT pg_sleep(1.5); COMMIT;"
        )
        refused: list[str] = []

        def second():
            refused.append(_refused(stack, self._convert(stack, tva_admin, staff), "CRM22"))

        _at_once(first, second, stack)
        assert refused == ["This person is allocated to a project; remove the allocation first."]
        assert _profile(stack, staff)["account_type"] == "staff"

    def test_back_to_staff(self, stack, person, make_profile):
        seller = make_profile(keep=True, account_type="crm_only", role="user", logs_time=False)
        stack.sql(self._convert(stack, person(role="admin"), seller, "staff"))
        assert _profile(stack, seller)["account_type"] == "staff"


# ---------------------------------------------------------------------------
# The browser — FR-CRM-ACC-10, data-model §5.12
# ---------------------------------------------------------------------------


def _crm_tables(stack) -> list[str]:
    rows = stack.sql(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
        "AND tablename LIKE 'crm\\_%' ORDER BY 1"
    )
    return [r["tablename"] for r in rows]


class TestBrowserRoles:
    def test_the_crm_tables_exist(self, stack):
        assert {
            "crm_workspaces",
            "crm_memberships",
            "crm_workspace_settings",
            "crm_select_options",
            "crm_fx_rates",
        } <= set(_crm_tables(stack))

    def test_every_crm_table_has_rls_and_no_browser_grant(self, stack):
        for table in _crm_tables(stack):
            row = stack.sql(
                "SELECT c.relrowsecurity AS rls, "
                f"has_table_privilege('anon', 'public.{table}', 'SELECT') AS anon, "
                f"has_table_privilege('authenticated', 'public.{table}', "
                "'SELECT,INSERT,UPDATE,DELETE') AS authed "
                f"FROM pg_class c WHERE c.oid = 'public.{table}'::regclass"
            )[0]
            assert row == {"rls": True, "anon": False, "authed": False}, table

    def test_no_crm_function_is_executable_by_the_browser(self, stack):
        rows = stack.sql(
            "SELECT p.oid::regprocedure::text AS fn, "
            "has_function_privilege('anon', p.oid, 'EXECUTE') AS anon, "
            "has_function_privilege('authenticated', p.oid, 'EXECUTE') AS authed "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.proname LIKE 'crm\\_%'"
        )
        assert {r["fn"].split("(")[0] for r in rows} >= {
            "crm_apply",
            "crm_audit_row",
            "crm_check_refs",
            "crm_remove_membership",
            "crm_invite",
            "crm_convert_account",
        }
        assert [r["fn"] for r in rows if r["anon"] or r["authed"]] == []

    def test_crm_apply_over_postgrest_is_refused_to_a_session(self, stack, seller):
        r = stack.http.post(
            f"{stack.url}/rest/v1/rpc/crm_apply",
            json={"p_actor": {}, "p_ops": []},
            headers={"apikey": stack.anon_key, "Authorization": f"Bearer {stack.jwt_for(seller)}"},
        )
        assert r.status_code in (401, 403, 404)
