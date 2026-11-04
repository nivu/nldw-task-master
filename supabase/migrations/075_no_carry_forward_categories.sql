-- 075_no_carry_forward_categories.sql
--
-- 001 FR-BAL-10: leave categories whose unused days lapse at month end.
--
-- Company policy from 2026-11: work from home and sick leave are
-- use-it-or-lose-it each month; casual leave still carries forward under
-- carry_forward_policy. Like 006, this is production data, not a development
-- fixture. Changing the list later is an UPDATE through Admin > Settings (or
-- the update_setting tool), not a migration.

INSERT INTO app_settings (key, value, description) VALUES
    (
        'no_carry_forward_categories',
        '["sick", "wfh"]',
        '001 FR-BAL-10. Leave categories (wfh, casual, sick) whose unused days '
        'lapse at month end instead of carrying forward. Categories not listed '
        'follow carry_forward_policy.'
    )
ON CONFLICT (key) DO NOTHING;
