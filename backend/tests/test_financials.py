"""Project financials — spec 003 §4.

The arithmetic is simple. What these tests protect is the two things that make
a wrong number invisible: an unrated person silently counting as free, and a
rate change silently re-pricing history.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.financials import Effort, person_totals, project_financials

D = Decimal


def eff(user, hours, rate):
    return Effort(user_id=user, hours=D(hours), rate=D(rate) if rate is not None else None)


class TestCogsAndMargin:
    def test_a_fully_rated_project(self):
        r = project_financials(D("10000"), [eff("a", "10", "100"), eff("b", "20", "150")])
        assert r.cogs == D("4000.00")  # 10x100 + 20x150
        assert r.margin == D("6000.00")
        assert r.margin_pct == D("60.00")
        assert r.complete is True
        assert r.unrated_user_ids == ()

    def test_several_entries_for_one_person_fold_together(self):
        r = project_financials(D("1000"), [eff("a", "2", "50"), eff("a", "3", "50")])
        assert len(r.people) == 1
        assert r.people[0].hours == D("5")
        assert r.people[0].cogs == D("250.00")

    def test_margin_is_rounded_to_the_cent(self):
        r = project_financials(D("100"), [eff("a", "3", "11.11")])
        assert r.cogs == D("33.33")
        assert r.margin == D("66.67")


class TestIncompletenessIsLoud:
    """FR-FIN-06 — an unrated person is unknown cost, never zero cost."""

    def test_an_unrated_person_makes_cogs_unknown(self):
        r = project_financials(D("10000"), [eff("a", "10", "100"), eff("b", "20", None)])
        assert r.cogs is None
        assert r.margin is None
        assert r.margin_pct is None
        assert r.complete is False
        assert r.unrated_user_ids == ("b",)

    def test_the_partial_figure_is_still_reported_as_partial(self):
        """A true lower bound is useful. A lower bound presented as the total is
        the thing this module exists to prevent."""
        r = project_financials(D("10000"), [eff("a", "10", "100"), eff("b", "20", None)])
        assert r.cogs_partial == D("1000.00")

    def test_one_unrated_entry_taints_the_whole_person(self):
        """ "Mostly known" is not "known"."""
        r = project_financials(D("1000"), [eff("a", "2", "50"), eff("a", "3", None)])
        assert r.people[0].cogs is None
        assert r.unrated_user_ids == ("a",)

    def test_attributed_revenue_still_works_when_unrated(self):
        """It is a distribution of a known total by hours; it needs no rate."""
        r = project_financials(D("1000"), [eff("a", "10", "100"), eff("b", "30", None)])
        assert r.people[0].attributed_revenue == D("250.00")
        assert r.people[1].attributed_revenue == D("750.00")

    def test_no_efforts_is_not_complete(self):
        """A project nobody has logged against has no COGS and no margin, and
        must not report either as a triumphant 100%."""
        r = project_financials(D("5000"), [])
        assert r.complete is False
        assert r.cogs is None
        assert r.margin is None


class TestNoRevenue:
    def test_cogs_without_revenue(self):
        r = project_financials(None, [eff("a", "10", "100")])
        assert r.cogs == D("1000.00")
        assert r.revenue is None
        assert r.margin is None
        assert r.people[0].attributed_revenue is None

    def test_zero_revenue_gives_no_margin_pct(self):
        """Dividing by zero is not a margin."""
        r = project_financials(D("0"), [eff("a", "1", "10")])
        assert r.margin == D("-10.00")
        assert r.margin_pct is None


class TestAttribution:
    """Spec 003 Q-06 — revenue × (their hours ÷ total hours)."""

    def test_shares_sum_to_revenue(self):
        r = project_financials(
            D("999"), [eff("a", "1", "1"), eff("b", "1", "1"), eff("c", "1", "1")]
        )
        total = sum(p.attributed_revenue for p in r.people)
        # 333.00 x 3 = 999.00 — the rounding happens to land exactly here; the
        # property that matters is that nobody's share is invented.
        assert total == D("999.00")

    def test_people_are_sorted_by_id_not_by_money(self):
        """Spec 003 §9 — ordering by a money column is how a table becomes a
        leaderboard without anybody deciding it should."""
        r = project_financials(D("1000"), [eff("z", "90", "1"), eff("a", "10", "999")])
        assert [p.user_id for p in r.people] == ["a", "z"]


class TestPersonTotals:
    """FR-FIN-05 — across projects."""

    def test_sums_across_projects(self):
        p1 = project_financials(D("1000"), [eff("a", "10", "50")])
        p2 = project_financials(D("2000"), [eff("a", "10", "50"), eff("b", "10", "50")])
        totals = {t.user_id: t for t in person_totals([p1, p2])}
        assert totals["a"].hours == D("20")
        assert totals["a"].cogs == D("1000.00")
        assert totals["a"].attributed_revenue == D("2000.00")  # 1000 + 1000
        assert totals["a"].projects == 2
        assert totals["b"].projects == 1

    def test_unrated_on_any_project_is_incomplete_overall(self):
        p1 = project_financials(D("1000"), [eff("a", "10", "50")])
        p2 = project_financials(D("1000"), [eff("a", "10", None)])
        t = person_totals([p1, p2])[0]
        assert t.complete is False
        assert t.cogs is None

    def test_sorted_by_id_never_by_money(self):
        p = project_financials(D("1000"), [eff("z", "1", "1"), eff("a", "99", "99")])
        assert [t.user_id for t in person_totals([p])] == ["a", "z"]
