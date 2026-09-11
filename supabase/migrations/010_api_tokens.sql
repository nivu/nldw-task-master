-- 010_api_tokens.sql
--
-- Spec 004 — personal access tokens, so Claude (or any MCP client) can act as
-- a specific person. Only a hash is stored (FR-TOK-02); the plaintext is shown
-- once at creation and never again.

CREATE TABLE IF NOT EXISTS api_tokens (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    name          text NOT NULL CHECK (btrim(name) <> ''),
    -- sha256 hex of the plaintext. Looked up by equality; never reversible.
    token_hash    text NOT NULL UNIQUE,
    -- The first characters of the plaintext, so a person can tell their tokens
    -- apart in a list without the list being worth stealing.
    prefix        text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    -- FR-TOK-03: 90 days, set by the backend at issue.
    expires_at    timestamptz NOT NULL,
    last_used_at  timestamptz,
    revoked_at    timestamptz
);

CREATE INDEX IF NOT EXISTS api_tokens_user ON api_tokens (user_id);

-- The browser never touches this table: tokens are issued and listed through
-- the API, which holds the service key. RLS on with no policies and no grants
-- means the browser role gets "permission denied" rather than an empty list,
-- which is the louder and therefore better failure.
ALTER TABLE api_tokens ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON api_tokens FROM authenticated, anon;
