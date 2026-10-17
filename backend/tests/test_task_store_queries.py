"""The task store's PostgREST requests — spec 008 contracts §1 `GET /tasks`
(FR-TASK-LIST-07).

The fake task store has no URL limit and no array syntax, so what only the
request itself shows is checked here against a recording client: a link
filter's ids go `_ID_BATCH` per request (a long URL fails with 414), and a
tag travels as a quoted array literal (a comma would split it). The real
database answers are in tests/db/test_task_listing_db.py.
"""

from __future__ import annotations

from app.services import supabase as db
from app.services.tasks import store


class Query:
    def __init__(self, client: Client) -> None:
        self.client = client
        self.calls: list[tuple] = []

    def __getattr__(self, name: str):
        def call(*args, **kwargs):
            self.calls.append((name, *args))
            return self

        return call

    @property
    def not_(self) -> Query:
        return self

    def execute(self):
        self.client.queries.append(self.calls)
        return type("Response", (), {"data": []})()


class Client:
    def __init__(self) -> None:
        self.queries: list[list[tuple]] = []

    def table(self, _name: str) -> Query:
        return Query(self)


def test_a_link_filter_reads_its_ids_in_batches(monkeypatch):
    client = Client()
    monkeypatch.setattr(db, "supabase", client)
    ids = [f"00000000-0000-4000-8000-{n:012d}" for n in range(250)]
    store.assigned_tasks("me", ("done",), ids=ids, limit=21)
    sizes = [len(c[2]) for q in client.queries for c in q if c[:2] == ("in_", "id")]
    assert sizes == [100, 100, 50]


def test_a_tag_is_one_quoted_array_element(monkeypatch):
    client = Client()
    monkeypatch.setattr(db, "supabase", client)
    store.assigned_tasks("me", ("open",), tag='a,b "c"\\')
    [query] = client.queries
    assert ("contains", "tags", '{"a,b \\"c\\"\\\\"}') in query
