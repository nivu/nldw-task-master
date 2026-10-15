"""What a list may ask — spec 007 FR-CRM-VIEW-02, -03, -08, FR-CRM-ACC-11;
Q-67; plan §5.5, §5.8; spec §11 AC "a Viewer filter or sort on an amount is
refused".

The rules are pure (`domain/crm/views.py`) and are tested as such first;
then the same rules through `POST /crm/query`, against `tests/crm_fake.py`,
whose `crm_query` keeps 034's checks: every operator per type returns the
rows it should, AND and OR nest, a field the list cannot filter on is 422, a
Viewer naming an amount anywhere is 422, a shared view with amount
conditions opens cleaned with the notice, and `q` matches literally.
"""

from __future__ import annotations

from datetime import date

import pytest
from crm_fake import MEMBER, OWNER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.domain.crm import views as rules
from app.services.crm import deals
from app.services.crm import views as service

ACADEMY = "?workspace=academy"
LABS = "?workspace=labs"
TODAY = date(2026, 10, 14)
NEXT = {"next_step": "Call", "next_step_on": "2026-10-20"}


def _cond(field, operator, value=None):
    node = {"field": field, "operator": operator}
    if value is not None:
        node["value"] = value
    return node


def _all(*items):
    return {"op": "and", "items": list(items)}


def _any(*items):
    return {"op": "or", "items": list(items)}


def _check(kind, spec, role="admin", linked=False):
    return rules.validate_spec(kind, spec, role=role, tva_linked=linked)


class TestOperatorsPerType:
    """FR-CRM-VIEW-02 — each type takes the operators that suit it."""

    @pytest.mark.parametrize(
        ("kind", "field", "operator", "value"),
        [
            ("company", "name", "is", "Acme"),
            ("company", "name", "is_not", "Acme"),
            ("company", "name", "contains", "ac"),
            ("company", "name", "not_contains", "ac"),
            ("company", "name", "is_empty", None),
            ("company", "city", "is_not_empty", None),
            ("company", "description", "contains", "ai"),
            ("opportunity", "probability", "gt", 10),
            ("opportunity", "probability", "gte", "10"),
            ("opportunity", "probability", "lt", 90),
            ("opportunity", "probability", "lte", 90),
            ("opportunity", "probability", "is", 50),
            ("opportunity", "expected_close_on", "before", "2026-12-01"),
            ("opportunity", "expected_close_on", "after", "2026-10-01"),
            ("opportunity", "expected_close_on", "on", "2026-10-31"),
            ("opportunity", "expected_close_on", "within_last_days", 30),
            ("opportunity", "expected_close_on", "within_next_days", 7),
            ("opportunity", "expected_close_on", "this_month", None),
            ("opportunity", "won_on", "this_quarter", None),
            ("company", "last_contact_at", "within_last_days", 14),
            ("person", "do_not_contact", "is", True),
            ("company", "account_type_id", "any_of", ["a"]),
            ("company", "account_type_id", "none_of", ["a"]),
            ("opportunity", "stage", "any_of", ["discovery"]),
            ("company", "lead_stage", "is", "new"),
            ("company", "owner_id", "is_me", None),
            ("company", "owner_id", "is", "u-owner"),
            ("person", "company_id", "is_empty", None),
            ("company", "channel_ids", "any_of", ["c"]),
            ("company", "channel_ids", "is_not_empty", None),
        ],
    )
    def test_an_operator_that_fits_is_accepted(self, kind, field, operator, value):
        assert _check(kind, {"filters": _all(_cond(field, operator, value))}) is None

    @pytest.mark.parametrize(
        ("kind", "field", "operator", "value"),
        [
            ("company", "description", "is", "x"),
            ("company", "name", "gt", "x"),
            ("opportunity", "probability", "contains", "1"),
            ("opportunity", "expected_close_on", "is", "2026-10-01"),
            ("person", "do_not_contact", "contains", "t"),
            ("company", "channel_ids", "is", "c"),
            ("company", "name", "is_me", None),
            ("company", "account_type_id", "before", "2026-10-01"),
        ],
    )
    def test_an_operator_that_does_not_fit_is_refused(self, kind, field, operator, value):
        assert "does not fit" in _check(kind, {"filters": _all(_cond(field, operator, value))})

    @pytest.mark.parametrize(
        ("field", "operator", "value", "why"),
        [
            ("expected_close_on", "on", "14 Oct", "YYYY-MM-DD"),
            ("expected_close_on", "on", "2026-02-30", "YYYY-MM-DD"),
            ("probability", "gt", "ten", "not a number"),
            ("probability", "gt", True, "not a number"),
            ("expected_close_on", "within_next_days", 4000, "0 to 3650"),
            ("expected_close_on", "within_next_days", "7", "0 to 3650"),
            ("stage", "any_of", "discovery", "list of options"),
            ("stage", "any_of", [], "list of options"),
            ("expected_close_on", "this_month", "2026-10", "takes no value"),
        ],
    )
    def test_a_value_that_does_not_fit_is_refused(self, field, operator, value, why):
        found = _check("opportunity", {"filters": _all(_cond(field, operator, value))})
        assert why in found


