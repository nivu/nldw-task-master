"""Company enrichment in the real database — spec 009 FR-CRMAI-ENR-01, -03,
-06 to -08, FR-CRMAI-CHG-11 (enrichment); Q-32, Q-33; data-model §3.5, §4;
migration 064.

What only Postgres can prove: 064's trigger queues a company created
through `crm_apply`, from the web (actor kind `user`) and from an import
(kind `import`, the Twenty upsert), from its website or from its primary
domain, and queues it again when the website changes, never for the same
site; it writes no audit row and leaves the company's `updated_at` alone;
the site it computes is the one `domain/crm/enrichment.site()` computes for
Enrich selected; the table's CHECKs hold the fixed failure reasons; the
store wrappers read the queue and write only a row still queued for its
site; and `crm_merge_companies` keeps the survivor's enrichment, or moves the
merged company's. RLS, grants, EXECUTE and the table's place in
`NOT_AUDITED` are checked by `test_crm_security_db.py`.

The store wrappers of `services/crm/store.py` run against the local stack.
Each test works in a workspace of its own.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from supabase import create_client

from app.domain.crm import enrichment as rules
from app.services.crm import store

pytestmark = pytest.mark.db

SYSTEM = {"kind": "system", "user_id": None, "channel": None, "workspace_id": None}


def _j(stack, value) -> str:
    return f"{stack.q(json.dumps(value))}::jsonb"


def _as_system(stack, statement: str) -> list[dict]:
    return stack.sql(
        f"SELECT set_config('crm.actor', {_j(stack, SYSTEM)}::text, true); {statement}"
    )


def _actor(ws: str, owner: str, kind: str = "user") -> dict:
    return {"user_id": owner, "kind": kind, "channel": "web", "workspace_id": ws}


def _apply(stack, actor: dict, ops: list[dict]) -> list[dict]:
    return stack.sql(f"SELECT crm_apply({_j(stack, actor)}, {_j(stack, ops)}) AS r")[0]["r"]


@pytest.fixture
def owner(make_profile) -> str:
    return make_profile(keep=True)


@pytest.fixture
def ws(stack, owner) -> str:
    key = f"e{uuid.uuid4().hex[:12]}"
    ws = _as_system(
        stack,
        "INSERT INTO crm_workspaces (key, name) "
        f"VALUES ({stack.q(key)}, {stack.q('Enrichment ' + key)}) RETURNING id",
    )[-1]["id"]
    _as_system(
        stack,
        "INSERT INTO crm_memberships (workspace_id, user_id, role) "
        f"VALUES ({stack.q(ws)}, {stack.q(owner)}, 'admin')",
    )
    return ws


@pytest.fixture
def real(stack, monkeypatch):
    """The real store against the local stack."""
    monkeypatch.setattr(store, "supabase", create_client(stack.url, stack.service_key))
    return store


def _host() -> str:
    return f"acme{uuid.uuid4().hex[:8]}.in"


def _company(stack, ws, owner, *, url=None, domain=None, kind="user", name="Acme") -> str:
    values = {"name": name, "owner_id": owner}
    if url:
        values["website"] = {"primary": {"url": url}, "others": []}
    ops = [{"op": "insert", "table": "crm_companies", "ref": "c", "values": values}]
    if domain:
        ops.append(
            {
                "op": "insert",
                "table": "crm_company_domains",
                "values": {"company_id": "$ref:c", "domain": domain, "is_primary": True},
            }
        )
    return _apply(stack, _actor(ws, owner, kind), ops)[0]["id"]


def _row(stack, company: str) -> dict | None:
    rows = stack.sql(
        'SELECT website, status, failure_reason, "values", queued_at, requested_by '
        f"FROM crm_company_enrichment WHERE company_id = {stack.q(company)}"
    )
    return rows[0] if rows else None


def _set_website(stack, ws, owner, company, url) -> None:
    _apply(
        stack,
        _actor(ws, owner),
        [
            {
                "op": "update",
                "table": "crm_companies",
                "id": company,
                "values": {"website": {"primary": {"url": url}, "others": []}},
            }
        ],
    )


def _done(stack, company: str) -> None:
    stack.sql(
        "UPDATE crm_company_enrichment SET status = 'done', "
        '"values" = \'{"city": "Coimbatore"}\', model = \'claude-sonnet-5-5\', '
        f"generated_at = now() WHERE company_id = {stack.q(company)}"
    )


class TestTheTriggerQueues:
    """FR-CRMAI-ENR-01, -07; data-model §3.5."""

    def test_enrichment_db_a_web_insert_with_a_website_is_queued(self, stack, ws, owner):
        host = _host()
        company = _company(stack, ws, owner, url=f"https://www.{host.upper()}/about")
        row = _row(stack, company)
        assert (row["website"], row["status"], row["values"]) == (
            f"https://www.{host}/",
            "queued",
            {},
        )
        assert row["requested_by"] is None

    def test_enrichment_db_an_import_upsert_is_queued(self, stack, ws, owner):
        host = _host()
        [done] = _apply(
            stack,
            _actor(ws, owner, "import"),
            [
                {
                    "op": "upsert",
                    "table": "crm_companies",
                    "values": {
                        "twenty_id": f"tw-{uuid.uuid4().hex[:8]}",
                        "name": "Imported",
                        "owner_id": owner,
                        "website": {"primary": {"url": f"{host}"}, "others": []},
                    },
                }
            ],
        )
        assert _row(stack, done["id"])["website"] == f"https://{host}/"

    def test_enrichment_db_a_primary_domain_alone_is_queued(self, stack, ws, owner):
        host = _host()
        company = _company(stack, ws, owner, domain=host)
        assert _row(stack, company)["website"] == f"https://{host}/"

    def test_enrichment_db_neither_is_not_queued(self, stack, ws, owner):
        assert _row(stack, _company(stack, ws, owner)) is None

    def test_enrichment_db_a_website_change_queues_again_and_clears(self, stack, ws, owner):
        host, other = _host(), _host()
        company = _company(stack, ws, owner, url=f"https://{host}")
        _done(stack, company)
        _set_website(stack, ws, owner, company, f"https://{other}/")
        row = _row(stack, company)
        assert (row["website"], row["status"], row["values"]) == (f"https://{other}/", "queued", {})

    def test_enrichment_db_the_same_site_changes_nothing(self, stack, ws, owner):
        host = _host()
        company = _company(stack, ws, owner, url=f"https://{host}")
        _done(stack, company)
        before = _row(stack, company)
        _set_website(stack, ws, owner, company, f"http://{host}/contact")
        assert _row(stack, company) == before

    def test_enrichment_db_no_audit_row_and_no_updated_at_change(self, stack, ws, owner):
        host = _host()
        company = _company(stack, ws, owner, domain=host)
        stamp = stack.sql(f"SELECT updated_at FROM crm_companies WHERE id = {stack.q(company)}")[0][
            "updated_at"
        ]
        stack.sql(
            "UPDATE crm_company_enrichment SET status = 'failed', "
            "failure_reason = 'Website did not respond' "
            f"WHERE company_id = {stack.q(company)}"
        )
        assert (
            stack.sql(f"SELECT updated_at FROM crm_companies WHERE id = {stack.q(company)}")[0][
                "updated_at"
            ]
            == stamp
        )
        audited = stack.sql(
            "SELECT count(*)::int AS n FROM audit_log "
            f"WHERE workspace_id = {stack.q(ws)} AND target_table = 'crm_company_enrichment'"
        )
        assert audited[0]["n"] == 0
        actions = stack.sql(
            "SELECT action FROM audit_log "
            f"WHERE target_table = 'crm_companies' AND target_id = {stack.q(company)}"
        )
        assert [a["action"] for a in actions] == ["company.created"]

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.Acme.in/about?x=1",
            "acme.in",
            "http://acme.in:8080/",
            "https://user@acme.co.in/",
            "  https://acme.in./  ",
            "not a url",
            "https://bad host/",
        ],
    )
    def test_enrichment_db_the_site_is_the_one_python_computes(self, stack, ws, owner, url):
        domain = _host()
        company = _company(stack, ws, owner, url=url, domain=domain)
        expected = rules.site({"primary": {"url": url}}, domain)
        assert _row(stack, company)["website"] == expected


class TestTheTable:
    """Data-model §3.5 — the fixed reasons, a reason iff failed."""

    @pytest.mark.parametrize(
        ("status", "reason"),
        [("failed", None), ("done", "Website did not respond"), ("failed", "It broke")],
    )
    def test_enrichment_db_the_reason_checks(self, stack, ws, owner, status, reason):
        company = _company(stack, ws, owner, domain=_host())
        given = "NULL" if reason is None else stack.q(reason)
        with pytest.raises(stack.SqlError) as refused:
            stack.sql(
                f"UPDATE crm_company_enrichment SET status = {stack.q(status)}, "
                f"failure_reason = {given} WHERE company_id = {stack.q(company)}"
            )
        assert refused.value.code == "23514"

    def test_enrichment_db_purging_the_company_deletes_its_row(self, stack, ws, owner):
        company = _company(stack, ws, owner, domain=_host())
        _as_system(stack, f"DELETE FROM crm_companies WHERE id = {stack.q(company)}")
        assert _row(stack, company) is None


class TestTheStore:
    """`services/crm/store.py`'s enrichment wrappers."""

    def test_enrichment_db_the_queue_skips_the_trash_and_writes_only_its_site(
        self, stack, ws, owner, real
    ):
        live = _company(stack, ws, owner, domain=_host(), name="Live")
        gone = _company(stack, ws, owner, domain=_host(), name="Gone")
        _apply(
            stack,
            _actor(ws, owner),
            [{"op": "soft_delete", "table": "crm_companies", "id": gone}],
        )
        later = datetime.now(UTC) + timedelta(minutes=1)
        queued = real.queued_enrichment(ws, 10, before=later)
        assert [r["company_id"] for r in queued] == [live]
        site = queued[0]["website"]
        assert real.write_enrichment(ws, live, "https://elsewhere.in/", {"status": "done"}) is False
        assert real.write_enrichment(
            ws,
            live,
            site,
            {"status": "done", "values": {"city": "Coimbatore"}, "model": "claude-sonnet-5-5"},
        )
        assert _row(stack, live)["status"] == "done"
        assert real.write_enrichment(ws, live, site, {"status": "failed"}) is False

    def test_enrichment_db_requeue_keeps_what_it_is_not_given(self, stack, ws, owner, real):
        company = _company(stack, ws, owner, domain=_host())
        _done(stack, company)
        site = _row(stack, company)["website"]
        real.requeue_enrichment(
            ws,
            [{"company_id": company, "website": site, "requested_by": owner}],
            datetime.now(UTC),
        )
        row = _row(stack, company)
        assert (row["status"], row["requested_by"], row["values"]) == (
            "queued",
            owner,
            {"city": "Coimbatore"},
        )
        [found] = real.enrichment_for(ws, [company])
        assert found["company_id"] == company


