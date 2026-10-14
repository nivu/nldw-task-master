"""The TVA Admin "CRM workspaces" panel — spec 007 FR-CRM-ACC-06, -13, -14,
FR-CRM-CHG-11; contracts §2.4; plan §4 CT-3, §5.13.

Runs against `tests/crm_fake.py`; `convert_account` there mirrors
`crm_convert_account`'s refusals, which `tests/db/test_crm_audit_db.py`
proves on the real function (the allocation race included). Vinita
(`OUTSIDER`) is a TVA admin in no workspace.
"""

from __future__ import annotations

import pytest
from crm_fake import MEMBER, OUTSIDER, OWNER, SELLER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.services import notify
from app.services.crm import members

TVA = "/api/v1/crm/tva-admin"


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    labs["allowed_domains"][:] = ["nunnarilabs.com", "deepweaver.ai"]
    http = crm_client(monkeypatch, fake=fake)
    monkeypatch.setattr(members, "_create_sign_in", lambda email, name: "u-new")
    monkeypatch.setattr(members, "_delete_sign_in", lambda user_id: None)
    monkeypatch.setattr(notify, "deliver", lambda m: [])
    return http, fake, labs, academy


class TestWhoGetsIn:
    """Contracts §2.4 — TVA `AdminDep`: staff admins, no membership needed."""

    @pytest.mark.parametrize("who", [MEMBER["id"], VIEWER["id"]])
    def test_staff_who_are_not_tva_admins_are_403(self, crm, who):
        http, *_ = crm
        assert http.get(f"{TVA}/workspaces", headers=as_(who)).status_code == 403

    def test_a_crm_only_account_is_refused(self, crm):
        http, *_ = crm
        r = http.get(f"{TVA}/workspaces", headers=as_(SELLER["id"]))
        assert (r.status_code, r.json()["detail"]) == (403, "This account is for the CRM only.")

    def test_a_tva_admin_sees_every_workspace_and_its_members(self, crm):
        """FR-CRM-ACC-13."""
        http, _, labs, _ = crm
        rows = http.get(f"{TVA}/workspaces", headers=as_(OUTSIDER["id"])).json()
        assert [(w["key"], w["member_count"]) for w in rows] == [("academy", 2), ("labs", 4)]
        members_ = http.get(
            f"{TVA}/workspaces/{labs['id']}/members", headers=as_(OUTSIDER["id"])
        ).json()
        assert {
            (m["display_name"], m["email"], m["role"], m["account_type"]) for m in members_
        } >= {("Tamil", SELLER["email"], "member", "crm_only")}

    def test_seeing_them_grants_no_crm_workspace(self, crm):
        http, *_ = crm
        http.get(f"{TVA}/workspaces", headers=as_(OUTSIDER["id"]))
        assert http.get("/api/v1/crm/workspaces", headers=as_(OUTSIDER["id"])).json() == []
        r = http.get("/api/v1/crm/members?workspace=labs", headers=as_(OUTSIDER["id"]))
        assert r.status_code == 404

    def test_an_unknown_workspace_is_404(self, crm):
        http, *_ = crm
        r = http.get(f"{TVA}/workspaces/nowhere/members", headers=as_(OUTSIDER["id"]))
        assert (r.status_code, r.json()["detail"]) == (404, "No such workspace.")


