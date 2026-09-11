"""Personal access tokens — spec 004 FR-TOK."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain import tokens

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


class TestGeneration:
    def test_plaintext_is_prefixed_and_only_the_hash_is_stored(self):
        plaintext, row = tokens.generate(NOW)
        assert plaintext.startswith("nunp_")
        assert row["token_hash"] == tokens.hash_token(plaintext)
        assert plaintext not in str(row)

    def test_two_tokens_never_collide(self):
        a, _ = tokens.generate(NOW)
        b, _ = tokens.generate(NOW)
        assert a != b

    def test_display_prefix_is_short(self):
        plaintext, row = tokens.generate(NOW)
        assert plaintext.startswith(str(row["prefix"]))
        assert len(str(row["prefix"])) == 12

    def test_expires_in_ninety_days(self):
        _, row = tokens.generate(NOW)
        assert datetime.fromisoformat(str(row["expires_at"])) == NOW + timedelta(days=90)

    def test_recognises_its_own_tokens_and_not_session_jwts(self):
        assert tokens.is_token("nunp_abc")
        assert not tokens.is_token("eyJhbGciOiJIUzI1NiJ9.x.y")


class TestRefusal:
    def good(self):
        return {"revoked_at": None, "expires_at": (NOW + timedelta(days=1)).isoformat()}

    def test_a_live_token_is_accepted(self):
        assert tokens.refusal(self.good(), NOW) is None

    def test_revoked_is_refused(self):
        row = self.good() | {"revoked_at": NOW.isoformat()}
        assert "revoked" in tokens.refusal(row, NOW)

    def test_expired_is_refused(self):
        row = self.good() | {"expires_at": (NOW - timedelta(seconds=1)).isoformat()}
        assert "expired" in tokens.refusal(row, NOW)

    def test_missing_expiry_is_refused_not_trusted(self):
        assert tokens.refusal({"revoked_at": None, "expires_at": None}, NOW) is not None
