"""In-memory task store — spec 008 tasks.md "How to use this file".

`FakeTaskStore` implements every public name of `app.services.tasks.store`
over the **same** in-memory tables as `tests/crm_fake.py` (`tasks`,
`task_links`, the memberships, projects and allocations), with Python fakes
of `task_apply` (049), the deferred workspace check (047) and
`task_purge_expired` (049), so route tests never touch a database. The real
functions are covered by `tests/db/`.

`FakeCrmStore.install` installs one beside itself, so a CRM route that
presents a task (its project link, FR-TASK-VIS-07) reads the same rows.
"""

from __future__ import annotations

import inspect
import uuid
from typing import Any

from crm_fake import (
    DEFAULTS,
    PEOPLE,
    FakeCrmStore,
    _at,
    _now,
    touched_tasks,
)

from app.services.tasks import store

#: 049: the tables `task_apply` writes; the later ones arrive in 051-055.
TASK_TABLES = ("tasks", "task_links")


def public_task_store_names() -> list[str]:
    """Every function the real task store exports — what the fake replaces."""
    return [
        name
        for name, value in vars(store).items()
        if inspect.isfunction(value) and value.__module__ == store.__name__ and name[0] != "_"
    ]


def _refuse(code: str, message: str) -> Exception:
    return store.refusal_for(code, message)


class FakeTaskStore:
    def __init__(self, crm: FakeCrmStore) -> None:
        self.crm = crm
        # `app_settings.tasks_default_level` (050).
        self.default = 0
        # Every task_apply call: (actor, ops).
        self.applied: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        # Every task_purge_expired call: (actor, before).
        self.purges: list[tuple[dict[str, Any], str]] = []

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
                    result["row"] = dict(row)
            self.crm.workspace_check(touched_tasks(ops, done))
            return done
        except Exception:
            self.crm.rows, self.crm.audit[audit_len:] = snapshot, []
            raise

    def _allowed(self, actor: dict[str, Any], table: str, subject: dict[str, Any]) -> bool:
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

    def _one(self, actor: dict[str, Any], op: dict[str, Any], refs: dict[str, str]) -> dict:
        table = op.get("table")
        if table not in TASK_TABLES:
            raise _refuse("CRM22", "That is not a task table.")
        if op.get("op") not in ("insert", "update", "soft_delete", "restore", "delete"):
            raise _refuse("CRM22", "Unknown task operation.")
        values = {
            k: refs[v[5:]] if isinstance(v, str) and v.startswith("$ref:") else v
            for k, v in (op.get("values") or {}).items()
        }
        for k in ("id", "created_at", "created_by"):
            if k in values:
                raise _refuse("CRM22", f"{k} cannot be set here.")
        who = actor.get("user_id")
        rows = self.rows[table]
        if op["op"] == "insert":
            row = {
                "id": str(uuid.uuid4()),
                "workspace_id": None,
                "created_at": _now(),
                "created_by": who,
                "updated_at": _now(),
                **({"updated_by": who} if table == "tasks" else {}),
                **DEFAULTS.get(table, {}),
                **values,
            }
            if not self._allowed(actor, table, row):
                raise _refuse("CRM04", "No such task.")
            if table == "tasks":
                self._parent_check(row)
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
            if table != "tasks":
                raise _refuse("CRM22", "This kind of row cannot be deleted.")
            deleting = op["op"] == "soft_delete"
            values = {
                "deleted_at": _now() if deleting else None,
                "deleted_by": who if deleting else None,
                "deleted_group": op.get("deleted_group") if deleting else None,
            }
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
