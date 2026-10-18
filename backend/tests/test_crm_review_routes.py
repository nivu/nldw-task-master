"""The Review Queue's routes — spec 009 FR-CRMAI-REV-01 to -06, -09, -10,
FR-CRMAI-FLAG-04, FR-CRMAI-CHG-09, -11 (items); contracts §2.2; spec §15
"Review Queue" lines:

- "Approving a next-step item changes the deal through the same validation
  as a manual edit: an approval with an empty date is refused, and an
  approval after someone else edited the deal's next step is refused as
  changed."
- "Approving a task item creates one 008 task with source and source
  reference, linked to the record; approving twice creates one."
- "Members see only their items; workspace Admins see all; Viewers see none;
  another workspace's item id gives 404."
- "An approved change's audit entry names the approver and carries the item
  id."
- "Trashing a company hides its summaries and items; restoring shows them."
- "crm_review_list and crm_review_decide work through NLDW, with CONFIRM
  FIRST on decide."

Routes run against `tests/crm_fake.py` (with 008's `tests/tasks_fake.py`
beside it), so a task item is approved through 008's real `create_task`.
No job makes items yet (S1b): the tests make them through
`services/crm/review.upsert`, as the jobs will, or seed them directly.

`008`'s morning brief shows the same To review section as CRM Today
(TestTheMorningBrief, T165), driven through 008's `GET /tasks/brief` and
`task_brief` in 008's TaskWorld.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from crm_fake import FakeCrmStore, as_
from crm_fake import client as crm_client
from postgrest.exceptions import APIError
from tasks_fake import ID, TaskWorld
from tasks_fake import NOW as TASKS_NOW

from app.domain.crm import ai_rules
from app.domain.crm import review as rules
from app.mcp import crm as tools
from app.mcp import tasks as task_tools
from app.services.crm import notifications
from app.services.crm import review as service
from app.services.crm import today as crm_today

API = "/api/v1/crm"
LABS = {"workspace": "labs"}
NOW = datetime(2026, 10, 18, 6, 0, tzinfo=UTC)


def _person(name: str, **values) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "email": f"{name.lower()}@nunnarilabs.com",
        "display_name": name,
        "role": "user",
        "lead_id": None,
        "is_active": True,
        "account_type": "staff",
        "timezone": "Asia/Kolkata",
        **values,
    }


ADMIN = _person("Navaneeth", role="admin")
ASHA = _person("Asha")
BALA = _person("Bala")
VIEWER = _person("Devansh", role="lead")
GONE = _person("Gowri", is_active=False)
OUTSIDER = _person("Vinita", role="admin")
PEOPLE = {p["id"]: p for p in (ADMIN, ASHA, BALA, VIEWER, GONE, OUTSIDER)}


class Ctx:
    def __init__(self, user_id: str) -> None:
        self.headers = {"authorization": f"Bearer {user_id}"}


class Queue:
    """Labs: Acme (owned by Asha) with Priya, two open deals of Asha's and
    one of Bala's; Academy has a deal of its own. The owner is a Labs and
    Academy admin; Devansh views Labs; Gowri's account is inactive."""

    def __init__(self, monkeypatch) -> None:
        self.fake = fake = FakeCrmStore().install(monkeypatch)
        self.labs = fake.add_workspace("labs", "Nunnari Labs")
        self.academy = fake.add_workspace("academy", "Nunnari Academy B2B")
        for ws in (self.labs, self.academy):
            fake.add_deal_lists(ws)
            fake.add_member(ws, ADMIN["id"], "admin", last_used_at="2026-10-01T00:00:00+00:00")
        fake.add_member(self.labs, ASHA["id"], "member")
        fake.add_member(self.labs, BALA["id"], "member")
        fake.add_member(self.labs, VIEWER["id"], "viewer")
        fake.add_member(self.labs, GONE["id"], "member")
        self.http = crm_client(monkeypatch, PEOPLE, fake=fake)
        monkeypatch.setattr(service, "_now", lambda: NOW)
        self.sent: list = []
        monkeypatch.setattr(
            notifications, "deliver", lambda message: self.sent.append(message) or ["email"]
        )
        self.acme = self.post("companies", {"name": "Acme", "owner_id": ASHA["id"]})
        self.patch("companies", self.acme["id"], {"lead_stage": "qualified"})
        self.priya = self.post("people", {"first_name": "Priya", "company_id": self.acme["id"]})
        self.pilot = self.deal("Acme pilot", ASHA)
        self.rollout = self.deal("Acme rollout", ASHA)
        self.balas = self.deal("Acme support", BALA)
        self.college = self.post(
            "companies", {"name": "Zqx College"}, query={"workspace": "academy"}
        )
        self.patch(
            "companies",
            self.college["id"],
            {"lead_stage": "qualified"},
            query={"workspace": "academy"},
        )

    def post(self, plural, body, who=ADMIN, query=LABS) -> dict:
        r = self.http.post(f"{API}/{plural}", params=query, json=body, headers=as_(who["id"]))
        assert r.status_code == 201, r.text
        return r.json()

    def patch(self, plural, record_id, body, who=ADMIN, query=LABS) -> dict:
        r = self.http.patch(
            f"{API}/{plural}/{record_id}", params=query, json=body, headers=as_(who["id"])
        )
        assert r.status_code == 200, r.text
        return r.json()

    def deal(self, name, owner) -> dict:
        return self.post(
            "opportunities",
            {
                "name": name,
                "company_id": self.acme["id"],
                "owner_id": owner["id"],
                "next_step": "Follow up on pilot",
                "next_step_on": "2026-10-21",
            },
        )

    def next_step(self, *deals, owner=ASHA, suggestion=None, version="g1") -> dict:
        """A `next_step_change` item, made as a job makes one."""
        ids = [d["id"] for d in deals]
        [made] = service.upsert(
            self.labs["id"],
            [
                {
                    "type": "next_step_change",
                    "owner_id": owner["id"] if owner else None,
                    "record_type": "opportunity",
                    "record_id": ids[0],
                    "suggestion": suggestion
                    or {"next_step": "Send revised SOW", "next_step_on": "2026-10-24"},
                    "current_values": {
                        d["id"]: {"next_step": d["next_step"], "next_step_on": d["next_step_on"]}
                        for d in deals
                    },
                    "source_kind": "email_thread",
                    "source_key": "gmail:mb1:t1",
                    "source_version": version,
                    "source_link": {"mailbox_id": "mb1", "thread_id": "t1"},
                    "dedupe_key": rules.dedupe_key(
                        "email_thread", "gmail:mb1:t1", "next_step_change", "opportunity", ids[0]
                    ),
                    "model": "claude-sonnet-5-5",
                    "generated_at": NOW.isoformat(),
                    "deal_choices": ids,
                }
            ],
        )
        return self.item(made["id"])

    def task_item(self, target_type, target, *, commitment_owner=None, owner=ASHA) -> dict:
        title, due_on = "Send the pilot plan", "2026-10-24"
        fingerprint = rules.commitment_fingerprint(title, due_on)
        [made] = service.upsert(
            self.labs["id"],
            [
                {
                    "type": "task_from_commitment",
                    "owner_id": owner["id"] if owner else None,
                    "record_type": target_type,
                    "record_id": target["id"],
                    "suggestion": {
                        "title": title,
                        "due_on": due_on,
                        "owner_id": commitment_owner["id"] if commitment_owner else None,
                        "owner_unknown": commitment_owner is None,
                    },
                    "current_values": {},
                    "source_kind": "email_thread",
                    "source_key": "gmail:mb1:t2",
                    "source_version": "g2",
                    "source_link": {"mailbox_id": "mb1", "thread_id": "t2"},
                    "dedupe_key": rules.dedupe_key(
                        "email_thread",
                        "gmail:mb1:t2",
                        "task_from_commitment",
                        target_type,
                        target["id"],
                        fingerprint=fingerprint,
                    ),
                    "model": "claude-sonnet-5-5",
                    "generated_at": NOW.isoformat(),
                }
            ],
        )
        return self.item(made["id"])

    def item(self, item_id: str) -> dict:
        return self.fake.rows["crm_review_items"][item_id]

    def get(self, path, who=ADMIN, query=LABS, **params):
        return self.http.get(f"{API}/{path}", params={**query, **params}, headers=as_(who["id"]))

    def approve(self, item, body=None, who=ASHA, query=LABS):
        return self.http.post(
            f"{API}/review/{item['id']}/approve",
            params=query,
            json=body or {},
            headers=as_(who["id"]),
        )

    def dismiss(self, item, who=ASHA, query=LABS):
        return self.http.post(
            f"{API}/review/{item['id']}/dismiss", params=query, headers=as_(who["id"])
        )

    def deal_row(self, deal) -> dict:
        return self.fake.rows["crm_opportunities"][deal["id"]]


