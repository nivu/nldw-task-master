"""Invitations and memberships — spec 007 FR-CRM-ACC-04 to -09, -12, -15,
FR-CRM-CHG-14; owner decision D11; AC "A workspace Admin can invite only
into their workspace, only from its allowed domains, and cannot change
anybody's TVA role".

Routes run against `tests/crm_fake.py`, whose `invite`, `remove_membership`
and `convert_account` mirror the database functions' rules (the real ones are
proved in `tests/db/test_crm_audit_db.py`). GoTrue's admin API is replaced at
`services/crm/members.py`'s two helpers, and delivery at `notify.deliver`.

The cast is `crm_fake.two_workspaces` plus Sanjeev, an Academy admin with no
TVA rights, and the allow-lists of FR-CRM-ACC-06.
"""

from __future__ import annotations

import pytest
from crm_fake import (
    MEMBER,
    OUTSIDER,
    OWNER,
    PEOPLE,
    SELLER,
    VIEWER,
    FakeCrmStore,
    as_,
    two_workspaces,
)
from crm_fake import client as crm_client

from app.api import deps
from app.domain.crm import invites as rules
from app.services import notify
from app.services.crm import members

SANJEEV = {
    "id": "u-sanjeev",
    "email": "sanjeev@nunnari.academy",
    "display_name": "Sanjeev",
    "role": "user",
    "lead_id": None,
    "is_active": True,
    "account_type": "staff",
}
STAFF_ON_GMAIL = {
    "id": "u-tarun",
    "email": "tarun.nl@gmail.com",
    "display_name": "Tarun",
    "role": "user",
    "lead_id": None,
    "is_active": True,
    "account_type": "staff",
}
LABS = "?workspace=labs"
ACADEMY = "?workspace=academy"


