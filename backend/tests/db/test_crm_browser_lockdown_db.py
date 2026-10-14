"""The browser can read no table — spec 007 data-model §4.0, §5.10;
constitution §I; FR-CRM-CHG-02.

A CRM-only seller holds a real session JWT, and the anon key is public. 025
revokes every table privilege in `public` from `anon` and `authenticated`, so
that pair reads nothing through PostgREST: every table answers a permission
error, and an empty `200` counts as open. The tables are enumerated from information_schema, so a
table added later is checked without anyone remembering to list it.

`tva_staff_only()` is the database's backstop behind the Python refusals: a
CRM-only account cannot be allocated, lead a project, log time or be anyone's
lead, whichever path writes the row.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


def _public_tables(stack) -> list[str]:
    rows = stack.sql(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' ORDER BY table_name"
    )
    return [r["table_name"] for r in rows]


class TestBrowserLockdown:
    def test_the_enumeration_finds_the_tva_tables(self, stack):
        tables = _public_tables(stack)
        assert {"profiles", "projects", "allocations", "time_entries", "audit_log"} <= set(tables)

    @pytest.mark.parametrize("who", ["crm_only_jwt", "anon_key_alone"])
    def test_every_public_table_is_closed_to_the_browser(self, stack, seller, who):
        bearer = stack.jwt_for(seller) if who == "crm_only_jwt" else stack.anon_key
        open_tables = {}
        for table in _public_tables(stack):
            r = stack.rest(table, bearer=bearer)
            # Only the permission error counts as closed. `200 []` is a table
            # that answered: after a reset most tables are empty, so an empty
            # answer cannot tell a revoked table from a re-granted one.
            if r.status_code in (401, 403) and r.json().get("code") == "42501":
                continue
            open_tables[table] = (r.status_code, r.text[:200])
        assert open_tables == {}

    def test_a_table_with_rows_is_refused_not_merely_empty(self, stack, seller):
        assert stack.sql("SELECT count(*) AS n FROM app_settings")[0]["n"] > 0
        r = stack.rest("app_settings", bearer=stack.jwt_for(seller))
        assert r.status_code in (401, 403)
        assert r.json()["code"] == "42501"


@pytest.fixture
def project(stack):
    pid = stack.sql("INSERT INTO projects (name) VALUES ('CRM db test') RETURNING id")[0]["id"]
    yield pid
    stack.sql(f"DELETE FROM allocations WHERE project_id = {stack.q(pid)}")
    stack.sql(f"DELETE FROM projects WHERE id = {stack.q(pid)}")


@pytest.fixture
def staff(make_profile) -> str:
    return make_profile()


def _refused(stack, query: str) -> str:
    with pytest.raises(stack.SqlError) as raised:
        stack.sql(query)
    assert raised.value.code == "CRM22"
    return raised.value.message


class TestTvaStaffOnly:
    def test_allocation_insert(self, stack, seller, project):
        message = _refused(
            stack,
            "INSERT INTO allocations (project_id, user_id, starts_on, ends_on, percent) "
            f"VALUES ({stack.q(project)}, {stack.q(seller)}, '2026-10-01', '2026-10-31', 50)",
        )
        assert message == "A CRM-only account cannot be allocated."

    def test_allocation_moved_to_a_crm_only_account(self, stack, seller, staff, project):
        aid = stack.sql(
            "INSERT INTO allocations (project_id, user_id, starts_on, ends_on, percent) "
            f"VALUES ({stack.q(project)}, {stack.q(staff)}, '2026-10-01', '2026-10-31', 50) RETURNING id"
        )[0]["id"]
        message = _refused(
            stack, f"UPDATE allocations SET user_id = {stack.q(seller)} WHERE id = {stack.q(aid)}"
        )
        assert message == "A CRM-only account cannot be allocated."

    def test_project_lead_on_insert(self, stack, seller):
        message = _refused(
            stack, f"INSERT INTO projects (name, lead_id) VALUES ('CRM db lead', {stack.q(seller)})"
        )
        assert message == "A CRM-only account cannot lead a project."

    def test_project_lead_on_update(self, stack, seller, project):
        message = _refused(
            stack, f"UPDATE projects SET lead_id = {stack.q(seller)} WHERE id = {stack.q(project)}"
        )
        assert message == "A CRM-only account cannot lead a project."

    def test_time_entry(self, stack, seller, project):
        message = _refused(
            stack,
            "INSERT INTO time_entries (user_id, date, project_id, hours_office) "
            f"VALUES ({stack.q(seller)}, '2026-10-01', {stack.q(project)}, 1)",
        )
        assert message == "A CRM-only account cannot log time."

    def test_profile_lead(self, stack, seller, staff):
        message = _refused(
            stack, f"UPDATE profiles SET lead_id = {stack.q(seller)} WHERE id = {stack.q(staff)}"
        )
        assert message == "A CRM-only account cannot be anyone's lead."

    def test_staff_are_untouched(self, stack, staff, project):
        stack.sql(f"UPDATE projects SET lead_id = {stack.q(staff)} WHERE id = {stack.q(project)}")
        stack.sql(
            "INSERT INTO allocations (project_id, user_id, starts_on, ends_on, percent) "
            f"VALUES ({stack.q(project)}, {stack.q(staff)}, '2026-10-01', '2026-10-31', 50)"
        )


class TestProfileShape:
    @pytest.mark.parametrize(
        "change",
        ["role = 'lead'", "logs_time = true", "dashboard_access = true"],
    )
    def test_a_crm_only_profile_keeps_its_shape(self, stack, seller, change):
        with pytest.raises(stack.SqlError) as raised:
            stack.sql(f"UPDATE profiles SET {change} WHERE id = {stack.q(seller)}")
        assert raised.value.code == "23514"

    def test_a_deactivation_reason_needs_an_inactive_account(self, stack, seller):
        with pytest.raises(stack.SqlError) as raised:
            stack.sql(
                f"UPDATE profiles SET deactivated_reason = 'admin' WHERE id = {stack.q(seller)}"
            )
        assert raised.value.code == "23514"
        stack.sql(
            "UPDATE profiles SET is_active = false, deactivated_reason = 'admin' "
            f"WHERE id = {stack.q(seller)}"
        )

    def test_existing_profiles_are_staff(self, stack):
        # seed.sql's two CRM-only sellers (S1c) are `*.crm@…`; everyone else
        # it seeds predates the column and defaulted to staff.
        rows = stack.sql(
            "SELECT DISTINCT account_type FROM profiles "
            "WHERE email NOT LIKE 'crm-db-%' AND email NOT LIKE '%.crm@%'"
        )
        assert {r["account_type"] for r in rows} <= {"staff"}
