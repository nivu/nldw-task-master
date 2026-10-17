"""Who sees, changes and deletes a task — spec 008 §3.1, §3.3, FR-TASK-VIS-01,
-05, -07, -08, FR-TASK-DATA-09; T4, T5; TP-01, TP-02.

`domain/tasks/visibility` is pure: these tests hand it a viewer and a task.

Acceptance criteria (spec §12, phase 1 — privacy):
- "A CRM task is visible to every active workspace member and to no
  non-member, including its creator or assignee after removal from the
  workspace, and including leads, managers and admins outside the workspace
  when it links a project" — TestCrmTasks.
- "A crm_only Labs member sees a deal task that also links a TVA project, and
  the response has no project field" — TestProjectField.
- "A project task with no CRM link is visible to every lead, manager and
  admin, every TVA user who is or was allocated to the project, its creator
  and its assignee, and to no other TVA user or crm_only account; a creator
  or assignee who cannot see the project sees only the project's name" —
  TestProjectTasks, TestProjectField.
- "Someone who sees a project task only through the project rule can read it
  and cannot change it" — TestProjectTasks.
- "A child task whose parent is private shows no parent id or title" —
  TestParent.
- "A private task later linked to a CRM record shows its earlier history only
  to its creator and assignee" — TestHistory.
- "The task panel and the quick-add preview show who will see the task" —
  TestAudience (the sentence; the panel and preview arrive in S1b, S1c).
"""

from __future__ import annotations

import pytest

from app.domain.tasks.visibility import (
    TaskFacts,
    Viewer,
    audience,
    can_see_project,
    history_visible,
    may_delete,
    may_restore,
    parent_field,
    project_field,
    sight,
)

LABS = "ws-labs"
PROJECT = "p-lumen"

ME = Viewer(id="u-me", tva_role="user")
OTHER = Viewer(id="u-other", tva_role="user")
LEAD = Viewer(id="u-lead", tva_role="lead")
MANAGER = Viewer(id="u-manager", tva_role="manager")
ADMIN = Viewer(id="u-admin", tva_role="admin")
ALLOCATED = Viewer(id="u-alloc", tva_role="user", allocated_project_ids=frozenset({PROJECT}))
SELLER = Viewer(id="u-seller", account_type="crm_only", memberships={LABS: "member"})
LABS_ADMIN = Viewer(id="u-labs-admin", tva_role="user", memberships={LABS: "admin"})
LABS_MEMBER = Viewer(id="u-labs-member", tva_role="user", memberships={LABS: "member"})
LABS_VIEWER = Viewer(id="u-labs-viewer", tva_role="admin", memberships={LABS: "viewer"})


def _task(**values) -> TaskFacts:
    base = {"id": "t1", "workspace_id": None, "created_by": "u-me", "assignee_id": "u-me"}
    return TaskFacts(**{**base, **values})


class TestCanSeeProject:
    """Spec §3.1 "Can see a TVA project", for tasks only."""

    @pytest.mark.parametrize("viewer", [LEAD, MANAGER, ADMIN])
    def test_leads_managers_and_admins_see_every_project(self, viewer):
        assert can_see_project(viewer, PROJECT)

    def test_a_user_sees_projects_they_are_or_were_allocated_to(self):
        assert can_see_project(ALLOCATED, PROJECT)
        assert not can_see_project(OTHER, PROJECT)

    def test_crm_only_sees_none(self):
        allocated_seller = Viewer(
            id="x", account_type="crm_only", tva_role="admin", allocated_project_ids={PROJECT}
        )
        assert not can_see_project(allocated_seller, PROJECT)


class TestPrivateTasks:
    """§3.1 Private: the creator only; §3.3 the creator edits."""

    def test_the_creator_edits(self):
        assert sight(ME, _task()) == "edit"

    @pytest.mark.parametrize("viewer", [OTHER, LEAD, MANAGER, ADMIN, LABS_ADMIN, SELLER])
    def test_nobody_else_sees_it_admins_included(self, viewer):
        assert sight(viewer, _task()) == "none"


