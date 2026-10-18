-- 055_task_inbox_review.sql
--
-- Spec 008 (tasks) FR-TASK-INBOX-01 to -03, FR-TASK-REVIEW-01; TP-08;
-- data-model §2.10, §5.2, §5.4.
--
-- The Ideas and Reading inbox and the weekly review (phase 4):
--
-- 1. `inbox_items` (FR-TASK-INBOX-01): an idea, link or note of its owner,
--    always private to that owner, admins included. Make it a task records
--    the task it became in `task_id` and sets `processed_at`; Archive sets
--    `processed_at` and `archived` (FR-TASK-INBOX-02). Written only through
--    `task_apply` (049), which already accepts this table once it carries
--    `inbox_items_crm_audit` and re-checks that the actor is the item's
--    owner. The audit rows have no workspace and are left out of the TVA
--    audit by `services/supabase.list_audit` (FR-TASK-CHG-07). Deleting the
--    owner's profile removes their items (ON DELETE CASCADE); 048's
--    `crm_audit_row()` already records that delete as the system. RLS on, no
--    policy, no grant to anon or authenticated: the backend reads it.
-- 2. `profiles.tasks_review_dismissed_week` (FR-TASK-REVIEW-01): the Monday
--    of the week the person dismissed the review panel; the panel stays
--    hidden for the rest of that week.
--
-- Reversible: ALTER TABLE profiles DROP COLUMN tasks_review_dismissed_week;
-- DROP TABLE inbox_items.

-- ---------------------------------------------------------------------------
-- 1. inbox_items — data-model §2.10
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS inbox_items (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id     uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    kind         text NOT NULL CHECK (kind IN ('idea', 'link', 'note')),
    title        text NOT NULL CHECK (btrim(title) <> '' AND char_length(title) <= 200),
    url          text CHECK (url IS NULL OR url ~ '^https?://[^\s]+$' AND char_length(url) <= 2000),
    notes        text CHECK (char_length(notes) <= 20000),
    tags         text[] NOT NULL DEFAULT '{}' CHECK (task_tags_ok(tags)),
    -- Set by Make it a task or Archive.
    processed_at timestamptz,
    archived     boolean NOT NULL DEFAULT false,
    -- The task it became.
    task_id      uuid REFERENCES tasks(id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS inbox_items_unprocessed
    ON inbox_items (owner_id, created_at DESC) WHERE processed_at IS NULL;

DROP TRIGGER IF EXISTS inbox_items_touch ON inbox_items;
CREATE TRIGGER inbox_items_touch BEFORE UPDATE ON inbox_items
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS inbox_items_crm_audit ON inbox_items;
CREATE TRIGGER inbox_items_crm_audit AFTER INSERT OR UPDATE OR DELETE ON inbox_items
    FOR EACH ROW EXECUTE FUNCTION public.crm_audit_row();

ALTER TABLE inbox_items ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON inbox_items FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. profiles.tasks_review_dismissed_week — data-model §2.10
-- ---------------------------------------------------------------------------

ALTER TABLE profiles
    ADD COLUMN IF NOT EXISTS tasks_review_dismissed_week date;
