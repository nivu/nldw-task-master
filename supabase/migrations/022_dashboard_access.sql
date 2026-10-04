-- 022_dashboard_access.sql
--
-- Spec 003 FR-DASH — the CEO dashboard is seen only by people the owner
-- authorises, whatever their role.
--
-- `is_owner` marks the owner. It is set HERE and only here: no API route, MCP
-- tool or screen can set or change it (the update model forbids the field),
-- so nobody can promote themselves to owner through the app, an admin
-- included. Moving ownership is a new migration, reviewed like any other.
--
-- `dashboard_access` is the authorisation itself. Only the owner may grant or
-- revoke it, through Admin → People; the owner sees the dashboard without it.
-- Both start false, which is what every existing row means.
--
-- At most one owner: the partial unique index refuses a second. On a database
-- with no profile for the owner's email (a fresh build), nobody is owner and
-- the dashboard is refused to everyone until a migration names one.
--
-- No column grant to `authenticated`: the browser never reads profiles
-- directly (all reads go through the backend proxy), as with 015 and 019.
-- There is no profiles update policy (004), so neither column is writable
-- from the browser either.

ALTER TABLE profiles ADD COLUMN IF NOT EXISTS is_owner boolean NOT NULL DEFAULT false;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS dashboard_access boolean NOT NULL DEFAULT false;

UPDATE profiles
SET is_owner = true, dashboard_access = true
WHERE lower(email) = 'navaneeth@nunnarilabs.com';

CREATE UNIQUE INDEX IF NOT EXISTS profiles_one_owner ON profiles (is_owner) WHERE is_owner;