class TestNesting:
    def test_and_or_nest_three_deep(self):
        spec = {"filters": _all(_any(_all(_cond("name", "contains", "a"))))}
        assert _check("company", spec) is None

    def test_a_fourth_level_is_refused(self):
        spec = {"filters": _all(_any(_all(_any(_cond("name", "contains", "a")))))}
        assert _check("company", spec) == "Filters nest at most 3 levels deep."

    def test_thirty_conditions_and_no_more(self):
        many = [_cond("name", "contains", str(n)) for n in range(30)]
        assert _check("company", {"filters": _all(*many)}) is None
        many.append(_cond("city", "is_empty"))
        assert _check("company", {"filters": _all(*many)}) == "Use at most 30 conditions."

    def test_a_group_joins_with_and_or_or_only(self):
        spec = {"filters": {"op": "xor", "items": []}}
        assert "and or or" in _check("company", spec)


class TestFieldsTheListKnows:
    """Unknown or non-filterable fields are 422 (FR-CRM-VIEW-02)."""

    @pytest.mark.parametrize(
        "spec",
        [
            {"filters": _all(_cond("nope", "is", "x"))},
            {"filters": _all(_cond("position", "gt", 1))},
            {"filters": _all(_cond("name; DROP TABLE crm_companies", "is", "x"))},
            {"sorts": [{"field": "channel_ids", "dir": "asc"}]},
            {"sorts": [{"field": "name", "dir": "sideways"}]},
            {"group_by": "name"},
            {"aggregates": [{"field": "name", "fn": "sum"}]},
            {"aggregates": [{"fn": "median"}]},
            {"columns": [{"field": "nope"}]},
        ],
    )
    def test_refused(self, spec):
        assert _check("company", spec) is not None

    def test_a_field_of_the_other_workspace_kind(self):
        """Entity is Labs's, Institution type Academy's (FR-CRM-WS-06)."""
        entity = {"filters": _all(_cond("entities", "any_of", ["deepweaver"]))}
        assert _check("company", entity, linked=True) is None
        assert _check("company", entity, linked=False) is not None
        institution = {"group_by": "institution_type_id"}
        assert _check("company", institution, linked=False) is None
        assert _check("company", institution, linked=True) is not None

    def test_a_kanban_groups_by_a_select_field(self):
        assert _check("company", {"kanban_field": "lead_stage"}) is None
        assert _check("company", {"kanban_field": "owner_id"}) == (
            "A kanban groups by a select field."
        )


class TestViewersAndAmounts:
    """FR-CRM-ACC-11 — a Viewer names no amount field, anywhere."""

    @pytest.mark.parametrize(
        "spec",
        [
            {"filters": _all(_cond("amount", "gt", 100))},
            {"filters": _all(_any(_all(_cond("amount_in_inr", "gt", 100))))},
            {"filters": _all(_cond("weighted_amount", "is_empty"))},
            {"sorts": [{"field": "amount_in_inr", "dir": "desc"}]},
            {"sorts": [{"field": "name", "dir": "asc"}, {"field": "amount", "dir": "desc"}]},
            {"group_by": "amount"},
            {"aggregates": [{"field": "amount_in_inr", "fn": "sum"}]},
            {"aggregates": [{"field": "weighted_amount", "fn": "avg"}]},
            {"aggregates": [{"field": "amount", "fn": "count"}]},
            {"columns": [{"field": "amount", "width": 120}]},
            {"card_fields": ["fx_rate_used"]},
        ],
    )
    def test_refused_for_a_viewer_alone(self, spec):
        assert _check("opportunity", spec, role="viewer") == rules.AMOUNT_REFUSAL
        assert _check("opportunity", spec, role="member") in (None, "Amount cannot group a list.")

    def test_a_currency_code_is_not_an_amount(self):
        spec = {"filters": _all(_cond("currency", "is", "USD")), "group_by": "currency"}
        assert _check("opportunity", spec, role="viewer") is None