class GoTrue:
    """The two GoTrue admin calls an invitation makes, recorded."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []

    def create(self, email: str, display_name: str) -> str:
        self.created.append(email)
        return f"u-new-{len(self.created)}"

    def delete(self, user_id: str) -> None:
        self.deleted.append(user_id)


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    labs["allowed_domains"][:] = ["nunnarilabs.com", "deepweaver.ai"]
    academy["allowed_domains"][:] = ["nunnari.academy", "nunnarilabs.com"]
    fake.add_member(academy, SANJEEV["id"], "admin")
    people = {**PEOPLE, SANJEEV["id"]: SANJEEV, STAFF_ON_GMAIL["id"]: STAFF_ON_GMAIL}
    http = crm_client(monkeypatch, people, fake=fake)
    gotrue = GoTrue()
    monkeypatch.setattr(members, "_create_sign_in", gotrue.create)
    monkeypatch.setattr(members, "_delete_sign_in", gotrue.delete)
    sent: list[notify.Message] = []
    monkeypatch.setattr(notify, "deliver", lambda m: sent.append(m) or ["slack"])
    return http, fake, labs, academy, gotrue, sent


def _invite(http, who, query=ACADEMY, **body):
    payload = {"email": "rep@nunnari.academy", "display_name": "Rep", "role": "member", **body}
    return http.post(f"/api/v1/crm/invitations{query}", json=payload, headers=as_(who))


def _tva_invite(http, ws, who=OUTSIDER["id"], **body):
    payload = {"email": "rep@nunnari.academy", "display_name": "Rep", "role": "member", **body}
    return http.post(
        f"/api/v1/crm/tva-admin/workspaces/{ws['id']}/invitations", json=payload, headers=as_(who)
    )


def _membership(fake, ws, user_id):
    return next(
        m
        for m in fake.rows["crm_memberships"].values()
        if m["workspace_id"] == ws["id"] and m["user_id"] == user_id
    )


class TestNewAccounts:
    """FR-CRM-ACC-04, FR-CRM-CHG-14."""

    def test_a_new_address_is_pre_created_as_crm_only(self, crm):
        http, fake, _, academy, gotrue, _ = crm
        r = _invite(http, SANJEEV["id"])
        assert r.status_code == 201
        body = r.json()
        assert body["created_account"] is True and body["reactivated"] is False
        assert gotrue.created == ["rep@nunnari.academy"]
        profile = fake.profiles[body["user_id"]]
        assert (
            profile["account_type"],
            profile["role"],
            profile["logs_time"],
            profile["lead_id"],
        ) == ("crm_only", "user", False, None)
        assert _membership(fake, academy, body["user_id"])["role"] == "member"

    def test_the_invitee_can_then_sign_in_to_the_crm_only(self, crm):
        http, _, _, _, _, _ = crm
        user_id = _invite(http, SANJEEV["id"]).json()["user_id"]
        listed = http.get("/api/v1/crm/workspaces", headers=as_(user_id)).json()
        assert [w["key"] for w in listed] == ["academy"]
        r = http.get("/api/v1/me/home", headers=as_(user_id))
        assert (r.status_code, r.json()["detail"]) == (403, "This account is for the CRM only.")

    def test_the_screen_gets_the_link_the_delivery_and_the_google_note(self, crm):
        """FR-CRM-ACC-07, -08."""
        http, _, _, _, _, sent = crm
        body = _invite(http, SANJEEV["id"]).json()
        assert body["sign_in_url"].endswith("/auth/login?next=/crm/academy")
        assert body["delivered_via"] == ["slack"]
        assert "Google" in body["note"] and "rep@nunnari.academy" in body["note"]
        (message,) = sent
        assert message.recipient_email == "rep@nunnari.academy"
        assert body["sign_in_url"] in message.body

    def test_nothing_sent_is_said_so(self, crm, monkeypatch):
        http, *_ = crm
        monkeypatch.setattr(notify, "deliver", lambda m: [])
        assert _invite(http, SANJEEV["id"]).json()["delivered_via"] == []

    def test_the_address_is_lower_cased(self, crm):
        http, fake, _, _, gotrue, _ = crm
        body = _invite(http, SANJEEV["id"], email="Rep@Nunnari.Academy").json()
        assert gotrue.created == ["rep@nunnari.academy"]
        assert fake.profiles[body["user_id"]]["email"] == "rep@nunnari.academy"


class TestTimeZone:
    """FR-CRM-ACC-15."""

    def test_deepweaver_defaults_to_sydney(self, crm):
        http, fake, *_ = crm
        body = _invite(http, OWNER["id"], LABS, email="seller@deepweaver.ai").json()
        assert fake.profiles[body["user_id"]]["timezone"] == "Australia/Sydney"

    def test_everyone_else_defaults_to_kolkata(self, crm):
        http, fake, *_ = crm
        body = _invite(http, SANJEEV["id"]).json()
        assert fake.profiles[body["user_id"]]["timezone"] == "Asia/Kolkata"

    def test_the_inviter_may_choose(self, crm):
        http, fake, *_ = crm
        body = _invite(http, SANJEEV["id"], timezone="Australia/Sydney").json()
        assert fake.profiles[body["user_id"]]["timezone"] == "Australia/Sydney"

    def test_an_existing_persons_preference_is_left_alone(self, crm):
        http, fake, *_ = crm
        fake.profiles[VIEWER["id"]]["timezone"] = "Asia/Kolkata"
        r = _invite(http, SANJEEV["id"], email=VIEWER["email"], timezone="Australia/Sydney")
        assert r.status_code == 201
        assert fake.profiles[VIEWER["id"]]["timezone"] == "Asia/Kolkata"


class TestExistingAccounts:
    """FR-CRM-ACC-04, Q-75."""

    def test_an_active_account_gets_the_membership_only(self, crm):
        http, fake, _, academy, gotrue, _ = crm
        before = dict(fake.profiles[VIEWER["id"]])
        r = _invite(http, SANJEEV["id"], email=VIEWER["email"], role="viewer")
        assert r.status_code == 201
        assert r.json()["created_account"] is False and r.json()["reactivated"] is False
        assert gotrue.created == []
        assert fake.profiles[VIEWER["id"]] == before
        assert _membership(fake, academy, VIEWER["id"])["role"] == "viewer"

    def test_an_existing_member_is_409(self, crm):
        http, *_ = crm
        r = _invite(http, OWNER["id"], LABS, email=MEMBER["email"])
        assert (r.status_code, r.json()["detail"]) == (
            409,
            "This person is already a member of this workspace.",
        )

    def test_a_crm_only_account_that_lost_its_last_membership_is_reactivated(self, crm):
        http, fake, labs, academy, gotrue, _ = crm
        fake.profiles[SELLER["id"]].update(email="tamil@nunnari.academy")
        removed = http.delete(
            f"/api/v1/crm/members/{_membership(fake, labs, SELLER['id'])['id']}{LABS}",
            headers=as_(OWNER["id"]),
        )
        assert removed.json() == {"removed": True, "account_deactivated": True}
        r = _invite(http, SANJEEV["id"], email="tamil@nunnari.academy")
        assert r.status_code == 201
        assert r.json()["reactivated"] is True and gotrue.created == []
        assert fake.profiles[SELLER["id"]]["is_active"] is True
        assert fake.profiles[SELLER["id"]]["deactivated_reason"] is None
        assert _membership(fake, academy, SELLER["id"])

    def test_an_account_a_tva_admin_deactivated_stays_off_for_a_workspace_admin(self, crm):
        http, fake, _, academy, _, _ = crm
        fake.profiles[SELLER["id"]].update(
            email="tamil@nunnari.academy", is_active=False, deactivated_reason="admin"
        )
        r = _invite(http, SANJEEV["id"], email="tamil@nunnari.academy")
        assert (r.status_code, r.json()["detail"]) == (422, rules.INACTIVE)
        assert fake.profiles[SELLER["id"]]["is_active"] is False

        r = _tva_invite(http, academy, email="tamil@nunnari.academy")
        assert r.status_code == 201 and r.json()["reactivated"] is True
        assert fake.profiles[SELLER["id"]]["is_active"] is True

    def test_an_inactive_staff_account_is_refused_even_to_a_tva_admin(self, crm):
        http, fake, _, academy, _, _ = crm
        fake.profiles["u-left"] = {
            **SANJEEV,
            "id": "u-left",
            "email": "left@nunnari.academy",
            "is_active": False,
            "deactivated_reason": "admin",
        }
        for r in (
            _invite(http, SANJEEV["id"], email="left@nunnari.academy"),
            _tva_invite(http, academy, email="left@nunnari.academy"),
        ):
            assert (r.status_code, r.json()["detail"]) == (422, rules.INACTIVE)


class TestWhoMayInviteWhere:
    """FR-CRM-ACC-05, -09; AC "A workspace Admin can invite only into their
    workspace, only from its allowed domains"."""

    def test_an_address_off_the_allow_list_is_403_naming_the_domains(self, crm):
        http, *_ = crm
        r = _invite(http, OWNER["id"], LABS, email="rep@gmail.com")
        assert (r.status_code, r.json()["detail"]) == (
            403,
            "Only addresses at nunnarilabs.com or deepweaver.ai can be invited to Nunnari Labs.",
        )

    def test_a_workspace_admin_cannot_invite_into_a_workspace_they_are_not_in(self, crm):
        """The guard answers as for a workspace that does not exist (FR-CRM-WS-02)."""
        http, fake, *_ = crm
        r = _invite(http, SANJEEV["id"], LABS, email="rep@nunnarilabs.com")
        assert (r.status_code, r.json()["detail"]) == (404, "No such workspace.")
        assert fake.applied == [] and len(fake.rows["crm_memberships"]) == 7

    def test_a_member_who_is_not_admin_there_is_403(self, crm):
        http, *_ = crm
        r = _invite(http, MEMBER["id"], ACADEMY)
        assert (r.status_code, r.json()["detail"]) == (403, "Only a workspace admin can do that.")

    @pytest.mark.parametrize(
        "field",
        [
            {"tva_role": "admin"},
            {"lead_id": "u-owner"},
            {"logs_time": True},
            {"account_type": "staff"},
        ],
    )
    def test_an_invitation_carries_no_tva_field(self, crm, field):
        http, *_ = crm
        assert _invite(http, SANJEEV["id"], **field).status_code == 422

    def test_a_workspace_admin_has_no_bypass(self, crm):
        http, *_ = crm
        r = _invite(http, SANJEEV["id"], email=STAFF_ON_GMAIL["email"], bypass_allow_list=True)
        assert r.status_code == 422

    def test_a_workspace_admin_cannot_reach_the_tva_admin_panel(self, crm):
        http, _, _, academy, _, _ = crm
        r = _tva_invite(http, academy, who=SANJEEV["id"])
        assert r.status_code == 403

    def test_a_tva_admin_invites_into_any_workspace_without_joining(self, crm):
        http, fake, labs, academy, _, _ = crm
        for ws in (labs, academy):
            email = f"rep@{ws['allowed_domains'][0]}"
            assert _tva_invite(http, ws, email=email).status_code == 201
        assert not any(
            m["user_id"] == OUTSIDER["id"] for m in fake.rows["crm_memberships"].values()
        )

    def test_a_tva_admin_may_bypass_for_an_existing_staff_account(self, crm):
        http, fake, labs, _, gotrue, _ = crm
        r = _tva_invite(http, labs, email=STAFF_ON_GMAIL["email"], bypass_allow_list=True)
        assert r.status_code == 201 and gotrue.created == []
        assert _membership(fake, labs, STAFF_ON_GMAIL["id"])

    def test_without_bypass_the_allow_list_holds_for_a_tva_admin_too(self, crm):
        http, _, labs, _, _, _ = crm
        assert _tva_invite(http, labs, email=STAFF_ON_GMAIL["email"]).status_code == 403

    def test_the_bypass_is_for_existing_staff_only(self, crm):
        http, _, labs, _, gotrue, _ = crm
        r = _tva_invite(http, labs, email="stranger@gmail.com", bypass_allow_list=True)
        assert (r.status_code, r.json()["detail"]) == (403, rules.BYPASS_STAFF_ONLY)
        r = _tva_invite(http, labs, email=SELLER["email"], bypass_allow_list=True)
        assert r.status_code == 403
        assert gotrue.created == []


