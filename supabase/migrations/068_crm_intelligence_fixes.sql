-- 068_crm_intelligence_fixes.sql
--
-- Spec 009 (CRM intelligence layer) slice S5, the whole-branch review's
-- finding "crm_review_release can hit the open-once unique index when it
-- reopens an item, which loses the refusal and leaves the item 'approved'";
-- FR-CRMAI-REV-06, -08; data-model §3.2.3 (amended in S5).
--
-- An approval claims its item (`approved`), makes the change, then releases
-- it, in three transactions. Meanwhile a job can store a newer suggestion
-- with the same dedupe key and a new source version, which
-- `crm_review_upsert` (061) inserts as a second open item. When the change
-- is then refused, 061's reopen broke `crm_review_items_open_once` (23505):
-- the person saw "That already exists." instead of the refusal, no audit row
-- was written, and the item stayed `approved` with nothing changed.
--
-- 1. `crm_review_release()` (061's) replaced: with a refusal, when another
--    open item of the workspace holds the same dedupe key, the claimed item
--    is not reopened. It expires instead, keeping the refusal, with a
--    `review.expired` audit row (the rule the company merge already applies
--    to two open items of one key, data-model §6); the newer item stays open.
--    Otherwise it reopens as before. Only the lines marked "068" are new.
--
-- Grants as 061's: EXECUTE revoked from PUBLIC, anon and authenticated and
-- granted to service_role (data-model §5).
--
-- Reversible: re-run 061's `crm_review_release`, with its grants.

CREATE OR REPLACE FUNCTION public.crm_review_release(
    p_actor jsonb, p_item_id uuid, p_refusal text, p_result jsonb
)
RETURNS void
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws  uuid := (p_actor ->> 'workspace_id')::uuid;
    who uuid := (p_actor ->> 'user_id')::uuid;
    item_key text;  -- 068
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('user', 'system') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;

    IF p_refusal IS NOT NULL THEN
        -- 068: a newer open item of the same key replaces this one.
        SELECT dedupe_key INTO item_key
        FROM crm_review_items
        WHERE id = p_item_id AND workspace_id = ws AND status = 'approved'
        FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such suggestion.';
        END IF;
        IF EXISTS (
            SELECT 1 FROM crm_review_items
            WHERE workspace_id = ws AND dedupe_key = item_key AND status = 'open'
              AND id <> p_item_id
        ) THEN
            UPDATE crm_review_items
            SET status = 'expired', decided_at = now(), refusal = left(p_refusal, 500)
            WHERE id = p_item_id AND workspace_id = ws AND status = 'approved';
            INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                                   target_table, target_id, before, after, suggestion_id)
            VALUES (who, p_actor ->> 'kind', p_actor ->> 'channel', ws, 'review.expired',
                    'crm_review_items', p_item_id::text, jsonb_build_object('status', 'approved'),
                    jsonb_build_object('status', 'expired'), p_item_id);
            RETURN;
        END IF;
        -- 068 end.
        UPDATE crm_review_items
        SET status = 'open', decided_at = NULL, decided_by = NULL,
            refusal = left(p_refusal, 500)
        WHERE id = p_item_id AND workspace_id = ws AND status = 'approved';
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such suggestion.';
        END IF;
        INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                               target_table, target_id, before, after, suggestion_id)
        VALUES (who, p_actor ->> 'kind', p_actor ->> 'channel', ws, 'review.reopened',
                'crm_review_items', p_item_id::text, jsonb_build_object('status', 'approved'),
                jsonb_build_object('status', 'open'), p_item_id);
    ELSE
        UPDATE crm_review_items SET result = coalesce(p_result, '{}')
        WHERE id = p_item_id AND workspace_id = ws AND status <> 'open';
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such suggestion.';
        END IF;
    END IF;
END;
$$;

REVOKE ALL ON FUNCTION public.crm_review_release(jsonb, uuid, text, jsonb)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_review_release(jsonb, uuid, text, jsonb) TO service_role;