class TestCleaningASharedView:
    """Q-67 — a view opens for a Viewer without its amounts, saying so."""

    def test_everything_about_amounts_is_dropped(self):
        config = {
            "filters": _all(
                _cond("stage", "any_of", ["proposal"]),
                _any(_cond("amount", "gt", 100), _cond("name", "contains", "a")),
            ),
            "sorts": [{"field": "amount", "dir": "desc"}, {"field": "name", "dir": "asc"}],
            "group_by": "amount_in_inr",
            "aggregates": [{"fn": "count"}, {"field": "amount_in_inr", "fn": "sum"}],
            "columns": [{"field": "name", "width": 200}, {"field": "amount", "width": 120}],
            "card_fields": ["company_id", "amount"],
        }
        cleaned, dropped = rules.clean_shared_view("opportunity", config, tva_linked=True)
        assert dropped is True
        assert cleaned == {
            "filters": _all(
                _cond("stage", "any_of", ["proposal"]),
                _any(_cond("name", "contains", "a")),
            ),
            "sorts": [{"field": "name", "dir": "asc"}],
            "aggregates": [{"fn": "count"}],
            "columns": [{"field": "name", "width": 200}],
            "card_fields": ["company_id"],
        }
        assert rules.validate_spec("opportunity", cleaned, role="viewer", tva_linked=True) is None

    def test_a_view_without_amounts_is_untouched(self):
        config = {"filters": _all(_cond("stage", "any_of", ["proposal"]))}
        assert rules.clean_shared_view("opportunity", config, tva_linked=True) == (config, False)


class TestResolving:
    def test_is_me_and_the_calendar(self):
        spec = {
            "filters": _all(
                _cond("owner_id", "is_me"),
                _cond("expected_close_on", "this_month"),
                _cond("won_on", "this_quarter"),
            )
        }
        out = rules.resolve(spec, user_id="u-1", today=date(2026, 11, 14))
        assert out["filters"]["items"] == [
            {"field": "owner_id", "operator": "is", "value": "u-1"},
            _all(
                _cond("expected_close_on", "after", "2026-10-31"),
                _cond("expected_close_on", "before", "2026-12-01"),
            ),
            _all(_cond("won_on", "after", "2026-09-30"), _cond("won_on", "before", "2027-01-01")),
        ]
        assert out["today"] == "2026-11-14"

    def test_q_and_contains_are_escaped(self):
        out = rules.resolve(
            {"q": "50%_off", "filters": _all(_cond("name", "contains", "a\\b"))},
            user_id="u",
            today=TODAY,
        )
        assert out["q"] == "50\\%\\_off"
        assert out["filters"]["items"][0]["value"] == "a\\\\b"

    def test_a_view_is_narrowed_by_the_request(self):
        config = {
            "filters": _all(_cond("stage", "any_of", ["proposal"])),
            "kanban_field": "stage",
            "sorts": [{"field": "position", "dir": "asc"}],
            "card_fields": ["amount"],
        }
        spec = {"filters": _all(_cond("name", "contains", "x")), "sorts": []}
        merged = rules.merge(config, spec, {"filters", "sorts"})
        assert merged == {
            "filters": _all(config["filters"], spec["filters"]),
            "group_by": "stage",
            "sorts": [],
        }


# ---------------------------------------------------------------------------
# Through POST /crm/query
# ---------------------------------------------------------------------------


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    fake.add_deal_lists(labs)
    fake.add_deal_lists(academy)
    fake.add_member(academy, VIEWER["id"], "viewer")
    fake.add(
        "crm_fx_rates", workspace_id=academy["id"], currency="USD", month="2026-10-01", rate=84
    )
    monkeypatch.setattr(deals, "today_in_company_tz", lambda: TODAY)
    monkeypatch.setattr(service, "today_for", lambda user: TODAY)
    return crm_client(monkeypatch, fake=fake), fake


def _post(http, body, who=OWNER["id"], query=ACADEMY):
    return http.post(f"/api/v1/crm/query{query}", json=body, headers=as_(who))


def _rows(http, obj, spec=None, who=OWNER["id"], **extra):
    r = _post(http, {"object": obj, "spec": spec or {}, **extra}, who)
    assert r.status_code == 200, r.text
    return r.json()


def _names(answer):
    return [row["name"] for row in answer["rows"]]


