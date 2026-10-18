"""Review Queue expiry and tombstones — spec 009 FR-CRMAI-REV-07, -08,
FR-CRMAI-JOB-01, -02; Q-34; spec §15 "An item untouched for 14 days
expires, the expiry is in the job log and audit, and the same source does
not re-create it".

The `review_expiry` job runs through `@crm_job` against `tests/crm_fake.py`
with a fixed clock: one run per workspace in the job log, each expiry
audited with no suggestion text, and a decided item reduced at 90 days to a
dedupe tombstone that still stops the same source making it again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from crm_fake import OWNER, FakeCrmStore, two_workspaces

from app.services.crm import review as service
from app.tasks import crm_ai
from app.worker import CRM_AI_QUEUE, celery_app

NOW = datetime(2026, 10, 18, 20, 0, tzinfo=UTC)  # 01:30 IST on the 19th


def _at(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    company = fake.add(
        "crm_companies",
        workspace_id=labs["id"],
        name="Acme",
        owner_id=OWNER["id"],
        deleted_at=None,
    )
    return fake, labs, academy, company


def _item(fake, labs, company, **values):
    values = {"source_key": f"enrichment:{company['id']}", **values}
    return fake.add_review_item(
        labs,
        type="enrichment_conflict",
        source_kind="enrichment",
        record_type="company",
        record_id=company["id"],
        suggestion={"field": "city", "value": "Coimbatore", "label": "City"},
        current_values={"city": None},
        source_link={"record_type": "company", "record_id": company["id"]},
        **values,
    )


def _plan(item):
    """The item as its job would make it again from the same source."""
    keys = (
        "type",
        "owner_id",
        "record_type",
        "record_id",
        "suggestion",
        "current_values",
        "source_kind",
        "source_key",
        "source_version",
        "source_link",
        "dedupe_key",
    )
    return {**{k: item[k] for k in keys}, "suggestion": {"field": "city", "value": "Coimbatore"}}


class TestExpiry:
    def test_review_expiry_at_14_days(self, crm):
        fake, labs, _, company = crm
        old = _item(fake, labs, company, created_at=_at(14.01))
        young = _item(fake, labs, company, created_at=_at(13.99), source_key="enrichment:x")
        result = crm_ai._review_expiry(NOW)
        assert result["failed"] == []
        assert (old["status"], young["status"]) == ("expired", "open")
        assert old["decided_at"] and old["decided_by"] is None

    def test_review_expiry_is_in_the_job_log(self, crm):
        fake, labs, academy, company = crm
        _item(fake, labs, company, created_at=_at(20))
        crm_ai._review_expiry(NOW)
        runs = {
            r["workspace_id"]: r
            for r in fake.rows["crm_job_runs"].values()
            if r["job"] == "review_expiry"
        }
        assert set(runs) == {labs["id"], academy["id"]}
        assert runs[labs["id"]]["outcome"] == "ok"
        assert runs[labs["id"]]["counts"] == {"expired": 1, "tombstoned": 0}
        assert runs[academy["id"]]["counts"] == {"expired": 0, "tombstoned": 0}

    def test_review_expiry_is_audited_without_text(self, crm):
        fake, labs, _, company = crm
        old = _item(fake, labs, company, created_at=_at(20))
        crm_ai._review_expiry(NOW)
        [row] = [a for a in fake.audit if a["target_table"] == "crm_review_items"]
        assert (row["action"], row["actor_label"], row["actor_id"]) == (
            "review.expired",
            "system",
            None,
        )
        assert (row["target_id"], row["suggestion_id"]) == (old["id"], old["id"])
        assert "Coimbatore" not in str(row)

    def test_review_expiry_is_not_re_created_from_the_same_source(self, crm):
        fake, labs, _, company = crm
        old = _item(fake, labs, company, created_at=_at(20))
        crm_ai._review_expiry(NOW)
        assert service.upsert(labs["id"], [_plan(old)]) == [{"id": None, "created": False}]
        changed = {**_plan(old), "source_version": "v2"}
        [made] = service.upsert(labs["id"], [changed])
        assert made["created"] is True and made["id"] != old["id"]

    def test_review_expiry_twice_changes_nothing(self, crm):
        fake, labs, _, company = crm
        _item(fake, labs, company, created_at=_at(20))
        crm_ai._review_expiry(NOW)
        audit = len(fake.audit)
        crm_ai._review_expiry(NOW)
        assert len(fake.audit) == audit
        assert sum(1 for i in fake.rows["crm_review_items"].values() if i["status"] == "open") == 0


class TestTombstones:
    def test_review_at_90_days_the_text_goes_and_the_key_stays(self, crm):
        fake, labs, _, company = crm
        old = _item(
            fake,
            labs,
            company,
            created_at=_at(120),
            status="dismissed",
            decided_at=_at(90.01),
            decided_by=OWNER["id"],
            refusal="This record changed after the suggestion was made.",
        )
        recent = _item(
            fake,
            labs,
            company,
            created_at=_at(100),
            status="dismissed",
            decided_at=_at(89.99),
            decided_by=OWNER["id"],
            source_key="enrichment:y",
        )
        key, version = old["dedupe_key"], old["source_version"]
        result = crm_ai._review_expiry(NOW)
        assert result["runs"]["labs"]["counts"] == {"expired": 0, "tombstoned": 1}
        assert (old["suggestion"], old["current_values"], old["source_link"], old["refusal"]) == (
            {},
            {},
            {},
            None,
        )
        assert old["tombstoned_at"]
        assert (old["dedupe_key"], old["source_version"], old["status"]) == (
            key,
            version,
            "dismissed",
        )
        assert recent["tombstoned_at"] is None and recent["suggestion"]

    def test_review_a_tombstone_still_stops_the_same_source(self, crm):
        fake, labs, _, company = crm
        old = _item(
            fake,
            labs,
            company,
            created_at=_at(120),
            status="expired",
            decided_at=_at(100),
        )
        plan = _plan(old)
        crm_ai._review_expiry(NOW)
        assert service.upsert(labs["id"], [plan]) == [{"id": None, "created": False}]

    def test_review_a_tombstone_is_left_out_of_the_decided_view(self, crm):
        fake, labs, _, company = crm
        _item(fake, labs, company, created_at=_at(120), status="expired", decided_at=_at(100))
        crm_ai._review_expiry(NOW)
        assert fake.list_review_items(labs["id"], status="decided") == []


class TestTheTask:
    """FR-CRMAI-JOB-02, -04."""

    def test_review_expiry_runs_on_the_ai_queue_and_keeps_no_result(self):
        task = celery_app.tasks["crm_ai.review_expiry"]
        assert task.ignore_result is True
        router = celery_app.amqp.router
        assert router.route({}, "crm_ai.review_expiry")["queue"].name == CRM_AI_QUEUE

    def test_review_expiry_is_scheduled_daily_at_0130(self):
        entry = celery_app.conf.beat_schedule["crm-ai-review-expiry"]
        assert entry["task"] == "crm_ai.review_expiry"
        schedule = entry["schedule"]
        assert (schedule.hour, schedule.minute) == ({1}, {30})
