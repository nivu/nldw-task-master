"""The pipeline dashboard and bookings targets — spec 007 FR-CRM-DASH-01 to
-04, FR-CRM-CAP-02, FR-CRM-LIST-06 (targets), FR-CRM-CHG-13; Q-26, Q-39,
Q-40; spec §11 AC "time in stage is correct on the dashboard"; contracts
§2.10 `GET /crm/dashboard`, `GET, PUT /crm/targets`.

The panels' arithmetic is checked against a hand-built fixture through
`domain/crm/dashboard.py`; who sees what, through the route against
`tests/crm_fake.py`.
"""

from __future__ import annotations

from datetime import date

import pytest
from crm_fake import DEFAULTS, MEMBER, OWNER, SELLER, VIEWER, FakeCrmStore, as_, two_workspaces
from crm_fake import client as crm_client

from app.domain.crm import dashboard as rules
from app.services.crm import dashboard as service

API = "/api/v1/crm"
LABS = "?workspace=labs"
ACADEMY = "?workspace=academy"
TODAY = date(2026, 10, 15)


def _deal(**values):
    return {
        "id": values.pop("id"),
        "name": "Deal",
        "stage": "discovery",
        "amount_in_inr": None,
        "probability": 10,
        "entity": None,
        "channel_id": None,
        "source_campaign_id": None,
        "owner_id": "u1",
        "company_id": "c1",
        "programme_id": None,
        "lost_reason_id": None,
        "won_on": None,
        "lost_on": None,
        "created_at": "2026-10-01T00:00:00+05:30",
        **values,
    }


def _facts(**values) -> rules.Facts:
    return rules.Facts(today=TODAY, deals=values.pop("deals", []), companies=[], **values)


class TestPeriods:
    """FR-CRM-DASH-02 — the financial year runs April to March."""

    @pytest.mark.parametrize(
        ("on", "fy", "quarter"),
        [
            (date(2026, 3, 31), date(2025, 4, 1), date(2026, 1, 1)),
            (date(2026, 4, 1), date(2026, 4, 1), date(2026, 4, 1)),
            (date(2026, 10, 15), date(2026, 4, 1), date(2026, 10, 1)),
            (date(2027, 2, 10), date(2026, 4, 1), date(2027, 1, 1)),
        ],
    )
    def test_fy_and_quarter_bounds(self, on, fy, quarter):
        assert rules.fy_start(on) == fy
        assert rules.quarter_start(on) == quarter

    def test_period_ends(self):
        assert rules.period_end("fy", date(2026, 4, 1)) == date(2027, 3, 31)
        assert rules.period_end("quarter", date(2027, 1, 1)) == date(2027, 3, 31)
        assert rules.period_end("month", date(2026, 2, 1)) == date(2026, 2, 28)

    @pytest.mark.parametrize(
        ("kind", "start", "why"),
        [
            ("month", date(2026, 10, 2), "A target's period starts on the 1st."),
            ("quarter", date(2026, 5, 1), "A quarter starts on 1 April"),
            ("fy", date(2026, 1, 1), "A financial year starts on 1 April."),
        ],
    )
    def test_a_target_period_starts_on_its_first_day(self, kind, start, why):
        refusal = rules.target_refusal(kind, start, None, has_entity=True, workspace="Labs")
        assert refusal and refusal.startswith(why)


