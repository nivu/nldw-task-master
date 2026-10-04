"""The holiday calendar — spec 001 FR-HOL-08, spec 006 FR-LOC-02."""

from datetime import date

from app.domain import holidays

DIWALI = date(2026, 11, 8)
EXISTING = [{"date": "2026-11-08", "name": "Diwali", "location_id": None}]


def entry(day, name, location_id=None):
    return {"date": day, "name": name, "location_id": location_id}


class TestClash:
    def test_a_holiday_everywhere_covers_every_location(self):
        assert holidays.clash(EXISTING, DIWALI, "loc-chennai")["name"] == "Diwali"
        assert holidays.clash(EXISTING, DIWALI, None)["name"] == "Diwali"

    def test_a_location_holiday_covers_only_that_location(self):
        existing = [{"date": "2026-11-08", "name": "Diwali", "location_id": "loc-chennai"}]
        assert holidays.clash(existing, DIWALI, "loc-chennai") is not None
        assert holidays.clash(existing, DIWALI, "loc-pune") is None

    def test_one_everywhere_clashes_with_one_for_a_location(self):
        existing = [{"date": "2026-11-08", "name": "Diwali", "location_id": "loc-chennai"}]
        assert holidays.clash(existing, DIWALI, None) is not None

    def test_another_date_is_free(self):
        assert holidays.clash(EXISTING, date(2026, 11, 9), None) is None


class TestPlanBulk:
    def test_new_dates_are_declared_in_order(self):
        entries = [
            entry(date(2026, 10, 2), "Gandhi Jayanti"),
            entry(date(2026, 12, 25), "Christmas"),
        ]
        declare, skipped = holidays.plan_bulk(entries, EXISTING)
        assert declare == entries
        assert skipped == []

    def test_an_already_declared_date_is_skipped_with_its_name(self):
        declare, skipped = holidays.plan_bulk([entry(DIWALI, "Deepavali")], EXISTING)
        assert declare == []
        assert skipped == [
            {
                "date": "2026-11-08",
                "name": "Deepavali",
                "location_id": None,
                "reason": "Already a holiday (Diwali).",
            }
        ]

    def test_a_repeated_line_is_skipped_and_the_first_wins(self):
        entries = [entry(date(2026, 12, 25), "Christmas"), entry(date(2026, 12, 25), "Xmas")]
        declare, skipped = holidays.plan_bulk(entries, [])
        assert [d["name"] for d in declare] == ["Christmas"]
        assert skipped[0]["name"] == "Xmas"
        assert skipped[0]["reason"] == "Listed earlier in this list."

    def test_two_locations_may_share_a_date(self):
        entries = [entry(DIWALI, "Diwali", "loc-chennai"), entry(DIWALI, "Diwali", "loc-pune")]
        declare, skipped = holidays.plan_bulk(entries, [])
        assert len(declare) == 2
        assert skipped == []

    def test_everywhere_after_a_location_on_the_same_date_is_skipped(self):
        entries = [entry(DIWALI, "Diwali", "loc-chennai"), entry(DIWALI, "Diwali")]
        declare, skipped = holidays.plan_bulk(entries, [])
        assert len(declare) == 1
        assert len(skipped) == 1