@pytest.fixture
def queue(monkeypatch) -> Queue:
    return Queue(monkeypatch)


def _ids(answer: dict) -> list[str]:
    return [i["id"] for i in answer["items"]]


class TestWhoSeesTheQueue:
    """FR-CRMAI-REV-03, contracts §2.2."""

    def test_review_members_see_only_theirs(self, queue):
        mine = queue.next_step(queue.pilot)
        queue.next_step(queue.balas, owner=BALA, version="g9")
        got = queue.get("review", who=ASHA).json()
        assert (_ids(got), got["total"]) == ([mine["id"]], 1)
        [shown] = got["items"]
        assert shown["type_label"] == "Change next step"
        assert shown["target"] == {
            "record_type": "opportunity",
            "record_id": queue.pilot["id"],
            "name": "Acme pilot",
        }
        assert shown["current"] == {"next_step": "Follow up on pilot", "next_step_on": "2026-10-21"}
        assert shown["deal_choices"][0]["name"] == "Acme pilot"
        assert shown["source"]["link"]["thread_key"] == "mb1:t1"
        assert shown["ai"]["model"] == "claude-sonnet-5-5"
        assert shown["expires_on"] == rules.expires_on(mine["created_at"]).isoformat()

    def test_review_admins_see_all_and_their_own_holds_the_admins_items(self, queue):
        ashas = queue.next_step(queue.pilot)
        admins = queue.next_step(queue.balas, owner=None, version="g9")
        assert set(_ids(queue.get("review", scope="all").json())) == {ashas["id"], admins["id"]}
        assert _ids(queue.get("review").json()) == [admins["id"]]

    def test_review_viewers_see_none(self, queue):
        queue.next_step(queue.pilot, owner=None)
        assert queue.get("review", who=VIEWER).json() == {"items": [], "total": 0}
        assert queue.get("review", who=VIEWER, scope="all").json() == {"items": [], "total": 0}

    @pytest.mark.parametrize("params", [{"scope": "all"}, {"status": "decided"}])
    def test_review_the_whole_queue_is_for_admins(self, queue, params):
        r = queue.get("review", who=ASHA, **params)
        assert (r.status_code, r.json()["detail"]) == (403, rules.ADMINS_ONLY)

    def test_review_another_workspaces_item_is_not_found(self, queue):
        foreign = queue.fake.add_review_item(
            queue.academy,
            type="enrichment_conflict",
            source_kind="enrichment",
            record_type="company",
            record_id=queue.college["id"],
            suggestion={"field": "city", "value": "Pune"},
        )
        for call in (queue.approve, queue.dismiss):
            r = call(foreign, who=ADMIN)
            assert (r.status_code, r.json()["detail"]) == (404, rules.NO_SUCH)
        assert foreign["status"] == "open"

    def test_review_another_members_item_is_not_found(self, queue):
        balas = queue.next_step(queue.balas, owner=BALA)
        r = queue.approve(balas, who=ASHA)
        assert (r.status_code, r.json()["detail"]) == (404, rules.NO_SUCH)

    def test_review_a_viewer_cannot_decide(self, queue):
        item = queue.next_step(queue.pilot, owner=None)
        assert queue.approve(item, who=VIEWER).status_code == 403

    def test_review_decided_items_for_admins(self, queue):
        item = queue.next_step(queue.pilot)
        assert queue.dismiss(item).status_code == 200
        assert _ids(queue.get("review", status="decided", scope="all").json()) == [item["id"]]
        assert queue.get("review", scope="all").json()["total"] == 0