class TestPipelineAndBookings:
    def test_open_pipeline_by_stage(self):
        deals = [
            _deal(id="d1", stage="discovery", amount_in_inr=100000, probability=10),
            _deal(id="d2", stage="proposal", amount_in_inr=200000, probability=50),
            _deal(id="d3", stage="proposal", amount_in_inr=None, probability=50),
            _deal(id="d4", stage="closed_won", amount_in_inr=999, won_on="2026-10-02"),
        ]
        panel = rules.pipeline_by_stage(deals, _facts())
        by = {s["stage"]: s for s in panel["stages"]}
        assert [s["stage"] for s in panel["stages"]] == [
            "discovery",
            "qualification",
            "proposal",
            "negotiation",
        ]
        assert by["proposal"] == {
            "stage": "proposal",
            "stage_label": "Proposal",
            "count": 2,
            "amount_in_inr": "200000.00",
            "weighted": "100000.00",
        }
        assert panel["total"] == {"count": 3, "amount_in_inr": "300000.00", "weighted": "110000.00"}

    def test_no_colour_without_a_target(self):
        deals = [_deal(id="d1", stage="closed_won", amount_in_inr=500000, won_on="2026-10-05")]
        rows = rules.bookings(deals, _facts(), rules.Filters(), has_entity=False)
        assert [r["period"] for r in rows] == ["month", "quarter", "fy"]
        assert all("colour" not in r and r["target"] is None for r in rows)
        assert rows[0]["value"] == "500000.00" and rows[0]["count"] == 1

    @pytest.mark.parametrize(
        ("booked", "colour"),
        [(500000, "green"), (400000, "amber"), (300000, "red")],
    )
    def test_colour_against_the_targets_pace(self, booked, colour):
        # 15 of October's 31 days gone: the pace for 1,000,000 is 483,870.97.
        deals = [_deal(id="d1", stage="closed_won", amount_in_inr=booked, won_on="2026-10-05")]
        target = {"period_kind": "month", "period_start": "2026-10-01", "amount_inr": 1000000}
        rows = rules.bookings(deals, _facts(targets=[target]), rules.Filters(), has_entity=False)
        month = rows[0]
        assert (month["target"], month["colour"]) == ("1000000.00", colour)
        assert "colour" not in rows[1]

    def test_a_target_per_entity(self):
        deals = [
            _deal(
                id="d1",
                stage="closed_won",
                amount_in_inr=100,
                won_on="2026-10-05",
                entity="deepweaver",
            ),
        ]
        target = {
            "period_kind": "fy",
            "period_start": "2026-04-01",
            "amount_inr": 100,
            "entity": "deepweaver",
        }
        rows = rules.bookings(deals, _facts(targets=[target]), rules.Filters(), has_entity=True)
        fy = rows[2]
        entities = {e["entity"]: e for e in fy["by_entity"]}
        assert "colour" not in fy
        assert entities["deepweaver"]["colour"] == "green"
        assert "colour" not in entities["nunnari_labs"]

    def test_won_and_lost_this_quarter_with_lost_reasons(self):
        deals = [
            _deal(id="d1", stage="closed_won", amount_in_inr=100, won_on="2026-10-02"),
            _deal(id="d2", stage="closed_won", amount_in_inr=100, won_on="2026-09-30"),
            _deal(id="d3", stage="closed_lost", lost_on="2026-10-03", lost_reason_id="o-price"),
            _deal(id="d4", stage="closed_lost", lost_on="2026-10-04", lost_reason_id="o-price"),
            _deal(id="d5", stage="closed_lost", lost_on="2026-10-04"),
        ]
        panel = rules.won_lost_quarter(deals, _facts(options={"o-price": "Price"}))
        assert panel["won"] == {"count": 1, "amount_in_inr": "100.00"}
        assert panel["lost"]["count"] == 3
        assert panel["lost_reasons"] == [
            {"lost_reason_id": "o-price", "label": "Price", "count": 2},
            {"lost_reason_id": None, "label": "No reason", "count": 1},
        ]

    def test_win_rate_and_average_deal_size_by_entity_and_channel(self):
        deals = [
            _deal(
                id="d1",
                stage="closed_won",
                amount_in_inr=300,
                won_on="2026-06-01",
                entity="nunnari_labs",
                channel_id="ch-ref",
            ),
            _deal(
                id="d2",
                stage="closed_won",
                amount_in_inr=100,
                won_on="2026-07-01",
                entity="deepweaver",
                channel_id="ch-ref",
            ),
            _deal(id="d3", stage="closed_lost", lost_on="2026-08-01", entity="nunnari_labs"),
            _deal(id="d4", stage="closed_won", amount_in_inr=999, won_on="2026-03-01"),
        ]
        panel = rules.win_rates(
            deals, _facts(options={"ch-ref": "Referral"}), rules.Filters(), has_entity=True
        )
        assert panel["overall"] == {
            "won": 2,
            "lost": 1,
            "win_rate": "66.7",
            "average_deal_size": "200.00",
        }
        labs = next(e for e in panel["by_entity"] if e["entity"] == "nunnari_labs")
        assert (labs["won"], labs["lost"], labs["win_rate"]) == (1, 1, "50.0")
        assert [c["label"] for c in panel["by_channel"]] == ["Referral", "No channel"]


