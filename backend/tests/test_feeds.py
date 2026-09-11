from datetime import date

from app.domain import feeds


def test_key_changes_with_salt_and_is_not_the_user_id():
    a = feeds.feed_key("u1", "s1", "secret")
    assert a != feeds.feed_key("u1", "s2", "secret") and "u1" not in a and len(a) == 40


def test_render_is_all_day_and_category_only():
    ics = feeds.render(
        [{"uid": "b1", "day": date(2026, 9, 14), "summary": "Deepika — Casual leave"}],
        calendar_name="Team",
    )
    assert "DTSTART;VALUE=DATE:20260914" in ics and "DTEND;VALUE=DATE:20260915" in ics
    assert "SUMMARY:Deepika — Casual leave" in ics and "reason" not in ics.lower()