class TestCompensation:
    """Plan CT-5 — the auth user is deleted only when this request made it."""

    def test_a_new_auth_user_is_deleted_when_crm_invite_fails(self, crm, monkeypatch):
        http, fake, _, _, gotrue, _ = crm

        def refuse(actor, plan):
            raise fake_refusal()

        monkeypatch.setattr(members.store, "invite", refuse)
        r = _invite(http, SANJEEV["id"])
        assert r.status_code == 422
        assert gotrue.deleted == ["u-new-1"]

    def test_an_existing_auth_user_is_never_deleted(self, crm, monkeypatch):
        http, _, _, _, gotrue, _ = crm
        monkeypatch.setattr(
            members.store, "invite", lambda actor, plan: (_ for _ in ()).throw(fake_refusal())
        )
        r = _invite(http, SANJEEV["id"], email=VIEWER["email"])
        assert r.status_code == 422
        assert gotrue.created == [] and gotrue.deleted == []


def fake_refusal():
    from app.services.crm import store

    return store.refusal_for("CRM22", "That change was refused.")


class TestRemovingAndRoles:
    """FR-CRM-ACC-09, -12; plan §5.13."""

    def test_removing_the_last_membership_deactivates_and_revokes(self, crm, monkeypatch):
        http, fake, labs, _, _, _ = crm
        forgotten = []
        monkeypatch.setattr(deps, "forget_personal_tokens", lambda: forgotten.append(True))
        r = http.delete(
            f"/api/v1/crm/members/{_membership(fake, labs, SELLER['id'])['id']}{LABS}",
            headers=as_(OWNER["id"]),
        )
        assert r.json() == {"removed": True, "account_deactivated": True}
        assert fake.profiles[SELLER["id"]]["is_active"] is False
        assert fake.profiles[SELLER["id"]]["deactivated_reason"] == "crm_no_membership"
        assert fake.tokens[SELLER["id"]] == 0
        assert forgotten == [True]
        r = http.get("/api/v1/crm/workspaces", headers=as_(SELLER["id"]))
        assert r.status_code == 403

    def test_a_staff_member_keeps_their_tva_account(self, crm):
        http, fake, labs, _, _, _ = crm
        r = http.delete(
            f"/api/v1/crm/members/{_membership(fake, labs, VIEWER['id'])['id']}{LABS}",
            headers=as_(OWNER["id"]),
        )
        assert r.json() == {"removed": True, "account_deactivated": False}
        assert fake.profiles[VIEWER["id"]]["is_active"] is True

    def test_a_role_change_is_audited(self, crm):
        http, fake, labs, _, _, _ = crm
        m = _membership(fake, labs, VIEWER["id"])
        r = http.patch(
            f"/api/v1/crm/members/{m['id']}{LABS}",
            json={"role": "member"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200 and r.json()["role"] == "member"
        assert fake.audit[-1]["action"] == "membership.role_changed"

    def test_the_last_admin_cannot_be_demoted_or_removed(self, crm):
        http, fake, _, academy, _, _ = crm
        # Academy has two admins (the owner, Sanjeev); the owner steps out first.
        owner = _membership(fake, academy, OWNER["id"])
        assert (
            http.delete(f"/api/v1/crm/members/{owner['id']}{ACADEMY}", headers=as_(OWNER["id"]))
        ).status_code == 200
        mine = _membership(fake, academy, SANJEEV["id"])
        r = http.patch(
            f"/api/v1/crm/members/{mine['id']}{ACADEMY}",
            json={"role": "member"},
            headers=as_(SANJEEV["id"]),
        )
        assert (r.status_code, r.json()["detail"]) == (422, "A workspace needs at least one admin.")
        r = http.delete(f"/api/v1/crm/members/{mine['id']}{ACADEMY}", headers=as_(SANJEEV["id"]))
        assert (r.status_code, r.json()["detail"]) == (422, "A workspace needs at least one admin.")

    @pytest.mark.parametrize("person", [OWNER, OUTSIDER])
    def test_a_workspace_admin_cannot_touch_the_owner_or_a_tva_admin(self, crm, person):
        http, fake, _, academy, _, _ = crm
        if person is OUTSIDER:
            fake.add_member(academy, OUTSIDER["id"], "member")
        m = _membership(fake, academy, person["id"])
        url = f"/api/v1/crm/members/{m['id']}{ACADEMY}"
        for r in (
            http.patch(url, json={"role": "viewer"}, headers=as_(SANJEEV["id"])),
            http.delete(url, headers=as_(SANJEEV["id"])),
        ):
            assert (r.status_code, r.json()["detail"]) == (403, rules.PROTECTED)
        assert _membership(fake, academy, person["id"])["role"] == m["role"]

    def test_a_tva_admin_can_remove_a_protected_membership(self, crm):
        http, fake, _, academy, _, _ = crm
        m = _membership(fake, academy, OWNER["id"])
        r = http.delete(f"/api/v1/crm/tva-admin/memberships/{m['id']}", headers=as_(OUTSIDER["id"]))
        assert r.json() == {"removed": True, "account_deactivated": False}

    def test_a_membership_of_another_workspace_is_not_found(self, crm):
        http, fake, labs, _, _, _ = crm
        m = _membership(fake, labs, SELLER["id"])
        r = http.delete(f"/api/v1/crm/members/{m['id']}{ACADEMY}", headers=as_(SANJEEV["id"]))
        assert (r.status_code, r.json()["detail"]) == (404, "No such member in this workspace.")


class TestMemberList:
    """Contracts §2.2 — every member reads it; email for admins only."""

    def test_admins_see_emails(self, crm):
        http, *_ = crm
        rows = http.get(f"/api/v1/crm/members{LABS}", headers=as_(OWNER["id"])).json()
        assert {r["display_name"] for r in rows} == {"Owner", "Deepika", "Devansh", "Tamil"}
        assert all("email" in r for r in rows)
        tamil = next(r for r in rows if r["display_name"] == "Tamil")
        assert tamil["account_type"] == "crm_only"

    def test_others_do_not(self, crm):
        http, *_ = crm
        rows = http.get(f"/api/v1/crm/members{LABS}", headers=as_(VIEWER["id"])).json()
        assert rows and not any("email" in r for r in rows)


class TestRules:
    """`domain/crm/invites.py` on its own."""

    def test_the_allow_list_sentence_names_one_domain_plainly(self):
        why = rules.allow_list_refusal("a@b.com", {"name": "X", "allowed_domains": ["x.com"]})
        assert why.detail == "Only addresses at x.com can be invited to X."

    def test_an_empty_allow_list_sends_the_admin_to_a_tva_admin(self):
        why = rules.allow_list_refusal("a@b.com", {"name": "X", "allowed_domains": []})
        assert why.status == 403 and "TVA admin" in why.detail
