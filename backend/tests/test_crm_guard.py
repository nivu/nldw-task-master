"""The CRM guard — spec 007 FR-CRM-WS-02, -03, -04, FR-CRM-MCP-03, -04,
FR-CRM-ACC-02, -10; D3, D13; AC "A write by a two-workspace member naming no
workspace is refused".

Runs the real dependency chain (`any_account` → `api/crm/deps.crm_context`)
against `tests/crm_fake.py`. The cast is `crm_fake.two_workspaces`: the owner
administers both workspaces, Deepika is a member of both and used Academy
last, Devansh views Labs, Tamil (CRM-only) is a Labs member, and Vinita, a
TVA admin, belongs to neither.
"""

from __future__ import annotations

import inspect

import pytest
from crm_fake import (
    MEMBER,
    OUTSIDER,
    OWNER,
    SELLER,
    VIEWER,
    FakeCrmStore,
    as_,
    public_store_names,
    two_workspaces,
)
from crm_fake import client as crm_client

from app.api.crm.deps import CrmContext, crm_labs, crm_write
from app.api.deps import CurrentUser
from app.api.errors import ProblemDetail
from app.services.crm import store

PROBABILITIES = {
    "discovery": 5,
    "qualification": 25,
    "proposal": 50,
    "negotiation": 75,
    "closed_won": 100,
    "closed_lost": 0,
}


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    return crm_client(monkeypatch), fake, labs, academy


class TestReadsDefaultToLastUsed:
    """FR-CRM-WS-04, FR-CRM-MCP-03, D13."""

    def test_a_read_without_a_workspace_uses_the_last_used(self, crm):
        http, *_ = crm
        body = http.get("/api/v1/crm/bootstrap", headers=as_(MEMBER["id"])).json()
        assert body["workspace"]["key"] == "academy"
        assert body["role"] == "member"

    def test_someone_who_never_switched_gets_the_first_by_name(self, crm):
        http, *_ = crm
        body = http.get("/api/v1/crm/bootstrap", headers=as_(OWNER["id"])).json()
        assert body["workspace"]["key"] == "academy"

    def test_a_workspace_may_be_named_by_key_or_id(self, crm):
        http, _, labs, _ = crm
        for name in ("labs", labs["id"]):
            body = http.get(f"/api/v1/crm/bootstrap?workspace={name}", headers=as_(MEMBER["id"]))
            assert body.json()["workspace"]["key"] == "labs"

    def test_use_moves_the_default(self, crm):
        http, *_ = crm
        assert http.post("/api/v1/crm/workspaces/labs/use", headers=as_(MEMBER["id"])).json() == {
            "ok": True
        }
        body = http.get("/api/v1/crm/bootstrap", headers=as_(MEMBER["id"])).json()
        assert body["workspace"]["key"] == "labs"
        listed = http.get("/api/v1/crm/workspaces", headers=as_(MEMBER["id"])).json()
        assert {w["key"]: w["last_used"] for w in listed} == {"labs": True, "academy": False}

    def test_a_viewer_and_a_crm_only_seller_may_record_it(self, crm):
        """The CRM shell records the workspace on every load, whatever the
        role, so recording it must never refuse a Viewer or a CRM-only seller."""
        http, fake, labs, _ = crm
        for who in (VIEWER, SELLER):
            r = http.post("/api/v1/crm/workspaces/labs/use", headers=as_(who["id"]))
            assert r.status_code == 200, r.text
            mine = next(
                m
                for m in fake.rows["crm_memberships"].values()
                if m["user_id"] == who["id"] and m["workspace_id"] == labs["id"]
            )
            assert mine["last_used_at"]

    def test_use_writes_no_audit_row(self, crm):
        """data-model §3.2: switching workspace is not a change to audit."""
        http, fake, *_ = crm
        http.post("/api/v1/crm/workspaces/labs/use", headers=as_(MEMBER["id"]))
        assert fake.audit == [] and fake.applied == []