class TestAllowedDomains:
    """FR-CRM-ACC-06, amended; plan §5.13."""

    def test_a_workspace_admin_cannot_widen_their_own(self, crm):
        http, _, labs, _ = crm
        r = http.patch(
            "/api/v1/crm/settings/allowed_domains?workspace=labs",
            json={"value": ["gmail.example"]},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 403
        # The panel's route is TVA AdminDep: a workspace role opens nothing.
        r = http.put(
            f"{TVA}/workspaces/{labs['id']}/allowed-domains",
            json={"domains": ["example.com"]},
            headers=as_(MEMBER["id"]),
        )
        assert r.status_code == 403

    def test_a_tva_admin_sets_them_cleaned(self, crm):
        http, fake, labs, _ = crm
        r = http.put(
            f"{TVA}/workspaces/{labs['id']}/allowed-domains",
            json={"domains": [" NunnariLabs.com", "deepweaver.ai", "@newco.in", "nunnarilabs.com"]},
            headers=as_(OUTSIDER["id"]),
        )
        assert r.status_code == 200
        assert r.json()["allowed_domains"] == ["nunnarilabs.com", "deepweaver.ai", "newco.in"]
        actor, (op,) = fake.applied[-1]
        assert (actor["user_id"], actor["workspace_id"]) == (OUTSIDER["id"], labs["id"])
        assert (op["table"], op["action"]) == ("crm_workspaces", "workspace.allowed_domains")
        assert (
            fake.rows["crm_workspaces"][labs["id"]]["allowed_domains"]
            == r.json()["allowed_domains"]
        )

    @pytest.mark.parametrize("webmail", ["gmail.com", "GMAIL.com", "outlook.com", "proton.me"])
    def test_public_webmail_is_refused(self, crm, webmail):
        http, fake, labs, _ = crm
        r = http.put(
            f"{TVA}/workspaces/{labs['id']}/allowed-domains",
            json={"domains": ["nunnarilabs.com", webmail]},
            headers=as_(OUTSIDER["id"]),
        )
        assert r.status_code == 422
        assert r.json()["detail"] == (
            f"{webmail.lower()} is a public email domain and cannot be on an allow-list."
        )
        assert fake.applied == []

    def test_something_that_is_not_a_domain_is_refused(self, crm):
        http, _, labs, _ = crm
        r = http.put(
            f"{TVA}/workspaces/{labs['id']}/allowed-domains",
            json={"domains": ["not a domain"]},
            headers=as_(OUTSIDER["id"]),
        )
        assert (r.status_code, r.json()["detail"]) == (422, "not a domain is not a domain name.")


class TestAccountType:
    """Contracts §2.4 `PATCH /crm/tva-admin/accounts/{user_id}`; FR-CRM-CHG-02."""

    def _convert(self, http, user_id, to="crm_only"):
        return http.patch(
            f"{TVA}/accounts/{user_id}", json={"account_type": to}, headers=as_(OUTSIDER["id"])
        )

    def test_staff_becomes_crm_only_with_every_tva_right_off(self, crm):
        http, fake, *_ = crm
        fake.profiles[MEMBER["id"]].update(logs_time=True, dashboard_access=True)
        r = self._convert(http, MEMBER["id"])
        assert r.status_code == 200
        body = r.json()
        assert (
            body["account_type"],
            body["role"],
            body["logs_time"],
            body["lead_id"],
            body["dashboard_access"],
            body["changed"],
        ) == ("crm_only", "user", False, None, False, True)
        r = http.get("/api/v1/me/home", headers=as_(MEMBER["id"]))
        assert r.status_code == 403

    def test_the_owner_is_refused(self, crm):
        http, *_ = crm
        r = self._convert(http, OWNER["id"])
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "The owner's account cannot be CRM-only.",
        )

    def test_someone_who_leads_people_is_refused(self, crm):
        http, fake, *_ = crm
        fake.profiles[MEMBER["id"]]["lead_id"] = VIEWER["id"]
        r = self._convert(http, VIEWER["id"])
        assert r.status_code == 422 and "lead" in r.json()["detail"]

    def test_a_project_lead_is_refused(self, crm):
        http, fake, *_ = crm
        fake.project_leads.add(VIEWER["id"])
        r = self._convert(http, VIEWER["id"])
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "This person leads a project; give it another lead first.",
        )

    def test_someone_holding_an_allocation_is_refused(self, crm):
        http, fake, *_ = crm
        fake.allocated.add(MEMBER["id"])
        r = self._convert(http, MEMBER["id"])
        assert (r.status_code, r.json()["detail"]) == (
            422,
            "This person is allocated to a project; remove the allocation first.",
        )

    def test_crm_only_back_to_staff(self, crm):
        http, *_ = crm
        r = self._convert(http, SELLER["id"], "staff")
        assert r.status_code == 200 and r.json()["account_type"] == "staff"

    def test_no_change_is_not_a_change(self, crm):
        http, fake, *_ = crm
        r = self._convert(http, SELLER["id"], "crm_only")
        assert r.json()["changed"] is False
        assert not [a for a in fake.audit if a["action"] == "user.account_type"]

    def test_an_admin_cannot_make_themselves_crm_only(self, crm):
        http, *_ = crm
        assert self._convert(http, OUTSIDER["id"]).status_code == 422

    def test_an_unknown_person_is_404(self, crm):
        http, *_ = crm
        assert self._convert(http, "u-nobody").status_code == 404