class TestTimeInStage:
    """Spec §11 AC "time in stage is correct on the dashboard"."""

    HISTORY = [
        {
            "opportunity_id": "d1",
            "from_stage": None,
            "to_stage": "discovery",
            "at": "2026-10-01T09:00:00+05:30",
        },
        {
            "opportunity_id": "d1",
            "from_stage": "discovery",
            "to_stage": "qualification",
            "at": "2026-10-05T09:00:00+05:30",
        },
        {
            "opportunity_id": "d1",
            "from_stage": "qualification",
            "to_stage": "proposal",
            "at": "2026-10-11T09:00:00+05:30",
        },
        {
            "opportunity_id": "d1",
            "from_stage": "proposal",
            "to_stage": "closed_won",
            "at": "2026-10-13T21:00:00+05:30",
        },
        {
            "opportunity_id": "d2",
            "from_stage": None,
            "to_stage": "discovery",
            "at": "2026-10-03T09:00:00+05:30",
        },
        {
            "opportunity_id": "d2",
            "from_stage": "discovery",
            "to_stage": "qualification",
            "at": "2026-10-05T09:00:00+05:30",
        },
    ]

    def test_time_in_stage_is_correct_on_the_dashboard(self):
        deals = [
            _deal(id="d1", stage="closed_won", won_on="2026-10-13"),
            _deal(id="d2", stage="qualification"),
        ]
        panel = rules.time_in_stage(deals, _facts(history=self.HISTORY), rules.Filters())
        by = {s["stage"]: s for s in panel["stages"]}
        # d1 spent 4 days in Discovery, d2 2: an average of 3.
        assert (by["discovery"]["average_days"], by["discovery"]["stays"]) == ("3.0", 2)
        assert (by["qualification"]["average_days"], by["qualification"]["stays"]) == ("6.0", 1)
        assert by["proposal"]["average_days"] == "2.5"
        # d2 is still in Qualification: an unfinished stay is not counted.
        assert by["negotiation"] == {
            "stage": "negotiation",
            "stage_label": "Negotiation",
            "average_days": None,
            "stays": 0,
        }
        assert panel["days_to_close"] == {"average_days": "12.5", "deals": 1}

    def test_stays_that_ended_outside_the_range_are_left_out(self):
        deals = [_deal(id="d1", stage="closed_won", won_on="2026-10-13")]
        f = rules.Filters(since=date(2026, 10, 6), until=date(2026, 10, 12))
        panel = rules.time_in_stage(deals, _facts(history=self.HISTORY), f)
        by = {s["stage"]: s for s in panel["stages"]}
        assert (by["discovery"]["stays"], by["qualification"]["stays"]) == (0, 1)
        assert panel["days_to_close"]["deals"] == 0