class TestMerge:
    """FR-CRMAI-CHG-11, data-model §4."""

    def _merge(self, stack, ws, owner, keep, gone, changes=None) -> None:
        rows = {
            r["id"]: r
            for r in stack.sql(
                f"SELECT id, updated_at FROM crm_companies WHERE id IN ({stack.q(keep)}, "
                f"{stack.q(gone)})"
            )
        }
        plan = {
            "survivor_id": keep,
            "merged_id": gone,
            "expect": {
                "survivor_updated_at": rows[keep]["updated_at"],
                "merged_updated_at": rows[gone]["updated_at"],
            },
            "survivor_changes": changes or {},
        }
        stack.sql(f"SELECT crm_merge_companies({_j(stack, _actor(ws, owner))}, {_j(stack, plan)})")

    def test_enrichment_db_merge_keeps_the_survivors_row(self, stack, ws, owner):
        keep = _company(stack, ws, owner, url=f"https://{_host()}", name="Keep")
        gone = _company(stack, ws, owner, url=f"https://{_host()}", name="Gone")
        _done(stack, gone)
        before = _row(stack, keep)
        self._merge(stack, ws, owner, keep, gone)
        assert _row(stack, keep) == before
        assert _row(stack, gone) is None

    def test_enrichment_db_merge_moves_the_merged_row_to_a_survivor_without_one(
        self, stack, ws, owner
    ):
        keep = _company(stack, ws, owner, name="Keep")
        gone = _company(stack, ws, owner, url=f"https://{_host()}", name="Gone")
        _done(stack, gone)
        moved = _row(stack, gone)
        self._merge(stack, ws, owner, keep, gone)
        assert _row(stack, keep) == moved
        assert _row(stack, gone) is None

    @pytest.mark.parametrize("own_site", [False, True])
    def test_enrichment_db_merge_choosing_the_merged_website_moves_its_row(
        self, stack, ws, owner, own_site
    ):
        # The chosen website queues the survivor for the merged company's
        # site; that row goes and the merged company's done row moves.
        keep = _company(
            stack, ws, owner, url=f"https://{_host()}" if own_site else None, name="Keep"
        )
        if own_site:
            _done(stack, keep)
        url = f"https://{_host()}"
        gone = _company(stack, ws, owner, url=url, name="Gone")
        _done(stack, gone)
        moved = _row(stack, gone)
        website = {"primary": {"url": url}, "others": []}
        self._merge(stack, ws, owner, keep, gone, {"website": website})
        assert _row(stack, keep) == moved
        assert _row(stack, gone) is None
