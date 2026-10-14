"""Workspace settings, select lists and FX rates — spec 007 FR-CRM-LIST-01 to
-06, -09, FR-CRM-FX-02, FR-CRM-PIPE-11, FR-CRM-ACC-06; Q-30, Q-59, Q-71.

Routes run against `tests/crm_fake.py`; the pure rules of
`domain/crm/settings.py` are tested on their own below. The values the
migration seeds into each workspace (FR-CRM-LIST-03 to -05) are database
state, so they are asserted against the real seeded database in
`tests/db/test_crm_seed_db.py`.
"""

from __future__ import annotations

import pytest
from crm_fake import MEMBER, OWNER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.domain.crm import settings as rules

LABS = "?workspace=labs"
ACADEMY = "?workspace=academy"
SIX = {
    "discovery": 10,
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


def _option_url(fake, ws, list_key, label, query=LABS) -> str:
    return f"/api/v1/crm/lists/{list_key}/options/{fake.option(ws, label)['id']}{query}"


class TestFixedLists:
    """FR-CRM-LIST-02, Q-30 — stages are renamed and recoloured, nothing else."""

    def test_rename_and_recolour(self, crm):
        http, fake, labs, _ = crm
        r = http.patch(
            _option_url(fake, labs, "opportunity.stage", "Proposal"),
            json={"label": "Proposal sent", "color": "blue"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200
        assert (r.json()["label"], r.json()["color"]) == ("Proposal sent", "blue")
        assert r.json()["system_key"] == "proposal"

    @pytest.mark.parametrize(
        "change", [{"position": 5}, {"is_active": False}, {"is_default": True}]
    )
    def test_no_reorder_deactivate_or_default(self, crm, change):
        http, fake, labs, _ = crm
        r = http.patch(
            _option_url(fake, labs, "opportunity.stage", "Proposal"),
            json=change,
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422
        assert r.json()["detail"] == (
            "Stages, lead stages, task status and bucket can be renamed and recoloured only."
        )

    def test_no_new_stage(self, crm):
        http, *_ = crm
        r = http.post(
            f"/api/v1/crm/lists/opportunity.stage/options{LABS}",
            json={"label": "Pilot"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422
        assert r.json()["detail"] == (
            "Stage is a fixed list; its options can be renamed and recoloured only."
        )


class TestSystemKeys:
    """FR-CRM-LIST-09, Q-71."""

    def test_a_keyed_option_cannot_be_deactivated(self, crm):
        http, fake, labs, _ = crm
        r = http.patch(
            _option_url(fake, labs, "company.account_type", "Client"),
            json={"is_active": False},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 422
        assert r.json()["detail"] == (
            "“Client” is used by a rule; it can be renamed and recoloured but not deactivated."
        )

    def test_a_keyed_option_can_be_renamed(self, crm):
        http, fake, labs, _ = crm
        r = http.patch(
            _option_url(fake, labs, "company.account_type", "Client"),
            json={"label": "Customer account"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200 and r.json()["system_key"] == "client"

    def test_a_plain_option_can_be_deactivated_and_stays_listed(self, crm):
        http, fake, labs, _ = crm
        r = http.patch(
            _option_url(fake, labs, "company.account_type", "Prospect"),
            json={"is_active": False},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 200
        lists = http.get(f"/api/v1/crm/lists{LABS}", headers=as_(OWNER["id"])).json()
        prospect = [o for o in lists["company.account_type"] if o["label"] == "Prospect"]
        assert prospect[0]["is_active"] is False


class TestEditableLists:
    """FR-CRM-LIST-01."""

    def test_add_goes_to_the_end(self, crm):
        http, *_ = crm
        r = http.post(
            f"/api/v1/crm/lists/channel/options{LABS}",
            json={"label": "  Partner   event ", "color": "teal"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 201
        assert (r.json()["label"], r.json()["position"], r.json()["is_fixed"]) == (
            "Partner event",
            3,
            False,
        )

    def test_a_duplicate_name_is_refused(self, crm):
        http, *_ = crm
        r = http.post(
            f"/api/v1/crm/lists/channel/options{LABS}",
            json={"label": "referral"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 409
        assert r.json()["detail"] == "This list already has an option with that name."

    def test_one_default_per_list(self, crm):
        http, fake, labs, _ = crm
        for label in ("Referral", "Inbound"):
            r = http.patch(
                _option_url(fake, labs, "channel", label),
                json={"is_default": True},
                headers=as_(OWNER["id"]),
            )
            assert r.status_code == 200
        lists = http.get(f"/api/v1/crm/lists{LABS}", headers=as_(OWNER["id"])).json()
        assert [o["label"] for o in lists["channel"] if o["is_default"]] == ["Inbound"]
        # The old default gives way in the same call, before the new one is set.
        _, ops = fake.applied[-1]
        assert [op["values"] for op in ops] == [{"is_default": False}, {"is_default": True}]

    def test_reorder(self, crm):
        http, fake, labs, _ = crm
        r = http.patch(
            _option_url(fake, labs, "channel", "Inbound"),
            json={"position": 1},
            headers=as_(OWNER["id"]),
        )
        assert r.json()["position"] == 1

    def test_another_workspaces_option_is_not_found(self, crm):
        http, fake, _, academy = crm
        r = http.patch(
            _option_url(fake, academy, "channel", "Referral", query=LABS),
            json={"label": "X"},
            headers=as_(OWNER["id"]),
        )
        assert (r.status_code, r.json()["detail"]) == (404, "No such option.")

    def test_an_academy_list_is_not_in_labs(self, crm):
        http, *_ = crm
        r = http.post(
            f"/api/v1/crm/lists/company.institution_type/options{LABS}",
            json={"label": "School"},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "Institution type is not used in Nunnari Labs."

    def test_every_member_reads_the_lists_but_only_admins_change_them(self, crm):
        http, *_ = crm
        assert http.get(f"/api/v1/crm/lists{LABS}", headers=as_(VIEWER["id"])).status_code == 200
        for who in (VIEWER, MEMBER):
            r = http.post(
                f"/api/v1/crm/lists/channel/options{LABS}",
                json={"label": "New"},
                headers=as_(who["id"]),
            )
            assert r.status_code == 403

    def test_each_workspace_gets_its_own_lists(self, crm):
        http, *_ = crm
        labs = http.get(f"/api/v1/crm/lists{LABS}", headers=as_(OWNER["id"])).json()
        academy = http.get(f"/api/v1/crm/lists{ACADEMY}", headers=as_(OWNER["id"])).json()
        only_academy = {"company.institution_type", "opportunity.programme"}
        assert only_academy & set(academy) == only_academy
        assert not only_academy & set(labs)
        # An empty list is still listed, so Settings can add to it.
        assert labs["company.industry"] == []

    def test_changes_are_audited_with_the_workspace(self, crm):
        http, fake, labs, _ = crm
        http.post(
            f"/api/v1/crm/lists/channel/options{LABS}",
            json={"label": "Webinar"},
            headers=as_(OWNER["id"]),
        )
        actor, ops = fake.applied[-1]
        assert actor == {
            "user_id": OWNER["id"],
            "kind": "user",
            "channel": "web",
            "workspace_id": labs["id"],
        }
        assert ops[0]["table"] == "crm_select_options"


class TestStageProbabilities:
    """FR-CRM-PIPE-11, FR-CRM-LIST-06."""

    def _patch(self, http, value, who=OWNER):
        return http.patch(
            f"/api/v1/crm/settings/stage_probabilities{LABS}",
            json={"value": value},
            headers=as_(who["id"]),
        )

    def test_all_six_from_0_to_100(self, crm):
        http, *_ = crm
        r = self._patch(http, {**SIX, "proposal": 40})
        assert r.status_code == 200 and r.json()["value"]["proposal"] == 40
        settings = http.get(f"/api/v1/crm/settings{LABS}", headers=as_(OWNER["id"])).json()
        assert settings["settings"]["stage_probabilities"]["proposal"] == 40

    def test_out_of_range_names_the_stages(self, crm):
        http, *_ = crm
        r = self._patch(http, {**SIX, "proposal": 101, "negotiation": -1})
        assert r.status_code == 422
        assert r.json()["detail"] == (
            "The probability for Proposal and Negotiation must be a whole number from 0 to 100."
        )

    def test_missing_stages_are_named(self, crm):
        http, *_ = crm
        r = self._patch(http, {"discovery": 10})
        assert r.json()["detail"] == (
            "Stage probabilities need Qualification, Proposal, Negotiation, Closed Won "
            "and Closed Lost."
        )

    def test_members_cannot(self, crm):
        http, *_ = crm
        assert self._patch(http, SIX, who=MEMBER).status_code == 403

    def test_an_unknown_setting(self, crm):
        http, *_ = crm
        r = http.patch(
            f"/api/v1/crm/settings/colour_scheme{LABS}",
            json={"value": {}},
            headers=as_(OWNER["id"]),
        )
        assert (r.status_code, r.json()["detail"]) == (404, "No such setting.")


class TestAllowList:
    """FR-CRM-ACC-06 (amended): a workspace admin sees it but cannot widen it."""

    def test_shown_in_settings(self, crm):
        http, *_ = crm
        body = http.get(f"/api/v1/crm/settings{LABS}", headers=as_(OWNER["id"])).json()
        assert body["allowed_domains"] == ["nunnarilabs.com"]

    def test_read_only_for_a_workspace_admin(self, crm):
        http, fake, *_ = crm
        r = http.patch(
            f"/api/v1/crm/settings/allowed_domains{LABS}",
            json={"value": {"domains": ["gmail.com"]}},
            headers=as_(OWNER["id"]),
        )
        assert r.status_code == 403
        assert r.json()["detail"] == "Only a TVA admin can change the domain allow-list."
        assert fake.applied == []

    def test_settings_are_admin_only(self, crm):
        http, *_ = crm
        assert http.get(f"/api/v1/crm/settings{LABS}", headers=as_(MEMBER["id"])).status_code == 403


class TestFxRates:
    """FR-CRM-FX-02, Q-59."""

    def _put(self, http, path, rate, who=OWNER):
        return http.put(
            f"/api/v1/crm/fx-rates/{path}{LABS}", json={"rate": rate}, headers=as_(who["id"])
        )

    def test_set_and_read(self, crm):
        http, *_ = crm
        r = self._put(http, "USD/2026-10", "83.25")
        assert r.status_code == 200
        assert r.json() == {"currency": "USD", "month": "2026-10", "rate": "83.25"}
        listed = http.get(f"/api/v1/crm/fx-rates{LABS}", headers=as_(OWNER["id"])).json()
        assert listed == [{"currency": "USD", "month": "2026-10", "rate": "83.25"}]

    def test_setting_the_same_month_again_updates_it(self, crm):
        http, fake, *_ = crm
        self._put(http, "AUD/2026-10", "54.1")
        self._put(http, "AUD/2026-10", "55")
        listed = http.get(f"/api/v1/crm/fx-rates{LABS}", headers=as_(OWNER["id"])).json()
        assert listed == [{"currency": "AUD", "month": "2026-10", "rate": "55"}]
        assert [ops[0]["op"] for _, ops in fake.applied] == ["insert", "update"]
        assert {ops[0]["action"] for _, ops in fake.applied} == {"fx_rate.set"}

    @pytest.mark.parametrize("rate", ["0", "-1", "abc", "1234567.1234567"])
    def test_the_rate_is_positive(self, crm, rate):
        http, *_ = crm
        assert self._put(http, "USD/2026-10", rate).status_code == 422

    @pytest.mark.parametrize("month", ["2026-13", "2026-1", "2026-10-01", "Oct-2026"])
    def test_the_month_is_yyyy_mm(self, crm, month):
        http, *_ = crm
        assert self._put(http, f"USD/{month}", "83").status_code == 422

    def test_inr_needs_no_rate(self, crm):
        http, *_ = crm
        r = self._put(http, "INR/2026-10", "1")
        assert (r.status_code, r.json()["detail"]) == (422, "INR converts at 1; it needs no rate.")

    def test_another_currency_is_refused(self, crm):
        http, *_ = crm
        assert self._put(http, "EUR/2026-10", "90").status_code == 422

    def test_admins_only(self, crm):
        http, *_ = crm
        assert self._put(http, "USD/2026-10", "83", who=MEMBER).status_code == 403
        assert http.get(f"/api/v1/crm/fx-rates{LABS}", headers=as_(MEMBER["id"])).status_code == 403


class TestRules:
    """domain/crm/settings.py on its own."""

    def test_six_valid_stages(self):
        assert rules.stage_probabilities_refusal(dict(SIX)) is None

    def test_a_bool_is_not_a_number(self):
        assert rules.stage_probabilities_refusal({**SIX, "proposal": True}) is not None

    def test_a_fraction_is_not_whole(self):
        assert rules.stage_probabilities_refusal({**SIX, "proposal": 50.5}) is not None

    def test_an_unknown_stage(self):
        assert rules.stage_probabilities_refusal({**SIX, "pilot": 5}) == "Unknown stage: pilot."

    def test_not_an_object(self):
        assert rules.setting_refusal("stage_probabilities", [1, 2]) is not None

    def test_academy_lists_are_academy_only(self):
        labs = rules.lists_for(tva_linked=True)
        academy = rules.lists_for(tva_linked=False)
        assert set(academy) - set(labs) == {"company.institution_type", "opportunity.programme"}
        assert set(labs) <= set(academy)

    def test_month_start(self):
        assert rules.month_start("2026-10").isoformat() == "2026-10-01"