class TestFlagsFunnelAndGroups:
    def test_flag_counts_by_kind_coloured_by_the_worst(self):
        flagged = {
            "crm_opportunities": [
                {
                    "id": "d1",
                    "owner_id": "u1",
                    "flags": [
                        {"kind": "next_step", "level": "amber"},
                        {"kind": "stale_deal", "level": "amber"},
                    ],
                },
                {
                    "id": "d2",
                    "owner_id": "u1",
                    "flags": [
                        {"kind": "next_step", "level": "red"},
                        {"kind": "client_silent", "level": "red"},
                    ],
                },
            ],
            "crm_people": [
                {"id": "p1", "owner_id": "u1", "flags": [{"kind": "reply_owed", "level": "amber"}]},
            ],
        }
        panel = {f["kind"]: f for f in rules.flag_counts(flagged, rules.Filters())}
        assert [f["label"] for f in rules.flag_counts({}, rules.Filters())] == [
            "Replies owed",
            "Silent clients",
            "Overdue next steps",
            "Overdue close dates",
            "Stale deals",
            "Loose ends",
        ]
        # A next step due soon (amber) is not overdue.
        assert (panel["next_step"]["value"], panel["next_step"]["colour"]) == (1, "red")
        assert (panel["stale_deal"]["value"], panel["stale_deal"]["colour"]) == (1, "amber")
        assert (panel["reply_owed"]["value"], panel["reply_owed"]["colour"]) == (1, "amber")
        assert (panel["loose_end"]["value"], panel["loose_end"]["colour"]) == (0, "green")

    def test_the_lead_funnel_leaves_partners_out(self):
        companies = [
            {"id": f"c{n}", "lead_stage": stage}
            for n, stage in enumerate(
                [
                    "new",
                    "new",
                    "contacted",
                    "qualified",
                    "deal_open",
                    "customer",
                    "partner",
                    "partner",
                    "disqualified",
                ]
            )
        ]
        panel = rules.lead_funnel(companies, _facts())
        by = {s["lead_stage"]: s for s in panel["stages"]}
        assert "partner" not in by
        assert (by["new"]["count"], by["new"]["reached"], by["new"]["conversion"]) == (
            2,
            6,
            "66.7",
        )
        assert (by["qualified"]["reached"], by["qualified"]["conversion"]) == (3, "66.7")
        assert by["customer"]["conversion"] is None
        assert panel["disqualified"] == 1

    def test_pipeline_by_channel_and_by_campaign(self):
        deals = [
            _deal(
                id="d1",
                amount_in_inr=100,
                probability=50,
                channel_id="ch-a",
                source_campaign_id="k1",
            ),
            _deal(id="d2", amount_in_inr=100, probability=50),
            _deal(
                id="d3",
                stage="closed_won",
                amount_in_inr=100,
                won_on="2026-10-01",
                channel_id="ch-a",
            ),
        ]
        facts = _facts(deals=deals, options={"ch-a": "Inbound"}, campaigns={"k1": "Webinars"})
        out = rules.build(facts, rules.Filters(), has_entity=False, academy=False)
        assert out["pipeline_by_channel"] == [
            {
                "channel_id": "ch-a",
                "label": "Inbound",
                "count": 1,
                "amount_in_inr": "100.00",
                "weighted": "50.00",
            },
            {
                "channel_id": None,
                "label": "No channel",
                "count": 1,
                "amount_in_inr": "100.00",
                "weighted": "50.00",
            },
        ]
        assert out["pipeline_by_campaign"][0]["label"] == "Webinars"
        assert "pipeline_by_institution_type" not in out

    def test_academy_panels_by_institution_type_and_programme(self):
        """FR-CRM-DASH-03, Q-40."""
        deals = [_deal(id="d1", amount_in_inr=100, company_id="c1", programme_id="pg")]
        facts = _facts(
            deals=deals,
            options={"it": "Engineering college", "pg": "AI course"},
        )
        facts.companies = [{"id": "c1", "lead_stage": "deal_open", "institution_type_id": "it"}]
        out = rules.build(facts, rules.Filters(), has_entity=False, academy=True)
        assert out["pipeline_by_institution_type"][0]["label"] == "Engineering college"
        assert out["pipeline_by_programme"][0]["label"] == "AI course"
        assert "by_entity" not in out["bookings"][0]


class TestActivity:
    """Q-26, FR-CRM-DASH-04 — per owner per week, in name order."""

    def test_owners_in_name_order_never_by_value(self):
        facts = _facts(
            names={"u-z": "Anil", "u-a": "Zara"},
            members=["u-a", "u-z"],
            notes=[{"created_by": "u-a", "created_at": "2026-10-13T10:00:00+05:30"}] * 5,
            tasks=[{"assignee_id": "u-z", "completed_at": "2026-10-14T10:00:00+05:30"}],
            emails=[
                {"owner_id": "u-a", "direction": "inbound", "sent_at": "2026-10-12T10:00:00Z"},
                {"owner_id": "u-a", "direction": "outbound", "sent_at": "2026-10-12T10:00:00Z"},
                {
                    "owner_id": "u-a",
                    "direction": "inbound",
                    "sent_at": "2026-10-12T10:00:00Z",
                    "is_auto": True,
                },
            ],
        )
        f = rules.Filters(since=date(2026, 10, 5), until=TODAY)
        panel = rules.activity(facts, f)
        assert panel["weeks"] == ["2026-10-05", "2026-10-12"]
        assert [o["name"] for o in panel["owners"]] == ["Anil", "Zara"]
        zara = panel["owners"][1]
        assert zara["weeks"][1] == {
            "week_start": "2026-10-12",
            "emails_in": 1,
            "emails_out": 1,
            "notes": 5,
            "tasks": 0,
        }
        assert panel["owners"][0]["totals"]["tasks"] == 1

    def test_at_most_thirteen_weeks(self):
        f = rules.Filters(since=date(2026, 1, 1), until=TODAY)
        assert len(rules.activity(_facts(members=["u"]), f)["weeks"]) == rules.ACTIVITY_WEEKS


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