class TestWritesNameTheirWorkspace:
    """FR-CRM-MCP-04, D13."""

    def test_a_two_workspace_member_naming_none_is_refused_with_both(self, crm):
        http, *_ = crm
        r = http.patch(
            "/api/v1/crm/settings/stage_probabilities",
            json={"value": PROBABILITIES},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422
        assert r.json()["detail"] == (
            "Name a workspace: Nunnari Academy B2B (academy), Nunnari Labs (labs)."
        )

    def test_naming_it_lets_the_write_through(self, crm):
        http, fake, labs, _ = crm
        r = http.patch(
            "/api/v1/crm/settings/stage_probabilities?workspace=labs",
            json={"value": PROBABILITIES},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200
        actor, _ = fake.applied[-1]
        assert actor["workspace_id"] == labs["id"]

    def test_a_one_workspace_member_writes_where_they_belong(self, crm):
        """Tamil is in Labs only: the write resolves there, then meets the
        role rule (a member is not an admin) rather than the workspace rule."""
        http, *_ = crm
        r = http.patch(
            "/api/v1/crm/settings/stage_probabilities",
            json={"value": PROBABILITIES},
            headers=as_(SELLER["id"]),
        )
        assert r.status_code == 403
        assert r.json()["detail"] == "Only a workspace admin can do that."


class TestIsolation:
    """FR-CRM-WS-02 — not a member reads as not there."""

    def test_a_workspace_you_are_not_in_is_not_found(self, crm):
        http, *_ = crm
        r = http.get("/api/v1/crm/lists?workspace=academy", headers=as_(VIEWER["id"]))
        assert r.status_code == 404
        assert r.json()["detail"] == "No such workspace."

    def test_a_workspace_that_does_not_exist_reads_the_same(self, crm):
        http, *_ = crm
        r = http.get("/api/v1/crm/lists?workspace=sales", headers=as_(VIEWER["id"]))
        assert (r.status_code, r.json()["detail"]) == (404, "No such workspace.")

    def test_a_tva_admin_gets_no_workspace_from_their_tva_role(self, crm):
        """FR-CRM-ACC-02."""
        http, *_ = crm
        assert http.get("/api/v1/crm/workspaces", headers=as_(OUTSIDER["id"])).json() == []
        r = http.get("/api/v1/crm/bootstrap", headers=as_(OUTSIDER["id"]))
        assert r.status_code == 404

    def test_use_of_a_workspace_you_are_not_in_is_not_found(self, crm):
        http, *_ = crm
        r = http.post("/api/v1/crm/workspaces/academy/use", headers=as_(SELLER["id"]))
        assert r.status_code == 404

    def test_the_workspace_list_is_only_your_own(self, crm):
        http, *_ = crm
        listed = http.get("/api/v1/crm/workspaces", headers=as_(SELLER["id"])).json()
        assert [(w["key"], w["role"]) for w in listed] == [("labs", "member")]


class TestCrmOnlyReachesTheCrm:
    """FR-CRM-CHG-01 — the CRM guard sits on `any_account`."""

    def test_a_crm_only_seller_gets_their_bootstrap(self, crm):
        http, *_ = crm
        body = http.get("/api/v1/crm/bootstrap", headers=as_(SELLER["id"])).json()
        assert body["me"] == {
            "id": SELLER["id"],
            "display_name": "Tamil",
            "account_type": "crm_only",
            "timezone": "Australia/Sydney",
            "number_format": "full",
        }

    def test_me_lists_the_workspaces(self, crm):
        http, *_ = crm
        body = http.get("/api/v1/me", headers=as_(SELLER["id"])).json()
        assert [(w["key"], w["role"], w["last_used"]) for w in body["crm"]["workspaces"]] == [
            ("labs", "member", True)
        ]


class TestRoleGuards:
    """Spec §3.3 through the deps: Viewers change nothing; Labs-only routes."""

    @staticmethod
    def _ctx(fake, ws, role) -> CrmContext:
        return CrmContext(user=CurrentUser(VIEWER), workspace=ws, role=role, channel="web")

    def test_a_viewer_cannot_write(self, crm):
        _, fake, labs, _ = crm
        with pytest.raises(ProblemDetail) as refused:
            crm_write(self._ctx(fake, labs, "viewer"))
        assert (refused.value.status, refused.value.detail) == (
            403,
            "Viewers cannot change records.",
        )

    @pytest.mark.parametrize("role", ["admin", "member"])
    def test_admins_and_members_write(self, crm, role):
        _, fake, labs, _ = crm
        assert crm_write(self._ctx(fake, labs, role)).role == role

    def test_the_labs_guard_refuses_academy(self, crm):
        _, fake, labs, academy = crm
        assert crm_labs(self._ctx(fake, labs, "admin")).workspace["key"] == "labs"
        with pytest.raises(ProblemDetail) as refused:
            crm_labs(self._ctx(fake, academy, "admin"))
        assert refused.value.status == 422

    def test_the_channel_is_recorded_as_web_by_default(self, crm):
        http, fake, *_ = crm
        http.patch(
            "/api/v1/crm/settings/stage_probabilities?workspace=labs",
            json={"value": PROBABILITIES},
            headers={**as_(OWNER["id"]), "X-Portal-Client": "mcp"},
        )
        actor, _ = fake.applied[-1]
        assert (actor["channel"], actor["kind"], actor["user_id"]) == ("web", "user", OWNER["id"])


class TestStore:
    """Plan §5.2 — every read is scoped by workspace, and the fake keeps up."""

    def test_every_read_takes_the_workspace_first(self):
        for name in public_store_names():
            if name in store.ACCOUNT_LEVEL:
                continue
            params = list(inspect.signature(getattr(store, name)).parameters)
            assert params[:1] == ["workspace_id"], name

    def test_the_fake_implements_every_store_name(self):
        missing = [n for n in public_store_names() if not callable(getattr(FakeCrmStore, n, None))]
        assert missing == []

    @pytest.mark.parametrize(("code", "status"), [("CRM04", 404), ("CRM09", 409), ("CRM22", 422)])
    def test_database_refusals_keep_their_sentence(self, code, status):
        refused = store.refusal_for(code, "A workspace needs at least one admin.")
        assert (refused.status, refused.detail) == (status, "A workspace needs at least one admin.")

    def test_a_unique_violation_becomes_a_sentence(self):
        refused = store.refusal_for(
            "23505", 'duplicate key value violates unique constraint "crm_select_options_label"'
        )
        assert (refused.status, refused.detail) == (
            409,
            "This list already has an option with that name.",
        )

    def test_anything_else_is_a_fault(self):
        assert store.refusal_for("42P01", 'relation "crm_x" does not exist') is None
