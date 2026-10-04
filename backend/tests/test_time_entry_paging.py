"""PostgREST stops at `max_rows` without an error, so time entries are read a
page at a time (app.services.supabase.list_time_entries)."""

from __future__ import annotations

from app.services import supabase as db


class FakeQuery:
    def __init__(self, rows: list[dict], max_rows: int) -> None:
        self.rows, self.max_rows = rows, max_rows
        self.offset, self.limit = 0, None

    def select(self, *_):
        return self

    def gte(self, *_):
        return self

    order = lte = eq = in_ = gte

    def range(self, start, end):
        self.offset, self.limit = start, end - start + 1
        return self

    def execute(self):
        size = min(self.limit or self.max_rows, self.max_rows)
        data = self.rows[self.offset : self.offset + size]
        return type("Response", (), {"data": data})()


class FakeClient:
    def __init__(self, rows: list[dict], max_rows: int) -> None:
        self.rows, self.max_rows, self.queries = rows, max_rows, 0

    def table(self, _name):
        self.queries += 1
        return FakeQuery(self.rows, self.max_rows)


def test_every_entry_is_read_past_the_row_limit(monkeypatch):
    rows = [{"id": str(n), "date": "2026-10-01"} for n in range(2500)]
    client = FakeClient(rows, max_rows=db.PAGE_SIZE)
    monkeypatch.setattr(db, "supabase", client)
    assert db.list_time_entries() == rows
    assert client.queries == 3


def test_an_exact_page_asks_once_more_and_stops(monkeypatch):
    rows = [{"id": str(n), "date": "2026-10-01"} for n in range(db.PAGE_SIZE)]
    client = FakeClient(rows, max_rows=db.PAGE_SIZE)
    monkeypatch.setattr(db, "supabase", client)
    assert len(db.list_time_entries(project_id="p")) == db.PAGE_SIZE
    assert client.queries == 2
