"""The morning brief — spec 008 FR-TASK-BRIEF-01 to -03; Journey 4.8;
contracts §2 `GET /tasks/brief`; plan §6 "§6.3 brief".

One screen and one tool (`task_brief`), never a pushed message (TP-08): the
caller's Today and overdue tasks and their waiting tasks due a nudge
(`domain/tasks/brief`), each through the viewer filter and `present`
(FR-TASK-VIS-04), and, for each CRM workspace they are a member of now, that
workspace's CRM Today sections exactly as `services/crm/today.build` gives
them, read from it and not recomputed (FR-TASK-BRIEF-01).
"""

from __future__ import annotations

from typing import Any

from app.domain.tasks import brief, delegation, rules, service
from app.domain.tasks.service import TaskActor
from app.services.crm import today as crm_today
from app.services.tasks import listing, present, store, viewer


def build(actor: TaskActor) -> dict[str, Any]:
    """`{as_of, today, overdue, nudge, workspaces}` for the caller."""
    now = service._now()
    profile = actor.user.profile
    zone = profile.get("timezone") or rules.DEFAULT_ZONE
    today = rules.day_of(now, zone)
    me = viewer.load(actor.user)

    mine = listing._seen(me, store.assigned_tasks(actor.user.id, rules.OPEN))
    on_today, overdue = brief.today_and_overdue(mine, today)
    days = profile.get("task_nudge_days") or delegation.DEFAULT_NUDGE_DAYS
    waiting = listing._seen(me, store.created_tasks(actor.user.id, ("waiting",)))
    due = brief.nudges(waiting, today, zone, days)
    nudge = present.present(me, [row for row, _ in due], today=today)
    for task, (_, idle) in zip(nudge, due, strict=True):
        task["idle_days"] = idle

    ws_ids = [m["workspace_id"] for m in store.memberships_of(actor.user.id)]
    workspaces = sorted(store.get_workspaces(ws_ids), key=lambda w: (w["name"], w["id"]))
    return {
        "as_of": today.isoformat(),
        "today": present.present(me, on_today, today=today),
        "overdue": present.present(me, overdue, today=today),
        "nudge": nudge,
        "workspaces": [
            {
                "workspace": {"key": w["key"], "name": w["name"]},
                "sections": crm_today.build(w["id"], actor.user.id, profile, now)["sections"],
            }
            for w in workspaces
        ],
    }