class TestSharedPersonalTasks:
    """§3.1 Shared personal: the creator and the current assignee."""

    def test_creator_and_assignee_edit(self):
        task = _task(assignee_id="u-other")
        assert sight(ME, task) == "edit"
        assert sight(OTHER, task) == "edit"

    def test_a_former_assignee_loses_sight(self):
        task = _task(assignee_id="u-lead")
        assert sight(OTHER, task) == "none"

    @pytest.mark.parametrize("viewer", [ADMIN, MANAGER, LABS_ADMIN])
    def test_nobody_else(self, viewer):
        assert sight(viewer, _task(assignee_id="u-other")) == "none"

    def test_only_the_creator_deletes_and_restores(self):
        task = _task(assignee_id="u-other")
        assert may_delete(ME, task) and may_restore(ME, task)
        assert not may_delete(OTHER, task) and not may_restore(OTHER, task)


class TestCrmTasks:
    """§3.1 CRM: every active member of the workspace, and nobody else; §3.3
    the CRM role decides (`007` §3.3, Q-57)."""

    def test_admins_and_members_edit(self):
        task = _task(workspace_id=LABS, created_by="u-x", assignee_id="u-x")
        assert sight(LABS_ADMIN, task) == "edit"
        assert sight(LABS_MEMBER, task) == "edit"
        assert sight(SELLER, task) == "edit"

    def test_a_viewer_reads_and_changes_only_the_status_of_their_own(self):
        theirs = _task(workspace_id=LABS, created_by="u-x", assignee_id="u-labs-viewer")
        other = _task(workspace_id=LABS, created_by="u-x", assignee_id="u-x")
        assert sight(LABS_VIEWER, theirs) == "status_only"
        assert sight(LABS_VIEWER, other) == "read_only"

    def test_creator_and_assignee_outside_the_workspace_get_nothing(self):
        task = _task(workspace_id=LABS)  # created by and assigned to u-me
        assert sight(ME, task) == "none"

    @pytest.mark.parametrize("viewer", [LEAD, MANAGER, ADMIN, ALLOCATED])
    def test_a_project_link_never_widens_it(self, viewer):
        task = _task(workspace_id=LABS, created_by="u-x", assignee_id="u-x", project_id=PROJECT)
        assert sight(viewer, task) == "none"

    def test_delete_and_restore_follow_007(self):
        mine = _task(workspace_id=LABS, created_by="u-labs-member", assignee_id="u-x")
        theirs = _task(workspace_id=LABS, created_by="u-x", assignee_id="u-labs-member")
        assert may_delete(LABS_MEMBER, mine) and not may_delete(LABS_MEMBER, theirs)
        assert may_delete(LABS_ADMIN, theirs)
        assert not may_delete(LABS_VIEWER, mine)
        assert may_restore(LABS_ADMIN, mine) and not may_restore(LABS_MEMBER, mine)


class TestProjectTasks:
    """§3.1 Project: whoever can see the project, plus creator and assignee;
    §3.3 read only through the project rule (TP-02)."""

    def _project_task(self, **values):
        return _task(created_by="u-x", assignee_id="u-y", project_id=PROJECT, **values)

    @pytest.mark.parametrize("viewer", [LEAD, MANAGER, ADMIN, ALLOCATED])
    def test_read_only_through_the_project(self, viewer):
        assert sight(viewer, self._project_task()) == "read_only"
        assert not may_delete(viewer, self._project_task())

    @pytest.mark.parametrize("viewer", [OTHER, SELLER])
    def test_no_other_user_and_no_crm_only_account(self, viewer):
        assert sight(viewer, self._project_task()) == "none"

    def test_creator_and_assignee_edit_even_without_the_project(self):
        task = _task(created_by="u-other", assignee_id="u-seller", project_id=PROJECT)
        assert sight(OTHER, task) == "edit"
        assert sight(SELLER, task) == "edit"