class TestAdministrationAudit:
    """Contracts §2.4 `GET /crm/tva-admin/audit`; plan CT-3 — the events, never
    the record content."""

    def test_every_kind_of_event_is_listed_without_content(self, crm):
        http, fake, labs, academy = crm
        admin = as_(OUTSIDER["id"])
        http.post(
            f"{TVA}/workspaces/{labs['id']}/invitations",
            json={"email": "rep@deepweaver.ai", "display_name": "Rep", "role": "member"},
            headers=admin,
        )
        http.put(
            f"{TVA}/workspaces/{academy['id']}/allowed-domains",
            json={"domains": ["nunnari.academy"]},
            headers=admin,
        )
        http.patch(
            f"{TVA}/accounts/{MEMBER['id']}", json={"account_type": "crm_only"}, headers=admin
        )
        # A CRM record-ish change (a setting) must not appear.
        http.patch(
            "/api/v1/crm/settings/stage_probabilities?workspace=labs",
            json={
                "value": {
                    "discovery": 5,
                    "qualification": 25,
                    "proposal": 50,
                    "negotiation": 75,
                    "closed_won": 100,
                    "closed_lost": 0,
                }
            },
            headers=as_(OWNER["id"]),
        )
        rows = http.get(f"{TVA}/audit", headers=admin).json()
        actions = [r["action"] for r in rows]
        assert actions == [
            "user.account_type",
            "workspace.allowed_domains",
            "membership.invited",
            "user.created",
        ]
        for row in rows:
            assert set(row) == {
                "id",
                "at",
                "workspace_key",
                "actor_name",
                "action",
                "target_name",
                "role",
            }
        invited = next(r for r in rows if r["action"] == "membership.invited")
        assert (invited["workspace_key"], invited["target_name"], invited["role"]) == (
            "labs",
            "Rep",
            "member",
        )
        assert invited["actor_name"] == "Vinita"
        domains = next(r for r in rows if r["action"] == "workspace.allowed_domains")
        assert (domains["workspace_key"], domains["target_name"]) == (
            "academy",
            "Nunnari Academy B2B",
        )
        changed = next(r for r in rows if r["action"] == "user.account_type")
        assert changed["target_name"] == "Deepika" and changed["workspace_key"] is None

    def test_filtered_to_a_workspace(self, crm):
        http, fake, labs, academy = crm
        admin = as_(OUTSIDER["id"])
        http.put(
            f"{TVA}/workspaces/{academy['id']}/allowed-domains",
            json={"domains": ["nunnari.academy"]},
            headers=admin,
        )
        assert http.get(f"{TVA}/audit?workspace_id={labs['id']}", headers=admin).json() == []
        rows = http.get(f"{TVA}/audit?workspace_id=academy", headers=admin).json()
        assert [r["action"] for r in rows] == ["workspace.allowed_domains"]

    def test_a_removal_names_the_person(self, crm):
        http, fake, labs, _ = crm
        m = next(m for m in fake.rows["crm_memberships"].values() if m["user_id"] == SELLER["id"])
        http.delete(f"{TVA}/memberships/{m['id']}", headers=as_(OUTSIDER["id"]))
        rows = http.get(f"{TVA}/audit", headers=as_(OUTSIDER["id"])).json()
        assert [(r["action"], r["target_name"]) for r in rows] == [
            ("user.deactivated", "Tamil"),
            ("membership.removed", "Tamil"),
        ]