def _companies(http, names, query=ACADEMY):
    made = {}
    for name in names:
        r = http.post(
            f"/api/v1/crm/companies{query}", json={"name": name}, headers=as_(OWNER["id"])
        )
        assert r.status_code == 201, r.text
        made[name] = r.json()
    return made


def _qualified_company(http, name="College"):
    company = _companies(http, [name])[name]
    http.patch(
        f"/api/v1/crm/companies/{company['id']}{ACADEMY}",
        json={"lead_stage": "qualified"},
        headers=as_(OWNER["id"]),
    )
    return company


def _deal(http, company, name, **fields):
    r = http.post(
        f"/api/v1/crm/opportunities{ACADEMY}",
        json={"name": name, "company_id": company["id"], **NEXT, **fields},
        headers=as_(OWNER["id"]),
    )
    assert r.status_code == 201, r.text
    return r.json()


class TestQueryRows:
    def test_filters_sorts_and_paging(self, crm):
        http, _ = crm
        _companies(http, ["Beta", "alpha", "Gamma", "Delta"])
        first = _rows(
            http,
            "companies",
            {"sorts": [{"field": "name", "dir": "asc"}], "limit": 2},
        )
        assert _names(first) == ["alpha", "Beta"]
        assert first["total"] == 4
        second = _rows(
            http,
            "companies",
            {"sorts": [{"field": "name", "dir": "asc"}], "limit": 2, "after": first["next"]},
        )
        assert _names(second) == ["Delta", "Gamma"]
        assert second["next"] is None

    def test_and_or_through_the_route(self, crm):
        http, _ = crm
        _companies(http, ["Acme", "Apex", "Brick"])
        spec = {
            "filters": _any(_all(_cond("name", "contains", "a"), _cond("name", "contains", "x"))),
            "sorts": [{"field": "name", "dir": "asc"}],
        }
        spec["filters"]["items"].append(_cond("name", "is", "brick"))
        assert _names(_rows(http, "company", spec)) == ["Apex", "Brick"]

    def test_is_me_is_the_caller(self, crm):
        http, _ = crm
        _companies(http, ["Mine"])
        spec = {"filters": _all(_cond("owner_id", "is_me"))}
        assert _names(_rows(http, "companies", spec)) == ["Mine"]
        assert _names(_rows(http, "companies", spec, who=MEMBER["id"])) == []

    def test_dates_and_numbers(self, crm):
        http, _ = crm
        company = _qualified_company(http)
        _deal(http, company, "Soon", expected_close_on="2026-10-20", probability=40)
        _deal(http, company, "Later", expected_close_on="2027-01-20", probability=10)
        cases = [
            (_cond("expected_close_on", "this_month"), ["Soon"]),
            (_cond("expected_close_on", "within_next_days", 10), ["Soon"]),
            (_cond("expected_close_on", "after", "2026-12-31"), ["Later"]),
            (_cond("expected_close_on", "on", "2026-10-20"), ["Soon"]),
            (_cond("probability", "gte", 40), ["Soon"]),
            (_cond("probability", "lt", 40), ["Later"]),
        ]
        for condition, expected in cases:
            assert _names(_rows(http, "opportunities", {"filters": _all(condition)})) == expected

    def test_a_q_of_50_percent_off_matches_literally(self, crm):
        """`%` and `_` are characters, not wildcards (plan §5.5)."""
        http, _ = crm
        _companies(http, ["50%_off deals", "500 offices", "50% off"])
        assert _names(_rows(http, "companies", {"q": "50%_off"})) == ["50%_off deals"]
        found = _rows(http, "companies", {"filters": _all(_cond("name", "contains", "0%_"))})
        assert _names(found) == ["50%_off deals"]

    def test_groups_and_totals(self, crm):
        http, fake = crm
        company = _qualified_company(http)
        _deal(http, company, "One", amount="1000", currency="USD", probability=50)
        _deal(http, company, "Two", amount="100", currency="INR", probability=10)
        answer = _rows(
            http,
            "opportunities",
            {
                "group_by": "currency",
                "aggregates": [
                    {"fn": "count"},
                    {"field": "amount_in_inr", "fn": "sum"},
                    {"field": "weighted_amount", "fn": "sum"},
                ],
            },
        )
        assert answer["aggregates"] == {
            "count": 2,
            "sum_amount_in_inr": "84100.00",
            "sum_weighted_amount": "42010.00",
        }
        assert [(g["key"], g["count"]) for g in answer["groups"]] == [("INR", 1), ("USD", 1)]
        assert answer["groups"][1]["aggregates"]["sum_amount_in_inr"] == "84000.00"

    def test_a_deleted_record_never_appears(self, crm):
        http, _ = crm
        made = _companies(http, ["Kept", "Gone"])
        http.request(
            "DELETE",
            f"/api/v1/crm/companies/{made['Gone']['id']}{ACADEMY}",
            headers=as_(OWNER["id"]),
        )
        assert _names(_rows(http, "companies")) == ["Kept"]

    def test_unknown_or_unfilterable_fields_are_422(self, crm):
        http, _ = crm
        for spec in (
            {"filters": _all(_cond("nope", "is", "x"))},
            {"filters": _all(_cond("position", "gt", 1))},
            {"sorts": [{"field": "nope"}]},
        ):
            r = _post(http, {"object": "companies", "spec": spec})
            assert r.status_code == 422, spec

    def test_a_query_is_a_read_and_defaults_to_the_last_used_workspace(self, crm):
        """D13: Deepika used Academy last; a query naming none reads there."""
        http, _ = crm
        _companies(http, ["Academy co"])
        r = _post(http, {"object": "companies"}, who=MEMBER["id"], query="")
        assert r.status_code == 200, r.text
        assert _names(r.json()) == ["Academy co"]


