"""The effort statement's CSV download — spec 006 FR-STMT-02; spec 007
FR-CRM-CHG-08, FR-CRM-IO-05.

The statement route already answers `format=csv` with a `text/csv` body
and a `Content-Disposition` naming the file (`api/ops.py`). What broke the
download in the browser was the portal's proxy, which re-read every answer as
JSON; S8 widened it to pass a file through with its type and name. These
tests pin what the backend sends, so the proxy has something exact to carry.
"""

from __future__ import annotations

import pytest
from crm_fake import OWNER, as_
from crm_fake import client as crm_client

from app.services import statements

STATEMENT = {
    "project": {"id": "p1", "name": "Acme Pilot", "client": "Acme"},
    "period": "2026-09",
    "days": ["2026-09-01", "2026-09-02"],
    "people": [
        {
            "user_id": "u1",
            "display_name": "Deepika",
            "days": {"2026-09-01": "8", "2026-09-02": "6.5"},
            "total": "14.5",
            "unconfirmed_weeks": [],
            "notes": [],
        }
    ],
    "by_phase": [{"phase": "Delivery", "hours": "14.5"}],
    "total_hours": "14.5",
    "unconfirmed": False,
}
URL = "/api/v1/analytics/projects/p1/statement?period=2026-09&format=csv"


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setattr(statements, "statement", lambda project_id, period: STATEMENT)
    return crm_client(monkeypatch)


class TestStatementCsv:
    def test_the_body_is_csv(self, http):
        r = http.get(URL, headers=as_(OWNER["id"]))
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/csv; charset=utf-8"
        lines = r.text.splitlines()
        assert lines[0] == "Acme Pilot — effort statement 2026-09"
        assert lines[1] == "Person,2026-09-01,2026-09-02,Total,Unconfirmed weeks"
        assert lines[2].startswith("Deepika,8,6.5,14.5")

    def test_the_file_is_named_after_the_project_and_month(self, http):
        r = http.get(URL, headers=as_(OWNER["id"]))
        assert r.headers["content-disposition"] == 'attachment; filename="Acme Pilot-2026-09.csv"'

    def test_json_stays_json(self, http):
        r = http.get(URL.replace("&format=csv", ""), headers=as_(OWNER["id"]))
        assert r.headers["content-type"] == "application/json"
        assert r.json()["total_hours"] == "14.5"
