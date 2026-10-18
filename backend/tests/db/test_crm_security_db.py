"""The database's own security, swept — spec 007 FR-CRM-ACC-10, FR-CRM-WS-02,
FR-CRM-AUD-01; constitution §I; data-model §2 ("References inside the
workspace"), §5.12, §5.14; D3.

Read from the catalogue (`pg_class`, `pg_proc`, `pg_trigger`,
`pg_constraint`), so a table, function or reference added later is checked
without anyone remembering to list it:

- every table in `public`, TVA's included, has row-level security on and no
  privilege at all for `anon` or `authenticated` (the browser reads nothing);
- no CRM function can be executed by `anon` or `authenticated`;
- every CRM record and config table carries the audit trigger; the logs
  that do not are written out below with why;
- every reference between `crm_*` tables is a composite foreign key on
  `(workspace_id, …)`, or, where it cannot be one (a polymorphic record id,
  an array of options), the table carries `crm_check_refs()` for it.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.db

#: CRM tables with no audit trigger (data-model §3): logs the system writes,
#: each with its own record of who and when, and the static field registry.
NOT_AUDITED = {
    "crm_stage_history",  # a deal's moves; the move itself is audited on the deal
    "crm_import_runs",  # audited through crm_record_import's own audit row
    "crm_job_runs",  # the job log (FR-CRM-JOB-01)
    "crm_notifications",  # the bell (FR-CRM-NOTIF-01)
    "crm_digest_sends",  # sent markers (FR-CRM-NOTIF-03)
    "crm_email_messages",  # the email log (FR-CRM-SYNC-04)
    "crm_email_links",  # the email log's links
    "crm_object_fields",  # the field registry, written by migrations only
    "crm_ai_usage",  # the AI usage log, cost per call, no text (009 FR-CRMAI-LLM-06)
    "crm_review_items",  # the Review Queue; decisions audited by its own functions (009 Q-34)
    "crm_review_item_deals",  # a Review Queue item's deal choices, ids only (009 Q-07)
    "crm_email_summaries",  # AI thread summaries, rewritten each message, removable (009 Q-34)
    "crm_email_summary_links",  # an email summary's links to its records (009 FR-CRMAI-SUM-05)
    "crm_company_enrichment",  # AI values beside a company's fields, re-read often (009 ENR-06)
}
#: Polymorphic references, checked by crm_check_refs() (data-model §5.14).
POLYMORPHIC = {"record_id", "target_id"}
#: 008 (047): the CRM's task tables, renamed and shared; every sweep below
#: covers them as CRM tables.
TASK_TABLES = "('tasks', 'task_links')"
#: 008 (047, FR-TASK-DATA-09, -15): the two plain references among them, by
#: design — a link's task and a subtask's parent, which may have no workspace.
PLAIN_TASK_KEYS = {"task_links_task_id", "tasks_parent_id_fkey"}
#: 008 (053, FR-TASK-RRULE-03): a recurring task's series key, the first
#: occurrence's id, which stays the series' name after that task is purged;
#: a key, not a reference.
SERIES_KEYS = {("tasks", "series_id")}


def _tables(stack, like: str = "%") -> list[str]:
    rows = stack.sql(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        f"WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relname LIKE {stack.q(like)} "
        "ORDER BY 1"
    )
    return [r["relname"] for r in rows]


class TestEveryTable:
    def test_the_sweep_sees_both_tva_and_crm(self, stack):
        tables = set(_tables(stack))
        assert {"profiles", "projects", "audit_log", "crm_companies", "crm_notifications"} <= tables

    def test_row_level_security_is_on(self, stack):
        rows = stack.sql(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relrowsecurity"
        )
        assert rows == []

    @pytest.mark.parametrize("role", ["anon", "authenticated"])
    def test_no_privilege_for_the_browser(self, stack, role):
        rows = stack.sql(
            "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
            f"WHERE table_schema = 'public' AND grantee IN ({stack.q(role)}, 'PUBLIC')"
        )
        assert rows == []
        # And through any route a grant could take (a role it inherits).
        rows = stack.sql(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm') AND ("
            + " OR ".join(
                f"has_table_privilege({stack.q(role)}, c.oid, '{p}')"
                for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
            )
            + ")"
        )
        assert rows == []


class TestEveryCrmFunction:
    def test_there_are_functions_to_check(self, stack):
        rows = stack.sql(
            "SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.proname LIKE 'crm\\_%'"
        )
        assert rows[0]["n"] > 20

    @pytest.mark.parametrize("role", ["anon", "authenticated"])
    def test_execute_is_denied(self, stack, role):
        rows = stack.sql(
            "SELECT p.oid::regprocedure::text AS fn FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.proname LIKE 'crm\\_%' "
            f"AND has_function_privilege({stack.q(role)}, p.oid, 'EXECUTE')"
        )
        assert rows == []

    def test_the_browser_cannot_call_one_through_postgrest(self, stack, seller):
        r = stack.http.post(
            f"{stack.url}/rest/v1/rpc/crm_job_latest",
            json={},
            headers={"apikey": stack.anon_key, "Authorization": f"Bearer {stack.jwt_for(seller)}"},
        )
        assert r.status_code in (401, 403, 404), r.text


class TestTheAuditTrigger:
    def test_every_record_and_config_table_has_it(self, stack):
        rows = stack.sql(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' "
            f"AND (c.relname LIKE 'crm\\_%' OR c.relname IN {TASK_TABLES}) "
            "AND NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid = c.oid "
            "AND t.tgfoid = 'public.crm_audit_row'::regproc AND NOT t.tgisinternal)"
        )
        assert {r["relname"] for r in rows} == NOT_AUDITED

    def test_it_fires_on_every_change(self, stack):
        rows = stack.sql(
            "SELECT t.tgrelid::regclass::text AS t, t.tgtype FROM pg_trigger t "
            "WHERE t.tgfoid = 'public.crm_audit_row'::regproc AND NOT t.tgisinternal"
        )
        assert rows
        # tgtype bits: 1 row, 4 insert, 8 delete, 16 update.
        for row in rows:
            assert row["tgtype"] & (1 | 4 | 8 | 16) == (1 | 4 | 8 | 16), row["t"]


class TestReferencesStayInTheWorkspace:
    def test_every_foreign_key_between_crm_tables_is_composite(self, stack):
        rows = stack.sql(
            "SELECT con.conname, "
            "(SELECT array_agg(a.attname::text ORDER BY k.i) FROM unnest(con.conkey) "
            "WITH ORDINALITY k(n, i) JOIN pg_attribute a "
            "ON a.attrelid = con.conrelid AND a.attnum = k.n) AS cols, "
            "(SELECT array_agg(a.attname::text ORDER BY k.i) FROM unnest(con.confkey) "
            "WITH ORDINALITY k(n, i) JOIN pg_attribute a "
            "ON a.attrelid = con.confrelid AND a.attnum = k.n) AS refs, "
            "con.confrelid::regclass::text AS target "
            "FROM pg_constraint con WHERE con.contype = 'f' "
            "AND (con.conrelid::regclass::text LIKE 'crm\\_%' "
            f"OR con.conrelid::regclass::text IN {TASK_TABLES}) "
            "AND (con.confrelid::regclass::text LIKE 'crm\\_%' "
            f"OR con.confrelid::regclass::text IN {TASK_TABLES})"
        )
        assert len(rows) > 30
        loose = []
        for row in rows:
            if row["target"] == "crm_workspaces":
                # The workspace column itself.
                assert row["cols"] == ["workspace_id"], row["conname"]
                continue
            if row["cols"][0] != "workspace_id" or row["refs"][0] != "workspace_id":
                loose.append(row["conname"])
        assert set(loose) == PLAIN_TASK_KEYS
        assert "task_links_task" in {r["conname"] for r in rows}

    def test_every_other_reference_carries_crm_check_refs(self, stack):
        """A uuid `*_id` column of a CRM table with no foreign key is a
        polymorphic reference; its table must carry the trigger."""
        rows = stack.sql(
            "SELECT c.table_name, c.column_name FROM information_schema.columns c "
            "WHERE c.table_schema = 'public' "
            f"AND (c.table_name LIKE 'crm\\_%' OR c.table_name IN {TASK_TABLES}) "
            "AND c.udt_name = 'uuid' AND c.column_name LIKE '%\\_id' "
            "AND c.column_name NOT IN ('id', 'workspace_id') "
            "AND NOT EXISTS (SELECT 1 FROM pg_constraint con JOIN pg_attribute a "
            "ON a.attrelid = con.conrelid AND a.attnum = ANY (con.conkey) "
            "WHERE con.contype = 'f' AND con.conrelid = c.table_name::regclass "
            "AND a.attname = c.column_name)"
        )
        assert rows, "the sweep found no polymorphic reference to check"
        checked = {
            r["t"]
            for r in stack.sql(
                "SELECT tgrelid::regclass::text AS t FROM pg_trigger "
                "WHERE tgfoid = 'public.crm_check_refs'::regproc AND NOT tgisinternal"
            )
        }
        unchecked = [
            (r["table_name"], r["column_name"])
            for r in rows
            if (r["table_name"], r["column_name"]) not in SERIES_KEYS
            and (r["column_name"] not in POLYMORPHIC or r["table_name"] not in checked)
        ]
        assert unchecked == []

    def test_every_option_array_carries_crm_check_refs_for_it(self, stack):
        arrays = stack.sql(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name LIKE 'crm\\_%' AND udt_name = '_uuid'"
        )
        assert arrays
        for a in arrays:
            args = stack.sql(
                "SELECT encode(tgargs, 'escape') AS args FROM pg_trigger "
                f"WHERE tgrelid = {stack.q(a['table_name'])}::regclass "
                "AND tgfoid = 'public.crm_check_refs'::regproc"
            )
            assert any(a["column_name"] in r["args"] for r in args), a


def _as_system(stack, statement: str) -> list[dict]:
    actor = '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}'
    return stack.sql(f"SELECT set_config('crm.actor', {stack.q(actor)}, true); {statement}")


class TestTheBellNamesOnlyItsOwnWorkspace:
    """044 — found by this sweep: a notification's record is held to its
    workspace, and a notification of a purged record can still be read."""

    @pytest.fixture
    def two(self, stack, make_profile):
        owner = make_profile(keep=True)
        made = []
        for _ in range(2):
            key = f"sec{uuid.uuid4().hex[:8]}"
            ws = _as_system(
                stack,
                "INSERT INTO crm_workspaces (key, name) "
                f"VALUES ({stack.q(key)}, {stack.q('Security ' + key)}) RETURNING id",
            )[-1]["id"]
            _as_system(
                stack,
                "INSERT INTO crm_memberships (workspace_id, user_id, role) "
                f"VALUES ({stack.q(ws)}, {stack.q(owner)}, 'admin')",
            )
            company = _as_system(
                stack,
                "INSERT INTO crm_companies (workspace_id, name, owner_id) "
                f"VALUES ({stack.q(ws)}, 'Security Co', {stack.q(owner)}) RETURNING id",
            )[-1]["id"]
            made.append((ws, company))
        return owner, made

    def _notify(self, stack, owner, ws, company) -> list:
        return stack.sql(
            "SELECT crm_notify(jsonb_build_array(jsonb_build_object("
            f"'workspace_id', {stack.q(ws)}, 'user_id', {stack.q(owner)}, 'kind', 'assigned', "
            f"'record_type', 'company', 'record_id', {stack.q(company)}, 'title', 'Security Co', "
            f"'dedupe_key', {stack.q('sec:' + uuid.uuid4().hex)}))) AS r"
        )[0]["r"]

    def test_another_workspaces_record_is_refused(self, stack, two):
        owner, [(ws_a, _), (_, company_b)] = two
        with pytest.raises(stack.SqlError) as refused:
            self._notify(stack, owner, ws_a, company_b)
        assert refused.value.code == "CRM04"

    def test_its_own_is_kept_and_stays_readable_after_a_purge(self, stack, two):
        owner, [(ws_a, company_a), _] = two
        [note] = self._notify(stack, owner, ws_a, company_a)
        _as_system(stack, f"DELETE FROM crm_companies WHERE id = {stack.q(company_a)}")
        stack.sql(f"UPDATE crm_notifications SET read_at = now() WHERE id = {stack.q(note['id'])}")
        moved = f"UPDATE crm_notifications SET record_id = gen_random_uuid() WHERE id = {stack.q(note['id'])}"
        with pytest.raises(stack.SqlError) as refused:
            stack.sql(moved)
        assert refused.value.code == "CRM04"