def _add_deal(fake, ws, **values):
    row = {
        **DEFAULTS["crm_opportunities"],
        "workspace_id": ws["id"],
        "name": "Deal",
        "stage": "proposal",
        "probability": 50,
        "owner_id": OWNER["id"],
        "created_at": "2026-10-01T00:00:00+00:00",
        "created_by": OWNER["id"],
        **values,
    }
    return fake.add("crm_opportunities", **row)


class Capacity:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, start, end):
        self.calls += 1
        return [
            {
                "week_start": "2026-10-12",
                "contributors": 2,
                "supply_hours": "80",
                "confirmed_hours": "10",
                "tentative_hours": "5",
            },
            {
                "week_start": "2026-10-19",
                "contributors": 5,
                "supply_hours": "200",
                "confirmed_hours": "120",
                "tentative_hours": "40",
            },
        ]


@pytest.fixture
def crm(monkeypatch):
    fake = FakeCrmStore().install(monkeypatch)
    labs, academy = two_workspaces(fake)
    for ws in (labs, academy):
        fake.add_deal_lists(ws)
    fake.add_member(academy, VIEWER["id"], "viewer")
    company = fake.add(
        "crm_companies",
        **{
            **DEFAULTS["crm_companies"],
            "workspace_id": labs["id"],
            "name": "Acme",
            "lead_stage": "deal_open",
            "owner_id": OWNER["id"],
            "entities": ["deepweaver"],
        },
    )
    _add_deal(fake, labs, company_id=company["id"], amount_in_inr=300000, entity="deepweaver")
    _add_deal(
        fake,
        labs,
        company_id=company["id"],
        amount_in_inr=100000,
        owner_id=MEMBER["id"],
        name="Deepika's deal",
    )
    monkeypatch.setattr(service, "today_in_company_tz", lambda: TODAY)
    capacity = Capacity()
    monkeypatch.setattr(service.capacity, "weekly", capacity)
    http = crm_client(monkeypatch, fake=fake)
    return http, fake, labs, academy, capacity


def _get(http, who, query=LABS):
    return http.get(f"{API}/dashboard{query}", headers=as_(who))