class TestNextStep:
    """FR-CRMAI-REV-04, contracts §2.2 — through `deals.update`."""

    def test_review_approving_sets_the_deal_through_the_manual_path(self, queue):
        item = queue.next_step(queue.pilot)
        r = queue.approve(item)
        assert r.status_code == 200, r.text
        deal = queue.deal_row(queue.pilot)
        assert (deal["next_step"], deal["next_step_on"]) == ("Send revised SOW", "2026-10-24")
        assert r.json()["result"]["opportunity"]["next_step"] == "Send revised SOW"
        assert r.json()["item"]["status"] == "approved"
        assert item["result"] == {"opportunity_id": queue.pilot["id"]}

    def test_review_the_audit_row_names_the_approver_and_carries_the_item(self, queue):
        item = queue.next_step(queue.pilot)
        queue.approve(item)
        [row] = [
            a
            for a in queue.fake.audit
            if a["target_table"] == "crm_opportunities" and a.get("suggestion_id")
        ]
        assert (row["actor_id"], row["actor_label"], row["channel"]) == (ASHA["id"], "user", "web")
        assert row["suggestion_id"] == item["id"]
        assert row["after"]["next_step"] == "Send revised SOW"
        decided = [a for a in queue.fake.audit if a["target_table"] == "crm_review_items"]
        assert [(a["action"], a["suggestion_id"]) for a in decided] == [
            ("review.approved", item["id"])
        ]

    def test_review_an_empty_date_is_refused_as_a_manual_edit_is(self, queue):
        manual = queue.http.patch(
            f"{API}/opportunities/{queue.rollout['id']}",
            params=LABS,
            json={"next_step": "Send revised SOW", "next_step_on": None},
            headers=as_(ASHA["id"]),
        )
        assert manual.status_code == 422
        item = queue.next_step(
            queue.pilot, suggestion={"next_step": "Send revised SOW", "next_step_on": None}
        )
        r = queue.approve(item)
        assert (r.status_code, r.json()["detail"]) == (422, manual.json()["detail"])
        assert (item["status"], item["refusal"]) == ("open", manual.json()["detail"])
        assert queue.deal_row(queue.pilot)["next_step"] == "Follow up on pilot"

    def test_review_someone_elses_edit_is_refused_as_changed(self, queue):
        item = queue.next_step(queue.pilot)
        queue.patch("opportunities", queue.pilot["id"], {"next_step": "Call the CFO"})
        r = queue.approve(item)
        assert (r.status_code, r.json()["detail"]) == (409, rules.CHANGED)
        assert (item["status"], item["refusal"]) == ("open", rules.CHANGED)
        assert queue.deal_row(queue.pilot)["next_step"] == "Call the CFO"

    def test_review_a_change_between_the_check_and_the_write_is_refused_as_changed(
        self, queue, monkeypatch
    ):
        item = queue.next_step(queue.pilot)
        deals = queue.fake.rows["crm_opportunities"]

        def changed_meanwhile(recorded, row):
            # Someone saves the deal after the check (a new row, as a
            # database read would give).
            deals[row["id"]] = {**row, "updated_at": "2026-10-18T05:59:00+00:00"}
            return []

        monkeypatch.setattr(rules, "stale_fields", changed_meanwhile)
        r = queue.approve(item)
        assert (r.status_code, r.json()["detail"]) == (409, rules.CHANGED)
        # The failed write rolls the fake's rows back: read the item again.
        after = queue.item(item["id"])
        assert (after["status"], after["refusal"]) == ("open", rules.CHANGED)
        assert queue.deal_row(queue.pilot)["next_step"] == "Follow up on pilot"

    def test_review_a_fault_during_the_change_reopens_the_item(self, queue, monkeypatch):
        """A failure that is not a refusal (here a statement timeout the
        store does not map) must not leave the item claimed with nothing
        changed: it opens again, and can be approved once the fault is gone."""
        item = queue.next_step(queue.pilot)
        real = service.deals.update

        def times_out(*args, **kwargs):
            raise APIError({"code": "57014", "message": "canceling statement due to timeout"})

        monkeypatch.setattr(service.deals, "update", times_out)
        with pytest.raises(APIError):
            queue.approve(item)
        after = queue.item(item["id"])
        assert (after["status"], after["decided_by"], after["refusal"]) == (
            "open",
            None,
            rules.NOT_MADE,
        )
        assert "57014" not in after["refusal"] and "timeout" not in after["refusal"]
        assert queue.deal_row(queue.pilot)["next_step"] == "Follow up on pilot"
        monkeypatch.setattr(service.deals, "update", real)
        r = queue.approve(item)
        assert r.status_code == 200, r.text
        assert queue.deal_row(queue.pilot)["next_step"] == "Send revised SOW"

    def test_review_a_last_inbound_change_does_not_refuse(self, queue):
        item = queue.next_step(queue.pilot)
        queue.deal_row(queue.pilot)["last_inbound_at"] = "2026-10-18T05:00:00+00:00"
        assert queue.approve(item).status_code == 200

    def test_review_edit_and_approve_against_the_current_value(self, queue):
        item = queue.next_step(queue.pilot)
        queue.patch("opportunities", queue.pilot["id"], {"next_step": "Call the CFO"})
        r = queue.approve(
            item, {"edits": {"next_step": "Send SOW to the CFO", "next_step_on": "2026-10-25"}}
        )
        assert r.status_code == 200, r.text
        deal = queue.deal_row(queue.pilot)
        assert (deal["next_step"], deal["next_step_on"]) == ("Send SOW to the CFO", "2026-10-25")

    def test_review_an_edit_of_another_field_is_refused(self, queue):
        item = queue.next_step(queue.pilot)
        r = queue.approve(item, {"edits": {"amount": "10"}})
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "amount cannot be edited on this suggestion.",
        )
        assert item["status"] == "open"

    def test_review_two_open_deals_need_the_chosen_one(self, queue):
        item = queue.next_step(queue.pilot, queue.rollout)
        r = queue.approve(item)
        assert (r.status_code, r.json()["detail"]) == (422, rules.CHOOSE_DEAL)
        assert item["status"] == "open"
        listed = queue.get("review", who=ASHA).json()["items"][0]
        assert {d["id"] for d in listed["deal_choices"]} == {
            queue.pilot["id"],
            queue.rollout["id"],
        }
        r = queue.approve(item, {"deal_id": queue.rollout["id"]})
        assert r.status_code == 200, r.text
        assert queue.deal_row(queue.rollout)["next_step"] == "Send revised SOW"
        assert queue.deal_row(queue.pilot)["next_step"] == "Follow up on pilot"

    def test_review_a_deal_that_is_not_a_choice_is_refused(self, queue):
        item = queue.next_step(queue.pilot)
        r = queue.approve(item, {"deal_id": queue.balas["id"]})
        assert (r.status_code, r.json()["detail"]) == (422, rules.CHOOSE_DEAL)