class TestViewerQueries:
    @pytest.mark.parametrize(
        "spec",
        [
            {"filters": _all(_cond("amount", "gt", 1))},
            {"sorts": [{"field": "amount_in_inr", "dir": "desc"}]},
            {"group_by": "amount"},
            {"aggregates": [{"field": "amount_in_inr", "fn": "sum"}]},
        ],
    )
    def test_a_viewer_amount_filter_sort_group_or_total_is_422(self, crm, spec):
        http, fake = crm
        r = _post(http, {"object": "opportunities", "spec": spec}, who=VIEWER["id"])
        assert r.status_code == 422
        assert r.json()["detail"] == "Viewers cannot filter, sort or total by amount."
        assert fake.queries == []

    def test_a_shared_view_with_an_amount_condition_is_cleaned(self, crm):
        http, fake = crm
        company = _qualified_company(http)
        _deal(http, company, "Big", amount="100", currency="USD")
        made = http.post(
            f"/api/v1/crm/views{ACADEMY}",
            json={
                "object": "opportunities",
                "name": "Big deals",
                "visibility": "shared",
                "config": {
                    "filters": _all(_cond("amount_in_inr", "gt", 1000)),
                    "sorts": [{"field": "amount", "dir": "desc"}],
                    "columns": [{"field": "name"}, {"field": "amount"}],
                    "aggregates": [{"fn": "count"}, {"field": "amount", "fn": "sum"}],
                },
            },
            headers=as_(OWNER["id"]),
        )
        assert made.status_code == 201, made.text
        admin = _rows(http, "opportunities", view="Big deals")
        assert _names(admin) == ["Big"] and "notice" not in admin
        seen = _rows(http, "opportunities", who=VIEWER["id"], view="Big deals")
        assert seen["notice"] == "A condition on an amount field was removed"
        assert _names(seen) == ["Big"]
        assert seen["aggregates"] == {"count": 1}
        assert "amount" not in seen["rows"][0]
        assert fake.queries[-1]["redact"] is True

    def test_the_seeded_pipeline_opens_for_a_viewer_without_its_sums(self, crm):
        http, _ = crm
        company = _qualified_company(http)
        _deal(http, company, "Open deal", amount="100", currency="USD")
        admin = _rows(http, "opportunities", view="pipeline")
        assert set(admin["groups"][0]["aggregates"]) == {
            "count",
            "sum_amount_in_inr",
            "sum_weighted_amount",
        }
        seen = _rows(http, "opportunities", who=VIEWER["id"], view="Pipeline")
        assert seen["notice"] == "A condition on an amount field was removed"
        assert seen["groups"] == [
            {"key": "discovery", "label": "Discovery", "count": 1, "aggregates": {"count": 1}}
        ]
        assert seen["view"]["config"]["card_fields"] == ["company_id", "next_step_on", "owner_id"]

    def test_a_viewer_narrowing_a_view_by_amount_is_still_refused(self, crm):
        http, _ = crm
        r = _post(
            http,
            {
                "object": "opportunities",
                "view": "pipeline",
                "spec": {"filters": _all(_cond("amount", "gt", 1))},
            },
            who=VIEWER["id"],
        )
        assert r.status_code == 422
