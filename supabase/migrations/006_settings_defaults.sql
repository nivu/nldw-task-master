-- 006_settings_defaults.sql
--
-- Policy defaults for the spec's open questions (§11).
--
-- These are production data, not development fixtures — the application needs
-- them to behave correctly on a fresh database, which is why they live in a
-- migration rather than in supabase/seed.sql.
--
-- Every row here answers a question that is still formally open. Changing an
-- answer is an UPDATE to this table, not a migration and not a deploy.

INSERT INTO app_settings (key, value, description) VALUES
    (
        'carry_forward_policy',
        '"rolling"',
        'Q-02. "rolling" carries unused days forward indefinitely from the start '
        'of tracking. "pooling" accumulates them into a year-level pool that '
        'resets each January. Owner: Devansh.'
    ),
    (
        'allow_excess_booking',
        'false',
        'Q-08. false blocks a booking that would take a balance below zero '
        '(FR-BOOK-05). true would allow it and flag it as excess. Blocking is '
        'the safer default and the easier one to relax later. Owner: Devansh.'
    ),
    (
        'sandwich_rule',
        'false',
        'Q-09. Whether leave days spanning a weekend consume the weekend. '
        'false = weekends never consume. The true branch is deliberately NOT '
        'implemented (YAGNI); turning it on needs a small diff in '
        'app/domain/cost.py, not a migration. Owner: Devansh.'
    ),
    (
        'auto_approve_at_lock',
        'true',
        'Q-04. Whether a pending booking that reaches its own date un-actioned '
        'is auto-approved by the nightly sweep. The alternative leaves records '
        'that are neither absence nor attendance. Owner: Devansh.'
    ),
    (
        'lead_view_shows_reason',
        'false',
        'Q-06. false = the team list shows category only; the reason is visible '
        'to the assigned lead on the approval screen. Reasons are personal data '
        '(NFR-05), particularly for sick leave. Owner: Devansh.'
    ),
    (
        'max_future_booking_days',
        '365',
        'A-14. How far ahead a booking may be made. Unbounded future booking '
        'lets one person consume allowance for periods no admin has configured.'
    ),
    (
        'timezone',
        '"Asia/Kolkata"',
        'NFR-03. The single timezone all calendar dates and the lock boundary '
        '(§6.3) are evaluated in. Changing this changes when days close.'
    )
ON CONFLICT (key) DO NOTHING;