class TestDismissAndTwice:
    """FR-CRMAI-REV-08, contracts §2.2."""

    def test_review_dismiss(self, queue):
        item = queue.next_step(queue.pilot)
        r = queue.dismiss(item)
        assert r.status_code == 200, r.text
        assert r.json()["item"]["status"] == "dismissed"
        assert r.json()["item"]["decided_by"] == {"id": ASHA["id"], "name": "Asha"}
        assert queue.deal_row(queue.pilot)["next_step"] == "Follow up on pilot"
        [row] = [a for a in queue.fake.audit if a["target_table"] == "crm_review_items"]
        assert (row["action"], row["actor_id"]) == ("review.dismissed", ASHA["id"])
        assert "Send revised SOW" not in str(row)

    def test_review_a_decided_item_is_refused(self, queue):
        item = queue.next_step(queue.pilot)
        queue.dismiss(item)
        for call in (queue.approve, queue.dismiss):
            r = call(item)
            assert (r.status_code, r.json()["detail"]) == (409, rules.ALREADY_DECIDED)

    def test_review_a_dismissed_item_is_not_made_again_from_the_same_source(self, queue):
        item = queue.next_step(queue.pilot)
        queue.dismiss(item)
        again = service.upsert(
            queue.labs["id"],
            [
                {
                    **{
                        k: item[k]
                        for k in (
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
                    },
                    "deal_choices": [queue.pilot["id"]],
                }
            ],
        )
        assert again == [{"id": None, "created": False}]
        assert len(queue.fake.rows["crm_review_items"]) == 1

    def test_review_a_refreshed_source_updates_the_open_item(self, queue):
        item = queue.next_step(queue.pilot)
        again = queue.next_step(
            queue.pilot,
            suggestion={"next_step": "Book the demo", "next_step_on": "2026-10-26"},
            version="g2",
        )
        assert again["id"] == item["id"]
        assert item["suggestion"]["next_step"] == "Book the demo"
        assert len(queue.fake.rows["crm_review_items"]) == 1


class TestTasks:
    """FR-CRMAI-REV-06 — through 008's `create_task`."""

    def _tasks(self, queue) -> list[dict]:
        return list(queue.fake.rows["tasks"].values())

    def test_review_approving_creates_one_task_with_its_source(self, queue):
        item = queue.task_item("person", queue.priya, commitment_owner=ASHA)
        r = queue.approve(item)
        assert r.status_code == 200, r.text
        [task] = self._tasks(queue)
        assert (task["title"], task["due_on"], task["source"], task["source_ref"]) == (
            "Send the pilot plan",
            "2026-10-24",
            "email_commitment",
            "gmail:mb1:t2",
        )
        assert task["workspace_id"] == queue.labs["id"]
        links = {
            (lk["record_type"], lk["record_id"])
            for lk in queue.fake.rows["task_links"].values()
            if lk["task_id"] == task["id"]
        }
        assert links == {("person", queue.priya["id"]), ("company", queue.acme["id"])}
        assert item["result"] == {"task_id": task["id"]}
        assert r.json()["result"]["task"]["id"] == task["id"]

    def test_review_approving_twice_creates_one_task(self, queue):
        item = queue.task_item("person", queue.priya, commitment_owner=ASHA)
        assert queue.approve(item).status_code == 200
        r = queue.approve(item)
        assert (r.status_code, r.json()["detail"]) == (409, rules.ALREADY_DECIDED)
        assert len(self._tasks(queue)) == 1

    def test_review_the_tasks_audit_rows_carry_the_item(self, queue):
        item = queue.task_item("person", queue.priya, commitment_owner=ASHA)
        queue.approve(item)
        actors = [actor for actor, ops in queue.fake.tasks.applied]
        assert actors and all(a.get("suggestion_id") == item["id"] for a in actors)
        assert all(a["user_id"] == ASHA["id"] for a in actors)

    def test_review_an_active_member_owner_gets_the_task(self, queue):
        item = queue.task_item("person", queue.priya, commitment_owner=BALA)
        queue.approve(item)
        [task] = self._tasks(queue)
        assert task["assignee_id"] == BALA["id"]

    @pytest.mark.parametrize("who", [None, VIEWER, GONE])
    def test_review_an_unknown_viewer_or_inactive_owner_leaves_the_approver(self, queue, who):
        item = queue.task_item("person", queue.priya, commitment_owner=who)
        assert queue.approve(item).status_code == 200
        [task] = self._tasks(queue)
        assert task["assignee_id"] == ASHA["id"]

    def test_review_a_company_target_sends_that_link_once(self, queue):
        item = queue.task_item("company", queue.acme)
        r = queue.approve(item)
        assert r.status_code == 200, r.text
        [task] = self._tasks(queue)
        links = [
            (lk["record_type"], lk["record_id"])
            for lk in queue.fake.rows["task_links"].values()
            if lk["task_id"] == task["id"]
        ]
        assert links == [("company", queue.acme["id"])]

    def test_review_a_task_008_refuses_keeps_the_item_open(self, queue):
        item = queue.task_item("person", queue.priya)
        r = queue.approve(item, {"edits": {"assignee_id": OUTSIDER["id"]}})
        assert r.status_code == 422, r.text
        assert (item["status"], item["refusal"]) == ("open", r.json()["detail"])
        assert self._tasks(queue) == []
        reopened = [
            a["action"] for a in queue.fake.audit if a["target_table"] == "crm_review_items"
        ]
        assert reopened == ["review.approved", "review.reopened"]

    def test_review_edit_and_approve_a_task(self, queue):
        item = queue.task_item("opportunity", queue.pilot)
        r = queue.approve(item, {"edits": {"title": "Send the plan and the SOW", "notes": "Both"}})
        assert r.status_code == 200, r.text
        [task] = self._tasks(queue)
        assert (task["title"], task["body"]) == ("Send the plan and the SOW", "Both")


class TestTrash:
    """FR-CRMAI-CHG-11 — a trashed target hides its items, restoring shows
    them, and an item cannot be decided while its record is in the Trash."""

    def test_review_trashing_the_target_hides_it_and_restoring_shows_it(self, queue):
        item = queue.next_step(queue.pilot, owner=None)
        assert _to_review(queue) == (1, [item["id"]])
        r = queue.http.delete(
            f"{API}/opportunities/{queue.pilot['id']}", params=LABS, headers=as_(ADMIN["id"])
        )
        assert r.status_code == 200, r.text
        assert queue.get("review", scope="all").json() == {"items": [], "total": 0}
        assert _to_review(queue) == (0, [])
        for call in (queue.approve, queue.dismiss):
            r = call(item, who=ADMIN)
            assert (r.status_code, r.json()["detail"]) == (422, rules.TRASHED)
        assert item["status"] == "open"
        r = queue.http.post(
            f"{API}/opportunities/{queue.pilot['id']}/restore",
            params=LABS,
            headers=as_(ADMIN["id"]),
        )
        assert r.status_code == 200, r.text
        assert _ids(queue.get("review", scope="all").json()) == [item["id"]]
        assert _to_review(queue) == (1, [item["id"]])


def _to_review(queue) -> tuple[int, list[str]]:
    """The Admin's Today To review section: its count and item ids (none
    when the section is not there)."""
    sections = queue.get("today").json()["sections"]
    found = [s for s in sections if s["key"] == "to_review"]
    if not found:
        return 0, []
    return found[0]["count"], [i["record_id"] for i in found[0]["items"]]


class TestNoNotice:
    """FR-CRMAI-REV-10 — creating items sends no bell and no message."""

    def test_review_creating_items_tells_nobody(self, queue):
        before = len(queue.fake.rows["crm_notifications"])
        queue.next_step(queue.pilot)
        queue.task_item("person", queue.priya, commitment_owner=ASHA)
        assert len(queue.fake.rows["crm_notifications"]) == before
        assert queue.sent == []

    def test_review_money_in_a_suggestion_is_masked_again(self, queue):
        item = queue.next_step(
            queue.pilot, suggestion={"next_step": "Agree the ₹4.5L fee", "next_step_on": None}
        )
        assert "4.5L" not in item["suggestion"]["next_step"]
        assert ai_rules.MASK in item["suggestion"]["next_step"]


class TestBootstrapAndToday:
    """FR-CRMAI-FLAG-04, FR-CRMAI-REV-09."""

    def test_review_the_queue_shows_while_items_are_open(self, queue):
        ai = queue.get("bootstrap", who=ASHA).json()["ai"]
        assert (ai["review_visible"], ai["open_review_count"]) == (False, 0)
        queue.next_step(queue.pilot)
        ai = queue.get("bootstrap", who=ASHA).json()["ai"]
        assert (ai["review_visible"], ai["open_review_count"]) == (True, 1)
        ai = queue.get("bootstrap", who=BALA).json()["ai"]
        assert (ai["review_visible"], ai["open_review_count"]) == (False, 0)
        # An Admin: while the workspace has any; the count is their own.
        ai = queue.get("bootstrap", who=ADMIN).json()["ai"]
        assert (ai["review_visible"], ai["open_review_count"]) == (True, 0)

    def test_review_never_for_a_viewer(self, queue):
        queue.fake.set_ai(queue.labs, phases={"1": True})
        queue.next_step(queue.pilot, owner=None)
        ai = queue.get("bootstrap", who=VIEWER).json()["ai"]
        assert (ai["review_visible"], ai["open_review_count"]) == (False, 0)

    def test_review_today_has_the_section_last(self, queue):
        item = queue.next_step(queue.pilot)
        sections = queue.get("today", who=ASHA).json()["sections"]
        assert [s["key"] for s in sections][-1] == "to_review"
        assert sections[-1]["count"] == 1
        assert sections[-1]["items"] == [
            {
                "record_type": "review_item",
                "record_id": item["id"],
                "title": "Change next step",
                "detail": "Acme pilot",
                "created_at": item["created_at"],
            }
        ]


class TestTheTimeline:
    """FR-CRMAI-REV-05, contracts §1 — "approved AI suggestion"."""

    def test_review_the_deals_timeline_says_approved_suggestion(self, queue):
        item = queue.next_step(queue.pilot)
        queue.approve(item)
        entries = queue.get(f"opportunities/{queue.pilot['id']}/timeline", who=ASHA).json()
        marked = [e for e in entries if e.get("approved_suggestion")]
        assert [e["suggestion_id"] for e in marked] == [item["id"]]
        assert all("suggestion_id" not in e for e in entries if e not in marked)


class TestThroughClaude:
    """Spec §15 "crm_review_list and crm_review_decide work through NLDW, with
    CONFIRM FIRST on decide"; slice S1b "done when"."""

    def test_review_listed_and_approved_from_claude(self, queue):
        item = queue.next_step(queue.pilot)
        listed = asyncio.run(tools.crm_review_list(Ctx(ASHA["id"]), workspace="labs"))
        assert _ids(listed) == [item["id"]]
        done = asyncio.run(
            tools.crm_review_decide(Ctx(ASHA["id"]), item["id"], "approve", workspace="labs")
        )
        assert done["item"]["status"] == "approved"
        [row] = [
            a
            for a in queue.fake.audit
            if a.get("suggestion_id") and a["target_table"] == "crm_opportunities"
        ]
        assert row["channel"] == "mcp"
        entries = queue.get(f"opportunities/{queue.pilot['id']}/timeline", who=ASHA).json()
        assert any(e.get("approved_suggestion") for e in entries)

    def test_review_edited_and_dismissed_from_claude(self, queue):
        one = queue.next_step(queue.pilot)
        two = queue.next_step(queue.rollout, version="g9")
        asyncio.run(
            tools.crm_review_decide(
                Ctx(ASHA["id"]),
                one["id"],
                "edit",
                edits={"next_step": "Send the SOW", "next_step_on": "2026-10-30"},
                workspace="labs",
            )
        )
        assert queue.deal_row(queue.pilot)["next_step"] == "Send the SOW"
        asyncio.run(
            tools.crm_review_decide(Ctx(ASHA["id"]), two["id"], "dismiss", workspace="labs")
        )
        assert two["status"] == "dismissed"


# ---------------------------------------------------------------------------
# Spec 009 S3b — rule suggestions (FR-CRMAI-RULE-07, contracts §2.2)
# ---------------------------------------------------------------------------


class TestRuleSuggestions:
    """Approving a follow-up rule's item goes through the manual path, so
    pipeline rules apply (a deal still needs its next step): R-3 the lead
    stage or the next touch, R-4 the conversion or the close-out, R-1 only
    marks the draft used. Each change's audit row carries the item."""

    def _rule(self, queue, rule, record_type, record, options, current, **extra) -> dict:
        key = f"rule:{rule}:{extra.get('member_id') or record['id']}"
        [made] = service.upsert(
            queue.labs["id"],
            [
                {
                    "type": "rule_suggestion",
                    "owner_id": ASHA["id"],
                    "record_type": record_type,
                    "record_id": record["id"],
                    "member_id": extra.get("member_id"),
                    "suggestion": {"rule": rule, "options": options, **extra.get("more", {})},
                    "current_values": current,
                    "source_kind": "rule",
                    "source_key": key,
                    "source_version": "v1",
                    "source_link": {"record_type": record_type, "record_id": record["id"]},
                    "dedupe_key": rules.dedupe_key(
                        "rule",
                        key,
                        "rule_suggestion",
                        record_type,
                        record["id"],
                        member_id=extra.get("member_id"),
                    ),
                    "model": None,
                    "generated_at": None,
                    "deal_choices": [],
                }
            ],
        )
        return queue.item(made["id"])

    def _cold(self, queue, stage="contacted"):
        quiet = queue.post("companies", {"name": "Quiet Ltd", "owner_id": ASHA["id"]})
        queue.patch("companies", quiet["id"], {"lead_stage": stage})
        options = (
            ["nurturing", "disqualified"]
            if stage == "contacted"
            else [
                "disqualified",
                "next_touch",
            ]
        )
        return quiet, self._rule(queue, "R-3", "company", quiet, options, {"lead_stage": stage})

    def _member(self, queue):
        campaign = queue.fake.add(
            "crm_campaigns",
            workspace_id=queue.labs["id"],
            name="Roundtable",
            type_id=None,
            status_id=None,
            starts_on=None,
            ends_on=None,
            owner_id=ADMIN["id"],
            budget=None,
            budget_currency=None,
            budget_in_inr=None,
            budget_fx_rate=None,
            twenty_id=None,
            position=0,
            deleted_at=None,
            deleted_by=None,
            deleted_group=None,
        )
        member = queue.fake.add(
            "crm_campaign_members",
            workspace_id=queue.labs["id"],
            campaign_id=campaign["id"],
            company_id=queue.acme["id"],
            person_id=None,
            status="engaged",
            status_changed_at="2026-10-01T00:00:00+00:00",
            last_touch_on=None,
            notes=None,
            converted_opportunity_id=None,
            twenty_id=None,
            position=0,
            created_at="2026-09-01T00:00:00+00:00",
            deleted_at=None,
            deleted_by=None,
            deleted_group=None,
        )
        item = self._rule(
            queue,
            "R-4",
            "company",
            queue.acme,
            ["qualify", "close_out"],
            {"member_status": "engaged"},
            member_id=member["id"],
        )
        return member, item

    def test_review_r3_moves_the_lead_stage_as_a_manual_edit(self, queue):
        quiet, item = self._cold(queue)
        r = queue.approve(item, {"option": "nurturing"})
        assert r.status_code == 200, r.text
        assert queue.fake.rows["crm_companies"][quiet["id"]]["lead_stage"] == "nurturing"
        assert r.json()["item"]["status"] == "approved"
        [row] = [
            a
            for a in queue.fake.audit
            if a["target_id"] == quiet["id"] and a.get("suggestion_id") == item["id"]
        ]
        assert (row["action"], row["actor_id"]) == ("company.updated", ASHA["id"])
        assert row["after"] == {"lead_stage": "nurturing"}

    def test_review_r3_needs_a_choice_of_option(self, queue):
        _, item = self._cold(queue)
        r = queue.approve(item)
        assert (r.status_code, r.json()["detail"]) == (422, service.CHOOSE_OPTION)
        r = queue.approve(item, {"option": "customer"})
        assert (r.status_code, r.json()["detail"]) == (422, service.CHOOSE_OPTION)

    def test_review_r3_a_next_touch_needs_its_date(self, queue):
        quiet, item = self._cold(queue, "nurturing")
        r = queue.approve(item, {"option": "next_touch"})
        assert (r.status_code, r.json()["detail"]) == (422, service.NEXT_TOUCH_DATE)
        r = queue.approve(item, {"option": "next_touch", "edits": {"next_touch_on": "2026-11-02"}})
        assert r.status_code == 200, r.text
        stored = queue.fake.rows["crm_companies"][quiet["id"]]
        assert (stored["next_touch_on"], stored["lead_stage"]) == ("2026-11-02", "nurturing")

    def test_review_r3_a_changed_lead_stage_is_refused(self, queue):
        quiet, item = self._cold(queue)
        queue.patch("companies", quiet["id"], {"lead_stage": "nurturing"})
        r = queue.approve(item, {"option": "disqualified"})
        assert (r.status_code, r.json()["detail"]) == (409, rules.CHANGED)
        assert queue.item(item["id"])["status"] == "open"

    def test_review_r4_qualify_still_needs_a_next_step(self, queue):
        member, item = self._member(queue)
        r = queue.approve(
            item, {"option": "qualify", "edits": {"new_opportunity": {"name": "Acme pilot 2"}}}
        )
        assert r.status_code == 422, r.text
        assert "next step" in r.json()["detail"]
        assert queue.item(item["id"])["status"] == "open"
        assert queue.item(item["id"])["refusal"] == r.json()["detail"]
        assert queue.fake.rows["crm_campaign_members"][member["id"]]["status"] == "engaged"

    def test_review_r4_qualify_converts_the_member(self, queue):
        member, item = self._member(queue)
        new = {
            "name": "Acme pilot 2",
            "owner_id": ASHA["id"],
            "next_step": "Send the proposal",
            "next_step_on": "2026-10-28",
        }
        r = queue.approve(item, {"option": "qualify", "edits": {"new_opportunity": new}})
        assert r.status_code == 200, r.text
        stored = queue.fake.rows["crm_campaign_members"][member["id"]]
        deal_id = r.json()["result"]["opportunity"]["id"]
        assert (stored["status"], stored["converted_opportunity_id"]) == ("converted", deal_id)
        assert queue.item(item["id"])["result"] == {
            "member_id": member["id"],
            "opportunity_id": deal_id,
        }

    def test_review_r4_close_out_is_not_interested(self, queue):
        member, item = self._member(queue)
        r = queue.approve(item, {"option": "close_out"})
        assert r.status_code == 200, r.text
        assert queue.fake.rows["crm_campaign_members"][member["id"]]["status"] == "not_interested"
        assert any(
            a["target_id"] == member["id"] and a.get("suggestion_id") == item["id"]
            for a in queue.fake.audit
        )

    def test_review_r4_a_changed_status_is_refused(self, queue):
        member, item = self._member(queue)
        queue.fake.rows["crm_campaign_members"][member["id"]]["status"] = "meeting_booked"
        r = queue.approve(item, {"option": "close_out"})
        assert (r.status_code, r.json()["detail"]) == (409, rules.CHANGED)

    def test_review_r1_marks_the_draft_used_and_changes_nothing(self, queue):
        item = self._rule(
            queue,
            "R-1",
            "person",
            queue.priya,
            ["mark_used"],
            {},
            more={"draft": "Hi Priya, a gentle nudge on the pilot plan."},
        )
        audit = len(queue.fake.audit)
        r = queue.approve(item)
        assert r.status_code == 200, r.text
        assert queue.item(item["id"])["result"] == {"used": True}
        assert [a["action"] for a in queue.fake.audit[audit:]] == ["review.approved"]
        r = queue.approve(item)
        assert r.status_code == 409

    @pytest.mark.parametrize("new", ["x", ["Acme pilot 2"]])
    def test_review_r4_a_malformed_new_opportunity_leaves_the_item_open(self, queue, new):
        member, item = self._member(queue)
        r = queue.approve(item, {"option": "qualify", "edits": {"new_opportunity": new}})
        assert r.status_code == 422, r.text
        assert r.json()["detail"].startswith("new_opportunity: ")
        assert queue.item(item["id"])["status"] == "open"
        assert queue.fake.rows["crm_campaign_members"][member["id"]]["status"] == "engaged"


# ---------------------------------------------------------------------------
# FR-CRMAI-REV-09 — 008's morning brief shows the same To review (T052, T165)
# ---------------------------------------------------------------------------


@pytest.fixture
def world(monkeypatch) -> TaskWorld:
    """008's brief world (as `test_task_brief.py` builds it): phase 2 of
    tasks on for the cast, and CRM Today on 008's fixed clock."""
    w = TaskWorld(monkeypatch)
    for person in w.fake.profiles.values():
        if person.get("tasks_level") == 1:
            person["tasks_level"] = 2
    monkeypatch.setattr(crm_today, "_now", lambda: TASKS_NOW)
    return w


class TestTheMorningBrief:
    """Spec §15 "Today shows To review … and 008's brief shows the same
    section": the brief's Labs sections are CRM Today's, To review among
    them, through `GET /tasks/brief` and `task_brief`."""

    @staticmethod
    def _item(world: TaskWorld, owner: str) -> dict:
        return world.fake.add_review_item(
            world.labs,
            type="next_step_change",
            owner_id=ID[owner],
            record_type="opportunity",
            record_id=world.deal["id"],
            suggestion={"next_step": "Send revised SOW", "next_step_on": "2026-10-24"},
        )

    @staticmethod
    def _labs(world: TaskWorld, who: str) -> list[dict]:
        r = world.call(who, "GET", "/brief")
        assert r.status_code == 200, r.text
        [labs] = [w for w in r.json()["workspaces"] if w["workspace"]["key"] == "labs"]
        return labs["sections"]

    @staticmethod
    def _today(world: TaskWorld, who: str) -> list[dict]:
        r = world.http.get(f"{API}/today", params=LABS, headers=as_(ID[who]))
        assert r.status_code == 200, r.text
        return r.json()["sections"]

    def test_review_the_brief_shows_the_members_to_review_as_today_does(self, world):
        item = self._item(world, "member")
        sections = self._labs(world, "member")
        assert sections == self._today(world, "member")
        assert sections[-1] == {
            "key": "to_review",
            "count": 1,
            "items": [
                {
                    "record_type": "review_item",
                    "record_id": item["id"],
                    "title": "Change next step",
                    "detail": "Acme RDX",
                    "created_at": item["created_at"],
                }
            ],
        }

    def test_review_task_brief_shows_the_same_to_review(self, world):
        item = self._item(world, "member")
        tool = asyncio.run(task_tools.task_brief(Ctx(ID["member"])))
        [labs] = [w for w in tool["workspaces"] if w["workspace"]["key"] == "labs"]
        assert labs["sections"] == self._today(world, "member")
        assert [i["record_id"] for i in labs["sections"][-1]["items"]] == [item["id"]]

    def test_review_the_brief_never_shows_it_to_a_viewer(self, world):
        world.fake.set_ai(world.labs, phases={"1": True})
        self._item(world, "member")
        sections = self._labs(world, "viewer")
        assert sections == self._today(world, "viewer")
        assert "to_review" not in [s["key"] for s in sections]

    def test_review_the_brief_leaves_it_out_while_the_queue_is_not_visible(self, world):
        """No phase on and no open item of the Member's own (another
        Member's does not count): absent, as on Today; a phase on shows it."""
        self._item(world, "creator")
        sections = self._labs(world, "member")
        assert sections == self._today(world, "member")
        assert "to_review" not in [s["key"] for s in sections]
        world.fake.set_ai(world.labs, phases={"1": True})
        sections = self._labs(world, "member")
        assert sections == self._today(world, "member")
        assert sections[-1] == {"key": "to_review", "count": 0, "items": []}
