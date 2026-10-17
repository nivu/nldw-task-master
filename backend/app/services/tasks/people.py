"""Who a task may go to, what it may link, and who will see it — spec 008
FR-TASK-DATA-06, -07, -13, FR-TASK-ROLL-04, FR-TASK-VIS-08; contracts §1
`GET /tasks/people`, `GET /tasks/linkable`, `POST /tasks/audience` and the
`assignee` name of `POST /tasks`.

Every answer is for the caller's own draft: people they may assign, records
of workspaces where they are an Admin or Member (FR-TASK-DATA-13), projects
they can see (spec §3.1). Nothing here writes.
"""

from __future__ import annotations

from typing import Any, Final

from app.domain.crm import dedupe
from app.domain.tasks import rollout, visibility
from app.domain.tasks.errors import TaskRefused
from app.domain.tasks.service import NO_SUCH_PROJECT, TaskActor, _workspace_of_links
from app.schemas.tasks import TaskAudienceIn
from app.services.crm import store as crm_store
from app.services.tasks import store, viewer

TOO_SHORT: Final = "Type at least two characters."
NO_SUCH_WORKSPACE: Final = "No such workspace."
NO_SUCH_PERSON: Final = "No such person."
#: How many records of each kind a workspace's search gives (as `crm_search`).
_PER_KIND: Final = 8
_KINDS: Final = (
    ("companies", "company"),
    ("people", "person"),
    ("opportunities", "opportunity"),
    ("campaigns", "campaign"),
)


def _shown(person: dict[str, Any]) -> dict[str, Any]:
    return {"id": person["id"], "display_name": person["display_name"], "email": person["email"]}


def _matches(person: dict[str, Any], q: str | None) -> bool:
    if not q:
        return True
    text = q.strip().lower()
    return (
        text in (person.get("display_name") or "").lower()
        or text in (person.get("email") or "").lower()
    )


def people(
    actor: TaskActor,
    *,
    q: str | None = None,
    workspace_id: str | None = None,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Contracts §1 `GET /tasks/people` — for a CRM draft, the workspace's
    members (a `crm_only` member included); for a project draft, staff who
    can see the project with phase 1 switched on; else active accounts with
    phase 1 switched on (FR-TASK-DATA-07, FR-TASK-ROLL-04)."""
    me = viewer.load(actor.user)
    everyone = store.active_profiles()
    default = store.default_level()
    if workspace_id is not None:
        if me.memberships.get(workspace_id) not in ("admin", "member"):
            raise TaskRefused(404, NO_SUCH_WORKSPACE)
        members = store.member_ids(workspace_id)
        found = [p for p in everyone if p["id"] in members]
    elif project_id is not None:
        if not visibility.can_see_project(me, project_id) or not store.get_projects([project_id]):
            raise TaskRefused(404, NO_SUCH_PROJECT)
        allocated = store.project_people(project_id)
        found = [
            p
            for p in everyone
            if (p.get("account_type") or "staff") != "crm_only"
            and (p.get("role") in visibility.PROJECT_ROLES or p["id"] in allocated)
            and rollout.level_for(p, default) >= 1
        ]
    else:
        found = [p for p in everyone if rollout.level_for(p, default) >= 1]
    return [
        _shown(p)
        for p in sorted(found, key=lambda p: ((p.get("display_name") or "").lower(), p["id"]))
        if _matches(p, q)
    ]


def resolve(actor: TaskActor, text: str) -> str:
    """Contracts §1.1 — `assignee` as a name or email: an email, a whole
    display name or a first name, case-insensitive, of an active account.
    No match, or several, is refused with a sentence that names them."""
    wanted = " ".join(text.split()).lower()
    everyone = store.active_profiles()
    by_email = [p for p in everyone if (p.get("email") or "").lower() == wanted]
    if by_email:
        return by_email[0]["id"]
    whole = [p for p in everyone if (p.get("display_name") or "").lower() == wanted]
    found = whole or [
        p for p in everyone if ((p.get("display_name") or "").split() or [""])[0].lower() == wanted
    ]
    if len(found) == 1:
        return found[0]["id"]
    if not found:
        raise TaskRefused(422, f"No one is called {text}.")
    names = sorted(f"{p['display_name']} ({p['email']})" for p in found)
    listed = ", ".join(names[:-1]) + f" and {names[-1]}"
    raise TaskRefused(422, f"{text} could be {listed}; give their email.")


def linkable(actor: TaskActor, q: str) -> dict[str, Any]:
    """Contracts §1 `GET /tasks/linkable` — CRM records only of workspaces
    where the caller is an Admin or Member (FR-TASK-DATA-13), through each
    workspace's own search; projects the caller can see, not archived
    (FR-TASK-DATA-06)."""
    text = " ".join((q or "").split())
    if len(text) < 2:
        raise TaskRefused(422, TOO_SHORT)
    me = viewer.load(actor.user)
    mine = sorted(w for w, role in me.memberships.items() if role in ("admin", "member"))
    workspaces = {w["id"]: w for w in store.get_workspaces(mine)}
    records = []
    for ws_id in mine:
        found = crm_store.search(ws_id, text, _PER_KIND, redact=True)
        for key, record_type in _KINDS:
            for row in found.get(key) or []:
                name = (
                    dedupe.full_name(row.get("first_name"), row.get("last_name"))
                    if record_type == "person"
                    else row.get("name")
                )
                records.append(
                    {
                        "workspace_key": workspaces.get(ws_id, {}).get("key"),
                        "record_type": record_type,
                        "record_id": row["id"],
                        "name": name,
                    }
                )
    projects = [
        {"id": p["id"], "name": p["name"], "status": p["status"]}
        for p in store.projects_named(text)
        if visibility.can_see_project(me, p["id"])
    ]
    return {"records": records, "projects": projects}


def audience(actor: TaskActor, draft: TaskAudienceIn) -> dict[str, str]:
    """Contracts §1 `POST /tasks/audience` — the FR-TASK-VIS-08 sentence for a
    draft, before a link or assignee is saved. A record or project the caller
    may not link is 404, as on create; so is an assignee `GET /tasks/people`
    would not offer for this draft."""
    me = viewer.load(actor.user)
    crm_links = [lk for lk in draft.links if lk.record_type != "tva_project"]
    ws_id = _workspace_of_links(me, crm_links)
    projects = [str(lk.record_id) for lk in draft.links if lk.record_type == "tva_project"]
    project = next(iter(store.get_projects(projects[:1])), None) if projects else None
    if projects and (project is None or not visibility.can_see_project(me, projects[0])):
        raise TaskRefused(404, NO_SUCH_PROJECT)
    workspace = next(iter(store.get_workspaces([ws_id])), None) if ws_id else None
    assignee_id = str(draft.assignee_id) if draft.assignee_id else actor.user.id
    if assignee_id != actor.user.id:
        project_id = projects[0] if projects and ws_id is None else None
        offered = people(actor, workspace_id=ws_id, project_id=project_id)
        if assignee_id not in {p["id"] for p in offered}:
            raise TaskRefused(404, NO_SUCH_PERSON)
    names = {p["id"]: p["display_name"] for p in store.get_profiles([assignee_id])}
    return {
        "visible_to": visibility.audience(
            {
                "viewer_id": actor.user.id,
                "created_by": actor.user.id,
                "assignee_id": assignee_id,
                "workspace_name": workspace["name"] if workspace else None,
                "project_name": project["name"] if project else None,
            },
            names,
        )
    }