class TestTheRoute:
    def test_an_admin_sees_every_panel(self, crm):
        http, *_ = crm
        r = _get(http, OWNER["id"])
        assert r.status_code == 200, r.text
        body = r.json()
        for panel in (
            "pipeline_by_stage",
            "bookings",
            "won_lost_quarter",
            "win_rate",
            "time_in_stage",
            "flags",
            "lead_funnel",
            "pipeline_by_channel",
            "pipeline_by_campaign",
            "activity",
            "capacity",
        ):
            assert panel in body, panel
        assert body["pipeline_by_stage"]["total"]["amount_in_inr"] == "400000.00"
        assert body["own_numbers_only"] is False
        assert body["range"] == {"from": "2026-04-01", "to": "2026-10-15"}

    def test_a_member_sees_their_own_numbers_only(self, crm):
        http, *_ = crm
        r = _get(http, MEMBER["id"], f"{LABS}&owner_id={OWNER['id']}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["own_numbers_only"] is True
        assert body["filters"]["owner_id"] == MEMBER["id"]
        assert body["pipeline_by_stage"]["total"]["amount_in_inr"] == "100000.00"
        assert [o["owner_id"] for o in body["activity"]["owners"]] == [MEMBER["id"]]

    def test_a_viewer_has_no_dashboard(self, crm):
        http, *_ = crm
        r = _get(http, VIEWER["id"])
        assert (r.status_code, r.json()["detail"]) == (403, "Viewers have no dashboard.")

    def test_the_entity_filter(self, crm):
        http, *_ = crm
        body = _get(http, OWNER["id"], f"{LABS}&entity=deepweaver").json()
        assert body["pipeline_by_stage"]["total"]["amount_in_inr"] == "300000.00"
        r = _get(http, OWNER["id"], f"{ACADEMY}&entity=deepweaver")
        assert r.status_code == 422

    def test_a_range_that_ends_before_it_starts(self, crm):
        http, *_ = crm
        r = _get(http, OWNER["id"], f"{LABS}&from=2026-10-10&to=2026-10-01")
        assert (r.status_code, r.json()["detail"]) == (422, "The range ends before it starts.")

    def test_an_owner_who_is_not_a_member(self, crm):
        http, *_ = crm
        r = _get(http, OWNER["id"], f"{LABS}&owner_id=u-outsider")
        assert r.status_code == 422

    def test_per_owner_rows_are_in_name_order(self, crm):
        http, *_ = crm
        names = [o["name"] for o in _get(http, OWNER["id"]).json()["activity"]["owners"]]
        assert names == sorted(names, key=str.casefold)


class TestCapacityOnTheDashboard:
    """FR-CRM-CAP-02, Q-39 — Labs admins and staff members only."""

    def test_admins_and_staff_members_see_it_hidden_below_three(self, crm):
        http, *_ = crm
        for who in (OWNER["id"], MEMBER["id"]):
            weeks = _get(http, who).json()["capacity"]["weeks"]
            assert weeks == [
                {"week_start": "2026-10-12", "hidden": "fewer than 3 people"},
                {"week_start": "2026-10-19", "tentative_hours": "40", "free_hours": "80"},
            ]

    def test_not_a_crm_only_member(self, crm):
        http, *_, capacity = crm
        r = _get(http, SELLER["id"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert "pipeline_by_stage" in body
        assert "capacity" not in body
        assert capacity.calls == 0

    def test_not_in_academy(self, crm):
        http, *_ = crm
        body = _get(http, OWNER["id"], ACADEMY).json()
        assert "capacity" not in body
        assert "pipeline_by_institution_type" in body and "pipeline_by_programme" in body


class TestTargets:
    """FR-CRM-LIST-06 — Admins set bookings targets, audited."""

    def test_put_replaces_the_set(self, crm):
        http, fake, *_ = crm
        me = as_(OWNER["id"])
        first = [
            {"period_kind": "month", "period_start": "2026-10-01", "amount_inr": "500000"},
            {
                "entity": "deepweaver",
                "period_kind": "fy",
                "period_start": "2026-04-01",
                "amount_inr": "9000000",
            },
        ]
        r = http.put(f"{API}/targets{LABS}", json=first, headers=me)
        assert r.status_code == 200, r.text
        assert [(t["period_kind"], t["amount_inr"], t["period_end"]) for t in r.json()] == [
            ("month", "500000.00", "2026-10-31"),
            ("fy", "9000000.00", "2027-03-31"),
        ]
        r = http.put(
            f"{API}/targets{LABS}", json=[{**first[0], "amount_inr": "600000"}], headers=me
        )
        assert [t["amount_inr"] for t in r.json()] == ["600000.00"]
        actions = [a["action"] for a in fake.audit if a["target_table"] == "crm_bookings_targets"]
        assert actions == [
            "bookings_target.created",
            "bookings_target.created",
            "bookings_target.purged",
            "bookings_target.updated",
        ]
        month = _get(http, OWNER["id"]).json()["bookings"][0]
        assert month["target"] == "600000.00" and month["colour"] == "red"

    @pytest.mark.parametrize(
        ("body", "detail"),
        [
            (
                [{"period_kind": "quarter", "period_start": "2026-11-01", "amount_inr": "1"}],
                "A quarter starts on 1 April, 1 July, 1 October or 1 January.",
            ),
            (
                [{"period_kind": "month", "period_start": "2026-10-01", "amount_inr": "1"}] * 2,
                "Each period has one target per entity; this list repeats one.",
            ),
        ],
    )
    def test_refusals(self, crm, body, detail):
        http, *_ = crm
        r = http.put(f"{API}/targets{LABS}", json=body, headers=as_(OWNER["id"]))
        assert (r.status_code, r.json()["detail"]) == (422, detail)

    def test_no_entity_outside_labs(self, crm):
        http, *_ = crm
        body = [
            {
                "entity": "deepweaver",
                "period_kind": "fy",
                "period_start": "2026-04-01",
                "amount_inr": "1",
            }
        ]
        r = http.put(f"{API}/targets{ACADEMY}", json=body, headers=as_(OWNER["id"]))
        assert r.status_code == 422

    def test_admins_only(self, crm):
        http, *_ = crm
        assert http.get(f"{API}/targets{LABS}", headers=as_(MEMBER["id"])).status_code == 403
        r = http.put(f"{API}/targets{LABS}", json=[], headers=as_(MEMBER["id"]))
        assert r.status_code == 403