class TestProjectField:
    """FR-TASK-VIS-07 — CRM links win."""

    def test_no_project_is_absent(self):
        assert project_field(ADMIN, _task()) == "absent"

    def test_a_crm_member_who_cannot_see_the_project_gets_none(self):
        task = _task(workspace_id=LABS, created_by="u-x", assignee_id="u-x", project_id=PROJECT)
        assert project_field(SELLER, task) == "absent"
        assert project_field(LABS_MEMBER, task) == "absent"

    def test_a_crm_member_who_can_see_it_gets_it(self):
        task = _task(workspace_id=LABS, created_by="u-x", assignee_id="u-x", project_id=PROJECT)
        assert project_field(LABS_VIEWER, task) == "full"  # a TVA admin

    def test_creator_or_assignee_without_sight_gets_the_name_only(self):
        task = _task(created_by="u-other", assignee_id="u-seller", project_id=PROJECT)
        assert project_field(OTHER, task) == "name_only"
        assert project_field(SELLER, task) == "name_only"

    def test_who_can_see_the_project_gets_it(self):
        task = _task(created_by="u-x", assignee_id="u-y", project_id=PROJECT)
        assert project_field(ALLOCATED, task) == "full"


class TestParent:
    """FR-TASK-DATA-09 — a parent the reader cannot see is "a private task"."""

    def test_no_parent(self):
        assert parent_field(ME, None, None) is None

    def test_a_parent_they_can_see(self):
        assert parent_field(ME, _task(id="p"), "Plan") == {"id": "p", "title": "Plan"}

    def test_a_private_parent_shows_no_id_or_title(self):
        parent = _task(id="p", created_by="u-other", assignee_id="u-other")
        assert parent_field(ME, parent, "Secret") == {"private": True}


class TestAudience:
    """FR-TASK-VIS-08 — the "Visible to" sentence."""

    def test_only_you(self):
        draft = {"viewer_id": "u-me", "created_by": "u-me", "assignee_id": "u-me"}
        assert audience(draft, {}) == "Visible to: only you"

    def test_you_and_the_assignee(self):
        draft = {"viewer_id": "u-me", "created_by": "u-me", "assignee_id": "u-t"}
        assert audience(draft, {"u-t": "Timothy Paul"}) == "Visible to: you and Timothy Paul"

    def test_the_assignee_sees_the_creator_named(self):
        draft = {"viewer_id": "u-t", "created_by": "u-me", "assignee_id": "u-t"}
        assert audience(draft, {"u-me": "Navaneeth"}) == "Visible to: you and Navaneeth"

    def test_a_project(self):
        draft = {"viewer_id": "u-me", "created_by": "u-me", "project_name": "Lumen"}
        assert audience(draft, {}) == (
            "Visible to: leads, managers, admins and people allocated to project Lumen"
        )

    def test_a_workspace_wins_over_a_project(self):
        draft = {"workspace_name": "Nunnari Labs", "project_name": "Lumen"}
        assert audience(draft, {}) == "Visible to: Nunnari Labs members"


class TestHistory:
    """FR-TASK-VIS-05 — private-era rows only to the creator and the assignee
    at that row."""

    ERA = {"created_by": "u-me", "assignee_id": "u-t"}

    def test_a_private_era_row_is_shown_to_creator_and_then_assignee(self):
        row = {"workspace_id": None, "target_table": "tasks"}
        assert history_visible(ME, row, self.ERA)
        assert history_visible(Viewer(id="u-t"), row, self.ERA)

    @pytest.mark.parametrize("viewer", [LABS_ADMIN, LABS_MEMBER, ADMIN, OTHER])
    def test_not_to_members_or_anyone_who_gained_sight_later(self, viewer):
        row = {"workspace_id": None, "target_table": "tasks"}
        assert not history_visible(viewer, row, self.ERA)

    def test_a_row_written_with_a_workspace_follows_the_task(self):
        row = {"workspace_id": LABS, "target_table": "tasks"}
        assert history_visible(LABS_MEMBER, row, self.ERA)

    def test_a_project_link_added_to_a_labs_task_is_not_private_era(self):
        """048: a `tva_project` link row is audited with its task's workspace."""
        row = {"workspace_id": LABS, "target_table": "task_links", "after": {"workspace_id": None}}
        assert history_visible(LABS_MEMBER, row, self.ERA)
