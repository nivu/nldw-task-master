"""Every cell of spec 007 §3.3 — FR-CRM-ACC-10; D3.

The expected table is written out here, cell by cell, in the spec's words,
never derived from `domain/crm/permissions.py`, so a wrong cell there cannot
hide itself. "Own" cells are checked with and without ownership, the Viewer's
"assigned to them" cell with and without the assignment.
"""

from __future__ import annotations

import pytest

from app.domain.crm.permissions import ACTIONS, ROLES, may

Y, N = True, False
OWN, ASSIGNEE = "own", "assignee"

#: Spec §3.3: (capability, admin, member, viewer).
SPEC = [
    ("read_records", Y, Y, Y),  # Viewer: except amount fields (FR-CRM-ACC-11)
    ("edit_records", Y, Y, N),
    ("soft_delete", Y, OWN, N),  # Q-36
    ("restore_or_purge", Y, N, N),
    ("see_amounts", Y, Y, N),
    ("manage_shared_views", Y, Y, N),  # Q-37
    ("manage_private_views", Y, Y, Y),
    ("administer", Y, N, N),  # settings, lists, FX rates, members, invitations, mailboxes
    ("import", Y, N, N),
    ("export", Y, Y, N),
    ("complete_task", Y, Y, ASSIGNEE),  # Q-57: status only
    ("move_deal", Y, Y, N),
]

CELLS = [
    (action, role, cell)
    for action, *cells in SPEC
    for role, cell in zip(("admin", "member", "viewer"), cells, strict=True)
]


class TestMatrix:
    def test_the_matrix_has_exactly_the_spec_rows(self):
        assert set(ACTIONS) == {row[0] for row in SPEC}

    def test_three_roles(self):
        assert ROLES == ("admin", "member", "viewer")

    @pytest.mark.parametrize(
        ("action", "role", "cell"), CELLS, ids=[f"{a}-{r}" for a, r, _ in CELLS]
    )
    def test_cell(self, action, role, cell):
        if cell == OWN:
            assert may(role, action, owns=True) is True
            assert may(role, action, owns=False) is False
        elif cell == ASSIGNEE:
            assert may(role, action, is_assignee=True) is True
            assert may(role, action, is_assignee=False) is False
        else:
            assert may(role, action) is cell
            # Ownership or an assignment widens nothing a cell refuses.
            assert may(role, action, owns=True, is_assignee=True) is cell


class TestUnknowns:
    def test_an_unknown_role_may_do_nothing(self):
        assert not any(may("owner", action) for action in ACTIONS)

    def test_an_unknown_action_is_refused(self):
        assert may("admin", "delete_workspace") is False

    def test_a_tva_role_is_not_a_crm_role(self):
        """FR-CRM-ACC-02 — a TVA manager or admin gains nothing here by it."""
        assert not any(
            may(tva, action) for tva in ("manager", "lead", "user") for action in ACTIONS
        )
