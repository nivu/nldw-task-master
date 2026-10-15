"""The view registry, in the database and in Python — spec 007 FR-CRM-VIEW-02,
-03, -08, FR-CRM-ACC-11; data-model §3.15, §7.5; plan §5.5.

`crm_query()` reads `crm_object_fields` (seeded by migration 034); the
services check requests against `domain/crm/objects.VIEW_FIELDS`. The fake
reads the registry out of the migration file itself, so these tests compare
what the database would hold with what Python believes, field for field,
amount flags included. `tests/db/test_crm_query_db.py` compares the real
table too.
"""

from __future__ import annotations

from crm_fake import FakeCrmStore

from app.domain.crm import objects

COLUMNS = (
    "column_sql",
    "type",
    "is_amount",
    "filterable",
    "sortable",
    "groupable",
    "aggregatable",
    "workspace_scope",
)


def _database() -> dict[tuple[str, str], dict]:
    return {(r["object"], r["field"]): r for r in FakeCrmStore().list_object_fields()}


def _python() -> dict[tuple[str, str], objects.ViewField]:
    return {(kind, f.field): f for kind, fs in objects.VIEW_FIELDS.items() for f in fs}


class TestRegistry:
    def test_the_same_fields(self):
        assert sorted(_database()) == sorted(_python())

    def test_field_for_field(self):
        python = _python()
        for key, row in _database().items():
            assert {c: row[c] for c in COLUMNS} == {c: getattr(python[key], c) for c in COLUMNS}, (
                key
            )

    def test_the_amount_flags_match_the_redaction_list(self):
        """Every stored amount the presenter omits for a Viewer is an amount
        to the query too (plan §5.5), and nothing else is. A campaign's
        figures derived on read (041) are redacted but have no column."""
        for kind in objects.VIEW_FIELDS:
            flagged = {f for (k, f), row in _database().items() if k == kind and row["is_amount"]}
            stored = objects.AMOUNT_FIELDS[kind] - objects.DERIVED_AMOUNTS.get(kind, frozenset())
            assert flagged == set(stored), kind

    def test_fields_shared_with_the_record_registry_agree(self):
        for (kind, name), row in _database().items():
            record_field = objects.field(kind, name)
            if record_field is not None:
                assert record_field.amount == row["is_amount"], (kind, name)

    def test_every_derived_column_is_one_the_query_knows(self):
        """`crm_query_expr()` knows three expressions; anything else must be
        a column name the record carries."""
        derived = {"weighted_amount", "full_name", "primary_domain"}
        for (kind, name), row in _database().items():
            if row["column_sql"] in derived:
                continue
            assert row["column_sql"] == name, (kind, name)
