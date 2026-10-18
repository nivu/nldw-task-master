"""In-memory task store — spec 008 tasks.md "How to use this file".

`FakeTaskStore` implements every public name of `app.services.tasks.store`
over the **same** in-memory tables as `tests/crm_fake.py` (`tasks`,
`task_links`, the memberships, projects and allocations), with Python fakes
of `task_apply` (049), the deferred workspace check (047) and
`task_purge_expired` (049), so route tests never touch a database. The real
functions are covered by `tests/db/`. S2a adds 052's comments (insert and
soft delete only, the workspace of their task) and the delegation messages'
sent markers. S2b reads the tasks a person created (Assigned by me, the
brief's nudges). S3b adds 053: a recurring task has a due date, a series has
one live occurrence per due date (`tasks_series_due`), and only a series'
next occurrence names its creator.

`FakeCrmStore.install` installs one beside itself, so a CRM route that
presents a task (its project link, FR-TASK-VIS-07) reads the same rows.

`TaskWorld` (S1b) is the cast and records the task route, tool and privacy
tests share: the TVA roles and account kinds of spec §3 and FR-TASK-VIS-06
with My Tasks switched on (uuid ids, keyed by `ID`), a Labs deal and
an Academy company made through the CRM routes, and TVA projects, at a fixed
clock (Tuesday 20 October 2026, noon IST). Its delegation messages (S2a) are
sent at once, through the real `notices.send`, and kept in `messages`.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime
from typing import Any

from crm_fake import (
    DEFAULTS,
    PEOPLE,
    CheckViolation,
    FakeCrmStore,
    _at,
    _now,
    as_,
    client,
    touched_tasks,
)

from app.domain.tasks import service
from app.services import notify
from app.services.tasks import notices, store

#: 049: the tables `task_apply` writes; the later ones arrive in 051-055.
TASK_TABLES = ("tasks", "task_links", "task_import_runs", "task_comments")


def public_task_store_names() -> list[str]:
    """Every function the real task store exports — what the fake replaces."""
    return [
        name
        for name, value in vars(store).items()
        if inspect.isfunction(value) and value.__module__ == store.__name__ and name[0] != "_"
    ]


def _refuse(code: str, message: str) -> Exception:
    return store._refusal(code, message)


class FakeTaskStore:
    def __init__(self, crm: FakeCrmStore) -> None:
        self.crm = crm
        # `app_settings.tasks_default_level` (050).
        self.default = 0
        # Every task_apply call: (actor, ops).
        self.applied: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        # Every task_purge_expired call: (actor, before).
        self.purges: list[tuple[dict[str, Any], str]] = []
        # 051: the Life OS import's run records, beside the CRM's tables.
        self.rows.setdefault("task_import_runs", {})
        # 052: comments (audited) and the messages' sent markers (a log).
        self.rows.setdefault("task_comments", {})
        self.rows.setdefault("task_notice_sends", {})

    @property
    def rows(self) -> dict[str, dict[str, dict[str, Any]]]:
        return self.crm.rows

    def install(self, monkeypatch) -> FakeTaskStore:
        for name in public_task_store_names():
            monkeypatch.setattr(store, name, getattr(self, name))
        return self

    # -- set-up helpers (not store names) -----------------------------------

    def add_task(self, **values: Any) -> dict[str, Any]:
        """A task as the database would hold it, written with no audit row."""
        row = {
            "id": str(uuid.uuid4()),
            "workspace_id": None,
            "created_at": _now(),
            "updated_at": _now(),
            "updated_by": values.get("created_by"),
            "bucket": "later",
            **DEFAULTS["tasks"],
            **values,
        }
        if row["status"] in ("open", "waiting") and not row.get("bucket"):
            row["bucket"] = "later"
        self.rows["tasks"][row["id"]] = row
        return row

    def add_link(self, task: dict[str, Any], record_type: str, record_id: str) -> dict[str, Any]:
        row = {
            "id": str(uuid.uuid4()),
            "workspace_id": None if record_type == "tva_project" else task["workspace_id"],
            "task_id": task["id"],
            "record_type": record_type,
            "record_id": record_id,
            "project_id": record_id if record_type == "tva_project" else None,
            "created_at": _now(),
            "created_by": task.get("created_by"),
        }
        self.rows["task_links"][row["id"]] = row
        return row

    # -- store ---------------------------------------------------------------

    def rpc(self, fn: str, params: dict[str, Any]) -> Any:
        raise AssertionError(f"FakeTaskStore has no fake for {fn}; call a named store function.")

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.rows["tasks"].get(task_id)
        return dict(row) if row else None

    def get_tasks(self, task_ids: list[str]) -> list[dict[str, Any]]:
        return [dict(self.rows["tasks"][t]) for t in task_ids if t in self.rows["tasks"]]

    def list_links(self, task_ids: list[str]) -> list[dict[str, Any]]:
        rows = [dict(r) for r in self.rows["task_links"].values() if r["task_id"] in task_ids]
        return sorted(rows, key=lambda r: (r["created_at"], r["id"]))

    def subtask_counts(self, task_ids: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.rows["tasks"].values():
            if row.get("parent_id") in task_ids and not row.get("deleted_at"):
                counts[row["parent_id"]] = counts.get(row["parent_id"], 0) + 1
        return counts

    def assigned_tasks(
        self,
        user_id: str,
        statuses: tuple[str, ...],
        *,
        tag: str | None = None,
        priority: str | None = None,
        ids: list[str] | None = None,
        q: str | None = None,
        before: tuple[str, str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        term = (q or "").strip().lower()
        rows = [
            dict(t)
            for t in self.rows["tasks"].values()
            if t.get("assignee_id") == user_id
            and t["status"] in statuses
            and not t.get("deleted_at")
            and (not tag or tag in (t.get("tags") or []))
            and (not priority or t.get("priority") == priority)
            and (ids is None or t["id"] in ids)
            and (
                not term
                or term in (t.get("title") or "").lower()
                or term in (t.get("body") or "").lower()
            )
            and (
                not before
                or _at(t["updated_at"]) < _at(before[0])
                or (_at(t["updated_at"]) == _at(before[0]) and t["id"] > before[1])
            )
        ]
        if limit is None:
            return sorted(rows, key=lambda r: r["id"])
        rows.sort(key=lambda r: r["id"])
        rows.sort(key=lambda r: _at(r["updated_at"]), reverse=True)
        return rows[:limit]

    def created_tasks(
        self, user_id: str, statuses: tuple[str, ...], *, others_only: bool = False
    ) -> list[dict[str, Any]]:
        rows = [
            dict(t)
            for t in self.rows["tasks"].values()
            if t.get("created_by") == user_id
            and t["status"] in statuses
            and not t.get("deleted_at")
            # SQL's `<>`: a task with no assignee is not someone else's.
            and (not others_only or t.get("assignee_id") not in (None, user_id))
        ]
        return sorted(rows, key=lambda r: r["id"])

    def done_between(self, user_id: str, start: str, end: str) -> list[dict[str, Any]]:
        rows = [
            dict(t)
            for t in self.rows["tasks"].values()
            if t.get("assignee_id") == user_id
            and t["status"] == "done"
            and not t.get("deleted_at")
            and t.get("completed_at")
            and _at(start) <= _at(t["completed_at"]) < _at(end)
        ]
        return sorted(rows, key=lambda r: (_at(r["completed_at"]), r["id"]))

    def linked_task_ids(self, record_type: str, record_id: str) -> list[str]:
        return sorted(
            {
                lk["task_id"]
                for lk in self.rows["task_links"].values()
                if lk["record_type"] == record_type and lk["record_id"] == record_id
            }
        )

    def subtasks(self, parent_ids: list[str]) -> list[dict[str, Any]]:
        rows = [
            dict(t)
            for t in self.rows["tasks"].values()
            if t.get("parent_id") in parent_ids and not t.get("deleted_at")
        ]
        return sorted(rows, key=lambda r: (r["created_at"], r["id"]))

    def task_history(self, task_id: str) -> list[dict[str, Any]]:
        def about(r: dict[str, Any]) -> bool:
            if r["target_table"] in ("tasks", "crm_tasks"):
                return r["target_id"] == task_id
            if r["target_table"] in ("task_links", "crm_task_links"):
                return any((x or {}).get("task_id") == task_id for x in (r["after"], r["before"]))
            return False

        return [dict(r) for r in sorted(self.crm.audit, key=lambda r: r["id"]) if about(r)]

    def deleted_personal_tasks(self, user_id: str) -> list[dict[str, Any]]:
        rows = [
            dict(t)
            for t in self.rows["tasks"].values()
            if t.get("created_by") == user_id
            and t.get("workspace_id") is None
            and t.get("deleted_at")
        ]
        return sorted(rows, key=lambda r: (r["deleted_at"], r["id"]), reverse=True)

    def existing_import_keys(self, keys: list[str]) -> set[str]:
        wanted = set(keys)
        return {
            t["source_ref"]
            for t in self.rows["tasks"].values()
            if t.get("source") == "import" and t.get("source_ref") in wanted
        }

    def get_import_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.rows["task_import_runs"].get(run_id)
        return dict(row) if row else None

    def latest_import_run_id(self, uploader_id: str) -> str | None:
        """The newest is the last inserted: the tests' clock is fixed, so
        every run's `created_at` is the same."""
        runs = self.rows["task_import_runs"].values()
        mine = [r["id"] for r in runs if r["uploader_id"] == uploader_id]
        return mine[-1] if mine else None

    def list_comments(self, task_id: str) -> list[dict[str, Any]]:
        rows = [
            dict(c)
            for c in self.rows["task_comments"].values()
            if c["task_id"] == task_id and not c.get("deleted_at")
        ]
        return sorted(rows, key=lambda r: (r["created_at"], r["id"]))

    def get_comment(self, comment_id: str) -> dict[str, Any] | None:
        row = self.rows["task_comments"].get(comment_id)
        return dict(row) if row else None

    def claim_notice(
        self, task_id: str, event: str, recipient_id: str, occurred_at: str
    ) -> str | None:
        """052 `task_notice_sends_once`: a new marker's id, else None."""
        key = (task_id, event, recipient_id, _at(occurred_at))
        for row in self.rows["task_notice_sends"].values():
            if (row["task_id"], row["event"], row["recipient_id"], _at(row["occurred_at"])) == key:
                return None
        row = {
            "id": str(uuid.uuid4()),
            "task_id": task_id,
            "event": event,
            "recipient_id": recipient_id,
            "occurred_at": occurred_at,
            "sent_at": None,
            "channels": None,
        }
        self.rows["task_notice_sends"][row["id"]] = row
        return row["id"]

    def mark_notice_sent(self, notice_id: str, sent_at: str, channels: list[str]) -> None:
        self.rows["task_notice_sends"][notice_id].update(sent_at=sent_at, channels=channels)

    def set_nudge_days(self, user_id: str, days: int) -> dict[str, Any]:
        """052 `profiles.task_nudge_days`, on the profiles the client signs in."""
        if not 1 <= days <= 30:
            raise _refuse("23514", "violates check constraint")
        self.crm.profiles[user_id]["task_nudge_days"] = days
        return dict(self.crm.profiles[user_id])

    def active_profiles(self) -> list[dict[str, Any]]:
        ids = list(self.crm.profiles) or list(PEOPLE)
        return [p for p in self.get_profiles(ids) if p["is_active"]]

    def project_people(self, project_id: str) -> set[str]:
        return {
            a["user_id"] for a in self.rows["allocations"].values() if a["project_id"] == project_id
        }

    def projects_named(self, q: str) -> list[dict[str, Any]]:
        term = q.strip().lower()
        if not term:
            return []
        return sorted(
            (
                {k: p.get(k) for k in ("id", "name", "status", "is_archived")}
                for p in self.rows["projects"].values()
                if not p.get("is_archived") and term in p["name"].lower()
            ),
            key=lambda p: p["name"],
        )

    def _profile(self, user_id: str) -> dict[str, Any] | None:
        return self.crm.profiles.get(user_id) or PEOPLE.get(user_id)

    def get_profiles(self, user_ids: list[str]) -> list[dict[str, Any]]:
        out = []
        for user_id in user_ids:
            p = self._profile(user_id)
            if p is not None:
                out.append(
                    {
                        "id": p["id"],
                        "email": p["email"],
                        "display_name": p["display_name"],
                        "role": p.get("role"),
                        "is_active": p.get("is_active", True),
                        "account_type": p.get("account_type") or "staff",
                        "tasks_level": p.get("tasks_level"),
                        "timezone": p.get("timezone"),
                    }
                )
        return out

    def memberships_of(self, user_id: str) -> list[dict[str, Any]]:
        return [
            {"workspace_id": m["workspace_id"], "role": m["role"]}
            for m in self.rows["crm_memberships"].values()
            if m["user_id"] == user_id
        ]

    def member_ids(self, workspace_id: str) -> set[str]:
        return {
            m["user_id"]
            for m in self.rows["crm_memberships"].values()
            if m["workspace_id"] == workspace_id
        }

    def allocated_project_ids(self, user_id: str) -> set[str]:
        return {
            a["project_id"] for a in self.rows["allocations"].values() if a["user_id"] == user_id
        }

    def get_projects(self, project_ids: list[str]) -> list[dict[str, Any]]:
        return [
            {k: p.get(k) for k in ("id", "name", "status", "is_archived")}
            for pid, p in self.rows["projects"].items()
            if pid in project_ids
        ]

    def get_workspaces(self, workspace_ids: list[str]) -> list[dict[str, Any]]:
        return [
            {k: w.get(k) for k in ("id", "key", "name", "tva_linked")}
            for wid, w in self.rows["crm_workspaces"].items()
            if wid in workspace_ids
        ]

    def record_workspace(self, record_type: str, record_id: str) -> dict[str, Any] | None:
        table = store.RECORD_TABLES.get(record_type)
        row = self.rows[table].get(record_id) if table else None
        return {k: row.get(k) for k in ("id", "workspace_id", "deleted_at")} if row else None

    def default_level(self) -> int:
        return self.default

    # -- task_apply (049), with 047's checks ---------------------------------

    def apply(self, actor: dict[str, Any], ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """task_apply (data-model §5.2): all ops or none."""
        self.applied.append((actor, ops))
        who = actor.get("user_id")
        if actor.get("kind") not in ("user", "system", "import") or (
            actor.get("kind") != "system" and not who
        ):
            raise _refuse("CRM22", "A task change needs an actor.")
        snapshot = {t: {k: dict(v) for k, v in rows.items()} for t, rows in self.rows.items()}
        audit_len = len(self.crm.audit)
        refs: dict[str, str] = {}
        try:
            done = []
            for op in ops:
                result = self._one(actor, op, refs)
                if op.get("ref"):
                    refs[op["ref"]] = result["id"]
                done.append(result)
            for op, result in zip(ops, done, strict=True):
                row = self.rows[op["table"]].get(result["id"])
                if row is not None:
                    self.crm._check_rows(op["table"], row)
                    if op["table"] == "tasks":
                        self._recurrence_check(row)
                    result["row"] = dict(row)
            self.crm.workspace_check(touched_tasks(ops, done))
            return done
        except Exception:
            self.crm.rows, self.crm.audit[audit_len:] = snapshot, []
            raise

    def _allowed(self, actor: dict[str, Any], table: str, subject: dict[str, Any]) -> bool:
        if table == "task_import_runs":
            # 049: the actor is the run's uploader.
            return subject.get("uploader_id") == actor.get("user_id")
        task = subject if table == "tasks" else self.rows["tasks"].get(subject.get("task_id"))
        if task is None:
            return False
        who = actor.get("user_id")
        if task.get("workspace_id") is not None:
            return any(
                m["workspace_id"] == task["workspace_id"] and m["user_id"] == who
                for m in self.rows["crm_memberships"].values()
            )
        return actor["kind"] in ("system", "import") or who in (
            task.get("created_by"),
            task.get("assignee_id"),
        )

    def _parent_check(self, row: dict[str, Any]) -> None:
        """047 `tasks_parent_check` and the parent foreign key."""
        parent_id = row.get("parent_id")
        if not parent_id:
            return
        parent = self.rows["tasks"].get(parent_id)
        if parent is None:
            raise _refuse("23503", "violates foreign key constraint")
        if parent.get("parent_id") or any(
            t.get("parent_id") == row["id"] for t in self.rows["tasks"].values()
        ):
            raise _refuse("CRM22", "A subtask cannot have subtasks.")

    def _recurrence_check(self, row: dict[str, Any]) -> None:
        """053 `tasks_recurrence_due` and `tasks_series_due`."""
        if row.get("recurrence") and not row.get("due_on"):
            raise CheckViolation(f"tasks violates tasks_recurrence_due: {row}")
        series = row.get("series_id")
        if series and not row.get("deleted_at"):
            if any(
                t["id"] != row["id"]
                and t.get("series_id") == series
                and str(t.get("due_on")) == str(row.get("due_on"))
                and not t.get("deleted_at")
                for t in self.rows["tasks"].values()
            ):
                raise _refuse(
                    "23505", 'duplicate key value violates unique constraint "tasks_series_due"'
                )

    @staticmethod
    def _import_run_defaults() -> dict[str, Any]:
        """051's column defaults; `now()` is the service's clock, which the
        tests fix."""
        return {
            "status": "dry_run",
            "report": {},
            "dry_run_at": service._now().isoformat(),
            "applied_at": None,
        }

    def _lifeos_key_check(self, row: dict[str, Any]) -> None:
        """047 `tasks_lifeos_key`: a Life OS key is unique, in the Trash or not."""
        ref = row.get("source_ref") or ""
        if row.get("source") == "import" and ref.startswith("lifeos:"):
            if any(
                t["id"] != row["id"] and t.get("source") == "import" and t.get("source_ref") == ref
                for t in self.rows["tasks"].values()
            ):
                raise _refuse(
                    "23505", 'duplicate key value violates unique constraint "tasks_lifeos_key"'
                )

    def _one(self, actor: dict[str, Any], op: dict[str, Any], refs: dict[str, str]) -> dict:
        table = op.get("table")
        if table not in TASK_TABLES:
            raise _refuse("CRM22", "That is not a task table.")
        if op.get("op") not in ("insert", "update", "soft_delete", "restore", "delete"):
            raise _refuse("CRM22", "Unknown task operation.")
        if table == "task_comments" and op["op"] not in ("insert", "soft_delete"):
            raise _refuse("CRM22", "A comment cannot be changed.")
        values = {
            k: refs[v[5:]] if isinstance(v, str) and v.startswith("$ref:") else v
            for k, v in (op.get("values") or {}).items()
        }
        for k in ("id", "created_at", "created_by"):
            # 053: a tasks insert may name its series' creator (checked below).
            if k in values and not (
                k == "created_by" and table == "tasks" and op["op"] == "insert"
            ):
                raise _refuse("CRM22", f"{k} cannot be set here.")
        who = actor.get("user_id")
        rows = self.rows[table]
        if op["op"] == "insert":
            series = values.get("series_id")
            if "created_by" in values and not any(
                series is not None
                and t.get("series_id") == series
                and t.get("created_by") == values["created_by"]
                for t in self.rows["tasks"].values()
            ):
                raise _refuse("CRM22", "created_by cannot be set here.")
            row = {
                "id": str(uuid.uuid4()),
                "workspace_id": None,
                "created_at": _now(),
                "created_by": who,
                "updated_at": _now(),
                **({"updated_by": who} if table == "tasks" else {}),
                **DEFAULTS.get(table, {}),
                **(self._import_run_defaults() if table == "task_import_runs" else {}),
                **(
                    {"kind": "comment", "deleted_at": None, "deleted_by": None}
                    if table == "task_comments"
                    else {}
                ),
                **values,
            }
            if not self._allowed(actor, table, row):
                raise _refuse("CRM04", "No such task.")
            if table == "tasks":
                self._parent_check(row)
                self._lifeos_key_check(row)
            if table == "task_comments":
                # 052 `task_comments_workspace`.
                task = self.rows["tasks"].get(row["task_id"]) or {}
                if row.get("workspace_id") != task.get("workspace_id"):
                    raise _refuse("CRM22", "A comment belongs to its task's workspace.")
            self.crm._check_refs(table, row)
            self.crm._check_unique(table, row)
            rows[row["id"]] = row
            self.crm._audit(actor, {**op, "op": "insert"}, row, None, dict(row))
            return {"ref": op.get("ref"), "id": row["id"], "row": dict(row)}

        row = rows.get(op.get("id"))
        if row is None or not self._allowed(actor, table, row):
            raise _refuse("CRM04", "No such task.")
        if op.get("expect_updated_at") and _at(op["expect_updated_at"]) != _at(row["updated_at"]):
            raise _refuse(
                "CRM09", "This task changed while you were editing it; reload and try again."
            )
        if op["op"] == "delete":
            self.crm._cascade(actor, table, row["id"])
            self.crm._delete(actor, table, row, op.get("action"))
            return {"ref": op.get("ref"), "id": row["id"], "row": dict(row)}
        if op["op"] in ("soft_delete", "restore"):
            if table not in ("tasks", "task_comments"):
                raise _refuse("CRM22", "This kind of row cannot be deleted.")
            deleting = op["op"] == "soft_delete"
            values = {
                "deleted_at": _now() if deleting else None,
                "deleted_by": who if deleting else None,
            }
            if table == "tasks":
                values["deleted_group"] = op.get("deleted_group") if deleting else None
        elif not values:
            raise _refuse("CRM22", "Nothing to change.")
        if table == "tasks":
            if (
                "workspace_id" in values
                and row.get("workspace_id") is not None
                and values["workspace_id"] != row["workspace_id"]
            ):
                raise _refuse("CRM22", "A CRM task cannot leave its workspace.")
            if "parent_id" in values:
                self._parent_check({**row, **values})
        self.crm._update(actor, op, row, values)
        return {"ref": op.get("ref"), "id": row["id"], "row": dict(row)}

    def purge_expired(self, actor: dict[str, Any], before: str) -> int:
        """task_purge_expired (data-model §5.3), as the system."""
        self.purges.append((actor, before))
        if actor.get("kind") != "system":
            raise _refuse("CRM22", "A task change needs an actor.")
        cutoff = _at(before)
        gone = [
            t
            for t in self.rows["tasks"].values()
            if t.get("workspace_id") is None
            and t.get("deleted_at")
            and _at(t["deleted_at"]) < cutoff
        ]
        for task in gone:
            self.crm._cascade(actor, "tasks", task["id"])
            self.crm._delete(actor, "tasks", task)
        return len(gone)


# ---------------------------------------------------------------------------
# The shared world of the S1b route, tool and privacy tests
# ---------------------------------------------------------------------------

#: Tuesday 20 October 2026, noon IST (spec §12's fixed clock).
NOW = datetime(2026, 10, 20, 6, 30, tzinfo=UTC)


def _person(key: str, n: int, name: str, role: str = "user", **values: Any) -> dict[str, Any]:
    return {
        "id": f"00000000-0000-4000-8000-{n:012d}",
        "key": key,
        "email": f"{name.split()[0].lower()}@nunnarilabs.com",
        "display_name": name,
        "role": role,
        "lead_id": None,
        "is_active": True,
        "account_type": "staff",
        "timezone": "Asia/Kolkata",
        "tasks_level": 1,
        "is_owner": False,
        **values,
    }


#: Spec §3 and FR-TASK-VIS-06's account kinds, by a short key, with uuid ids
#: (the task routes take uuids). Phase 1 is on for all but Omar.
_CAST = (
    _person("owner", 1, "Navaneeth", "admin", is_owner=True),
    _person("member", 2, "Deepika"),
    _person("viewer", 3, "Devansh", "lead"),
    _person(
        "seller", 4, "Tamil", account_type="crm_only", logs_time=False, timezone="Australia/Sydney"
    ),
    _person("outsider", 5, "Vinita", "admin"),
    _person("manager", 6, "Mona Manager", "manager"),
    _person("lead", 7, "Lakshmi Lead", "lead"),
    _person("creator", 8, "Chitra Creator", lead_id="00000000-0000-4000-8000-000000000007"),
    _person("assignee", 9, "Arun Assignee"),
    _person("former", 10, "Farah Former"),
    _person("waiting", 11, "Wasim Waiting"),
    _person("removed", 12, "Ravi Removed"),
    _person("labsadmin", 13, "Leela Labsadmin"),
    _person("user", 14, "Uma User"),
    _person("off", 15, "Omar Off", tasks_level=None),
    _person("gone", 16, "Gita Gone", is_active=False),
)
#: Short key → id.
ID: dict[str, str] = {p["key"]: p["id"] for p in _CAST}
TASK_PEOPLE: dict[str, dict[str, Any]] = {p["id"]: p for p in _CAST}


class TaskWorld:
    """Labs (Navaneeth and Leela Admins; Deepika, Tamil, Chitra and Ravi
    Members; Devansh a Viewer) and Academy (Navaneeth); Acme, Deepa and the
    Acme deal in Labs and College in Academy, made through the CRM routes;
    Lumen (confirmed, Deepika allocated), Orbit (tentative) and Old
    (archived) TVA projects."""

    def __init__(self, monkeypatch) -> None:
        self.fake = FakeCrmStore().install(monkeypatch)
        self.labs = self.fake.add_workspace("labs", "Nunnari Labs", tva_linked=True)
        self.academy = self.fake.add_workspace("academy", "Nunnari Academy B2B")
        self.fake.add_deal_lists(self.labs)
        self.fake.add_member(self.labs, ID["owner"], "admin", last_used_at=_now())
        self.fake.add_member(self.academy, ID["owner"], "admin")
        self.fake.add_member(self.labs, ID["labsadmin"], "admin")
        for key in ("member", "seller", "creator"):
            self.fake.add_member(self.labs, ID[key], "member")
        # Ravi is removed from Labs by the tests that need it (FR-TASK-VIS-06).
        self.removed = self.fake.add_member(self.labs, ID["removed"], "member")
        self.fake.add_member(self.labs, ID["viewer"], "viewer")
        self.http = client(monkeypatch, TASK_PEOPLE, fake=self.fake)
        monkeypatch.setattr(service, "_now", lambda: NOW)
        # S2a: each delegation message is sent at once, through the real
        # sender, and kept here instead of going to Slack or email.
        self.messages: list[notify.Message] = []
        monkeypatch.setattr(notices, "enqueue", notices.send)
        monkeypatch.setattr(notify, "deliver", self._deliver)
        labs, me = "?workspace=labs", as_(ID["owner"])
        self.acme = self._made(f"/api/v1/crm/companies{labs}", {"name": "Acme"})
        self.deepa = self._made(f"/api/v1/crm/people{labs}", {"first_name": "Deepa"})
        self.http.patch(
            f"/api/v1/crm/companies/{self.acme['id']}{labs}",
            json={"lead_stage": "qualified"},
            headers=me,
        )
        self.deal = self._made(
            f"/api/v1/crm/opportunities{labs}",
            {
                "name": "Acme RDX",
                "company_id": self.acme["id"],
                "next_step": "Call",
                "next_step_on": "2026-10-20",
            },
        )
        self.college = self._made("/api/v1/crm/companies?workspace=academy", {"name": "College"})
        self.lumen = self.fake.add_project("Lumen", status="confirmed")
        self.orbit = self.fake.add_project("Orbit", status="tentative")
        self.old = self.fake.add_project("Old", status="confirmed", is_archived=True)
        self.fake.add_allocation(self.lumen, ID["member"])

    def _deliver(self, message: notify.Message) -> list[str]:
        self.messages.append(message)
        return ["slack"]

    def _made(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self.http.post(path, json=body, headers=as_(ID["owner"]))
        assert r.status_code == 201, r.text
        return r.json()

    @property
    def tasks(self) -> dict[str, dict[str, Any]]:
        return self.fake.rows["tasks"]

    def call(self, who: str, method: str, path: str, **kwargs: Any):
        """`/api/v1/tasks…` as `who`, a key of `ID`."""
        return self.http.request(method, f"/api/v1/tasks{path}", headers=as_(ID[who]), **kwargs)

    def add(self, who: str, **body: Any) -> dict[str, Any]:
        """`POST /tasks` as `who`; the task as returned. An `assignee_id` or
        `waiting_on_id` may be given by its key."""
        for name in ("assignee_id", "waiting_on_id"):
            if body.get(name) in ID:
                body[name] = ID[body[name]]
        r = self.call(who, "POST", "", json={"title": "Call Deepak", **body})
        assert r.status_code == 201, r.text
        return r.json()

    def link(self, record: dict[str, Any], record_type: str) -> dict[str, str]:
        return {"record_type": record_type, "record_id": record["id"]}
