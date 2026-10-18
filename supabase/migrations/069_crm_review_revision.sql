-- 069_crm_review_revision.sql
--
-- Spec 009 (CRM intelligence layer) FR-CRMAI-REV-04, -08; data-model §3.2,
-- §3.2.2, §3.2.3 (amended); contracts §2.2. A fix to 061: "approving can
-- apply AI text the person never saw".
--
-- While an item is open, a job that summarises the same source again
-- refreshes it in place (`crm_review_upsert`, same dedupe key): a new
-- suggestion, new current values, a new source version. An approval sent
-- for the item as it was shown then applied the newer suggestion, which the
-- person had not seen, and the refreshed current values hid the change.
--
-- 1. `crm_review_items.revision`: 1 when an item is made, one more each time
--    a refresh changes its suggestion, its current values or its source
--    version. The API shows it as the item's `version`; an approval sends it
--    back.
-- 2. `crm_review_upsert()` (061's) replaced: the refresh also counts the
--    revision. Only the lines marked "069" are new.
-- 3. `crm_review_claim()` (061's) replaced, with a fifth parameter
--    `p_expect_revision integer DEFAULT NULL`: when given, the claim wins
--    only while the item is still at that revision; an open item at another
--    revision is CRM09 "This suggestion changed; review it again.", and the
--    item stays open. Only the lines marked "069" are new. A dismissal
--    passes no revision.
--
-- Grants as 061's: EXECUTE revoked from PUBLIC, anon and authenticated and
-- granted to service_role (data-model §5).
--
-- Reversible: DROP FUNCTION crm_review_claim(jsonb, uuid, text, text,
-- integer); re-run 061's crm_review_claim and crm_review_upsert, with their
-- grants; ALTER TABLE crm_review_items DROP COLUMN revision.

ALTER TABLE public.crm_review_items
    ADD COLUMN revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1);

CREATE OR REPLACE FUNCTION public.crm_review_upsert(p_actor jsonb, p_items jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws      uuid := (p_actor ->> 'workspace_id')::uuid;
    item    jsonb;
    deal    jsonb;
    made    uuid;
    created boolean;
    results jsonb := '[]';
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('sync', 'system') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;
    IF jsonb_typeof(p_items) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Nothing to change.';
    END IF;

    FOR item IN SELECT value FROM jsonb_array_elements(p_items) LOOP
        IF EXISTS (
            SELECT 1 FROM crm_review_items
            WHERE workspace_id = ws AND dedupe_key = item ->> 'dedupe_key'
              AND status <> 'open' AND source_version = item ->> 'source_version'
        ) THEN
            results := results || jsonb_build_array(
                jsonb_build_object('id', NULL, 'created', false));
            CONTINUE;
        END IF;

        INSERT INTO crm_review_items AS t (
            workspace_id, type, owner_id, record_type, record_id, member_id, suggestion,
            current_values, source_kind, source_key, source_version, source_link, dedupe_key,
            model, generated_at
        )
        VALUES (
            ws,
            item ->> 'type',
            (item ->> 'owner_id')::uuid,
            item ->> 'record_type',
            (item ->> 'record_id')::uuid,
            (item ->> 'member_id')::uuid,
            coalesce(item -> 'suggestion', '{}'),
            coalesce(item -> 'current_values', '{}'),
            item ->> 'source_kind',
            item ->> 'source_key',
            item ->> 'source_version',
            coalesce(item -> 'source_link', '{}'),
            item ->> 'dedupe_key',
            item ->> 'model',
            (item ->> 'generated_at')::timestamptz
        )
        ON CONFLICT (workspace_id, dedupe_key) WHERE status = 'open' DO UPDATE
        SET suggestion = EXCLUDED.suggestion,
            current_values = EXCLUDED.current_values,
            source_version = EXCLUDED.source_version,
            model = EXCLUDED.model,
            generated_at = EXCLUDED.generated_at,
            -- 069: a refresh that changes what the item shows is a new revision.
            revision = t.revision + CASE
                WHEN (t.suggestion, t.current_values, t.source_version)
                     IS DISTINCT FROM
                     (EXCLUDED.suggestion, EXCLUDED.current_values, EXCLUDED.source_version)
                THEN 1 ELSE 0 END
        RETURNING t.id, (t.xmax = 0) INTO made, created;

        -- Q-07: the deal picker, each choice an open deal of the workspace.
        DELETE FROM crm_review_item_deals WHERE item_id = made;
        FOR deal IN SELECT value FROM jsonb_array_elements(coalesce(item -> 'deal_choices', '[]'))
        LOOP
            IF NOT EXISTS (
                SELECT 1 FROM crm_opportunities o
                WHERE o.workspace_id = ws AND o.id = (deal #>> '{}')::uuid
                  AND o.deleted_at IS NULL
                  AND o.stage IN ('discovery', 'qualification', 'proposal', 'negotiation')
            ) THEN
                RAISE EXCEPTION USING ERRCODE = 'CRM04',
                    MESSAGE = 'No such open deal in this workspace.';
            END IF;
            INSERT INTO crm_review_item_deals (workspace_id, item_id, opportunity_id)
            VALUES (ws, made, (deal #>> '{}')::uuid)
            ON CONFLICT DO NOTHING;
        END LOOP;

        results := results || jsonb_build_array(
            jsonb_build_object('id', made, 'created', created));
    END LOOP;
    RETURN results;
END;
$$;

DROP FUNCTION public.crm_review_claim(jsonb, uuid, text, text);

CREATE OR REPLACE FUNCTION public.crm_review_claim(
    p_actor jsonb, p_item_id uuid, p_status text, p_expect_status text DEFAULT 'open',
    p_expect_revision integer DEFAULT NULL  -- 069
)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws    uuid := (p_actor ->> 'workspace_id')::uuid;
    who   uuid := (p_actor ->> 'user_id')::uuid;
    row_j jsonb;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('user', 'system') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;
    IF p_status IS NULL OR p_status NOT IN ('approved', 'dismissed') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'A suggestion is approved or dismissed.';
    END IF;

    UPDATE crm_review_items t
    SET status = p_status, decided_at = now(), decided_by = who, refusal = NULL
    WHERE t.id = p_item_id AND t.workspace_id = ws AND t.status = p_expect_status
      AND (p_expect_revision IS NULL OR t.revision = p_expect_revision)  -- 069
    RETURNING to_jsonb(t) INTO row_j;
    IF row_j IS NULL THEN
        IF NOT EXISTS (SELECT 1 FROM crm_review_items
                       WHERE id = p_item_id AND workspace_id = ws) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such suggestion.';
        END IF;
        -- 069: still undecided, but no longer what the person was shown.
        IF EXISTS (SELECT 1 FROM crm_review_items
                   WHERE id = p_item_id AND workspace_id = ws
                     AND status = p_expect_status) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM09',
                MESSAGE = 'This suggestion changed; review it again.';
        END IF;
        RAISE EXCEPTION USING ERRCODE = 'CRM09',
            MESSAGE = 'This suggestion was already decided.';
    END IF;

    INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                           target_table, target_id, before, after, suggestion_id)
    VALUES (who, p_actor ->> 'kind', p_actor ->> 'channel', ws, 'review.' || p_status,
            'crm_review_items', p_item_id::text, jsonb_build_object('status', p_expect_status),
            jsonb_build_object('status', p_status), p_item_id);
    RETURN row_j;
END;
$$;

REVOKE ALL ON FUNCTION public.crm_review_upsert(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_review_claim(jsonb, uuid, text, text, integer)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_review_upsert(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_review_claim(jsonb, uuid, text, text, integer)
    TO service_role;
