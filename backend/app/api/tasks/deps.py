"""The task guard — spec 008 FR-TASK-ROLL-03, FR-TASK-CHG-02; plan §5.1;
contracts §0 "Who", "Phase guard", "Channel".

Every `/tasks` route depends on one of the guards below. Each runs on
`any_account`, not TVA's `current_user`: a CRM-only seller uses My Tasks too
(T6). It then refuses 403 "Tasks are not switched on for you yet." when the
caller's effective level (their own, else the global default, read once per
request) is below the route's phase, and hands the route a `TaskActor` built
from the request: the user, kind `user` and the channel `deps._authenticate`
set (`mcp` only through the in-process bridge).

`tests/test_crm_only_refused.py` asserts that every route under
`/api/v1/tasks` depends on one of these guards.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from typing import Annotated

from fastapi import Depends, Request

from app.api.deps import AnyAccountDep
from app.api.errors import ProblemDetail
from app.domain.tasks import rollout
from app.domain.tasks.service import TaskActor
from app.services.tasks import store


@cache
def tasks_dep(level: int) -> Callable[..., TaskActor]:
    """The guard of phase `level`: one callable per level, so a route's
    dependency tree names it."""

    def guard(request: Request, user: AnyAccountDep) -> TaskActor:
        why = rollout.refusal(rollout.level_for(user.profile, store.default_level()), level)
        if why:
            raise ProblemDetail(403, why)
        return TaskActor(user=user, kind="user", channel=getattr(request.state, "channel", "web"))

    guard.__name__ = f"tasks_level_{level}"
    return guard


TasksDep = Annotated[TaskActor, Depends(tasks_dep(1))]
TasksDep2 = Annotated[TaskActor, Depends(tasks_dep(2))]
TasksDep3 = Annotated[TaskActor, Depends(tasks_dep(3))]
TasksDep4 = Annotated[TaskActor, Depends(tasks_dep(4))]
