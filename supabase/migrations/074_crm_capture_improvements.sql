-- 074_crm_capture_improvements.sql
--
-- Spec 010 (automatic email capture) Revision 1, §14: FR-CAP-GRP-01 to -09,
-- FR-CAP-ACAD-03, FR-CAP-INV-05; data-model §9; plan "Revision 1" R1.2,
-- R1.4, R1.5, PD-22 to PD-26, PD-38 to PD-41.
--
-- 1. `crm_mailboxes` gains `invites_rescanned_at` (set once when the
--    invitation rescan finishes; never reset by the API) and
--    `invite_rescan_token` (the rescan's Gmail page token between syncs, at
--    most 512 characters). Both are the sync's progress, not configuration.
-- 2. `crm_audit_row()` replaced: the mailbox skip list gains both columns,
--    so the rescan's bookkeeping writes no audit row; a change of
--    `capture_source` is still audited as `mailbox.updated`.
-- 3. Six pure helpers over a capture item's conversation entries
--    (data-model §9.3): `crm_capture_entries` (an item's `threads`, or the
--    one entry a pre-074 item stands for), `crm_capture_newest` (the
--    greatest `(latest_at, message_id)`), `crm_capture_merge` (one entry
--    into a list, unique by `ref`, never replaced by an older one, a deal
--    document's origin kept, PD-39), `crm_capture_evidence` (the totals
--    the suggestion shows), `crm_capture_link` (the top level from the
--    newest entry) and `crm_capture_person` (a new lead's suggested person
--    follows its newest entry when a conversation leaves, FR-CAP-GRP-03).
--    They read and write no table.
-- 4. `crm_capture_group_upsert(actor, item, entry)`: one capture item per
--    company key (FR-CAP-GRP-01, -02, -06). Skips a conversation state a
--    decided item already lists (FR-CAP-GRP-05), releases the conversation
--    from every other open capture item (FR-CAP-GRP-07; never for, and
--    never removing, a deal document's entry, PD-39), then inserts the item
--    or merges the entry into the open one, taking the incoming
--    suggestion, current values and deal choices only when its entry is
--    the newest. The body of 069's `crm_review_upsert` is copied and
--    extended; 069's function is not replaced.
-- 5. `crm_capture_release_thread(actor, ref, keep)`: removes a
--    conversation from every open capture item but the one keyed `keep`;
--    an item left with none expires (FR-CAP-GRP-07).
-- 6. `crm_capture_consolidate(actor, plan)`: the open per-conversation
--    items made before 074, grouped by Python, become one item per company
--    key, the newest surviving and the others expired with a pointer to it
--    (FR-CAP-GRP-08, PD-26).
-- 7. `crm_review_release()` replaced: a refused capture item merges its
--    conversations into the open item that took its key before it
--    expires, and one that reopens leaves out every conversation another
--    open item holds now (PD-38); the merge, too, leaves out every
--    conversation a third open item holds now (FR-CAP-GRP-07).
-- 8. `crm_workspace_settings_key_check` widened with `capture_routing`,
--    seeded `{"accept_routed": true}` in every workspace under the system
--    actor (audited `setting.created`), and `crm_workspaces_seed_ai()`
--    replaced so a workspace created later is seeded too (FR-CAP-ACAD-03,
--    PD-41).
-- 9. `crm_capture_apply_routed(actor, run, routed, counts)`: called by
--    Python just before 073's `crm_capture_apply`, under the same checks
--    and the same capture lock. Each routed Academy candidate of the run
--    (`routed` = `{candidate id: boolean}`) keeps whether it was handed
--    over (`finding.routed`, `duplicate_company_id` null), as a live
--    judgement records it (FR-CAP-ACAD-02), and the run's stored counts
--    gain `academy` and `academy_unrouted`, which its candidates, live
--    decisions once applied, no longer give (FR-CAP-RPT-09).
--
-- Every 074 function that changes items first takes the workspace's
-- capture lock (072: `pg_advisory_xact_lock(hashtext('crm_capture:' ||
-- workspace))`), so they run one after another; approvals (069's claim) do
-- not take it, so every row they change is read `FOR UPDATE` and re-checked
-- `open` first, and a row no longer open is never changed.
--
-- Replaced functions, copied from their latest definitions in the tree
-- (`grep -l "CREATE OR REPLACE FUNCTION public.<name>("
-- supabase/migrations/*.sql | sort | tail -1`), with only the lines marked
-- "074" added: crm_audit_row from 071_crm_capture_sync.sql,
-- crm_review_release from 068_crm_intelligence_fixes.sql,
-- crm_workspaces_seed_ai from 072_crm_capture_discovery.sql. The key CHECK
-- is dropped by name and re-added under the same name (072's list plus
-- `capture_routing`). `crm_review_upsert`'s body (069) is copied into
-- `crm_capture_group_upsert`; 069's function is unchanged, and so is 073's
-- `crm_capture_supersede`, which new code no longer calls (PD-25).
--
-- No new table and no uuid[] column. EXECUTE on every function here is
-- revoked from PUBLIC, anon and authenticated and granted to service_role
-- (data-model §9.5).
--
-- Reversible: DROP FUNCTION crm_capture_apply_routed(jsonb, uuid, jsonb, jsonb),
-- crm_capture_group_upsert(jsonb, jsonb, jsonb),
-- crm_capture_release_thread(jsonb, text, text),
-- crm_capture_consolidate(jsonb, jsonb), crm_capture_link(jsonb, jsonb),
-- crm_capture_evidence(jsonb, jsonb), crm_capture_merge(jsonb, jsonb),
-- crm_capture_person(text, jsonb, jsonb, jsonb), crm_capture_newest(jsonb), crm_capture_entries(jsonb); re-run 071's
-- crm_audit_row, 068's crm_review_release and 072's crm_workspaces_seed_ai
-- with their grants; delete the `capture_routing` rows (under a system
-- actor) and re-add 072's key CHECK; ALTER TABLE crm_mailboxes DROP COLUMN
-- invite_rescan_token, DROP COLUMN invites_rescanned_at. Grouped items stay
-- readable by 073's code (their top-level mailbox_id and thread_id are
-- kept); consolidated items stay expired.

-- ---------------------------------------------------------------------------
-- 1. crm_mailboxes — data-model §9.2 (FR-CAP-INV-05)
-- ---------------------------------------------------------------------------

ALTER TABLE crm_mailboxes
    ADD COLUMN IF NOT EXISTS invites_rescanned_at timestamptz,
    ADD COLUMN IF NOT EXISTS invite_rescan_token text
        CONSTRAINT crm_mailboxes_invite_rescan_token_check
        CHECK (char_length(invite_rescan_token) <= 512);

-- ---------------------------------------------------------------------------
-- 2. crm_audit_row — 071's, the rescan's progress skipped
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_audit_row()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    -- NEW is unassigned on DELETE and OLD on INSERT, so read rows as jsonb.
    new_j    jsonb := CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END;
    old_j    jsonb := CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END;
    r        jsonb := coalesce(new_j, old_j);
    changed  text[];
    before_j jsonb;
    after_j  jsonb;
    actor    jsonb;
    act      text;
    noun     text;
BEGIN
    -- Skip first, so a skippable write never needs an actor.
    IF TG_OP = 'UPDATE' THEN
        SELECT coalesce(array_agg(n.key ORDER BY n.key), '{}')
        INTO changed
        FROM jsonb_each(new_j) AS n
        WHERE n.key NOT IN ('updated_at', 'updated_by')
          AND n.value IS DISTINCT FROM old_j -> n.key;
        -- A select option's position is not kanban order: reordering a list
        -- is an admin settings change and is audited (FR-CRM-LIST-01, AUD-01).
        -- 039: a mailbox's sync cursor is the sync's own bookkeeping, written
        -- every page; the settings an admin changes are still audited.
        IF changed <@ ARRAY['last_used_at']
           -- 067 (009 FR-CRMAI-REL-02): a contact recency recompute is not an
           -- edit either.
           OR changed <@ ARRAY['flag_level', 'flags', 'flags_computed_at', 'contact_recency']
           OR (changed <@ ARRAY['position'] AND TG_TABLE_NAME <> 'crm_select_options')
           -- 071 (010 FR-CAP-SRC-06, PD-3): the deal-document cursor and
           -- the switch-back reconcile point are the sync's bookkeeping too.
           -- 074 (010 FR-CAP-INV-05): and the invitation rescan's progress.
           OR (TG_TABLE_NAME = 'crm_mailboxes'
               AND changed <@ ARRAY['history_id', 'page_token', 'backfilled_at',
                                    'last_synced_at', 'docs_checked_at',
                                    'reconcile_after',
                                    'invites_rescanned_at', 'invite_rescan_token']) THEN  -- 074
            RETURN NULL;
        END IF;
        SELECT jsonb_object_agg(k, old_j -> k), jsonb_object_agg(k, new_j -> k)
        INTO before_j, after_j
        FROM unnest(changed) AS k;
    ELSIF TG_OP = 'INSERT' THEN
        after_j := new_j;
    ELSE
        before_j := old_j;
    END IF;

    actor := nullif(current_setting('crm.actor', true), '')::jsonb;
    -- Deleting a profile (or its auth user) cascades to its memberships and
    -- the note mentions naming it, its favourites and private views, and
    -- clears created_by on what it authored, with no actor set. Those referential actions are recorded as the
    -- system, not refused, so a TVA account can still be deleted. They are
    -- told apart by the profile being gone already: a direct write about a
    -- person who still exists is not one.
    IF actor IS NULL
       AND ((TG_OP = 'DELETE' AND TG_TABLE_NAME = 'crm_memberships'
             AND NOT EXISTS (SELECT 1 FROM profiles WHERE id = (old_j ->> 'user_id')::uuid))
            OR (TG_OP = 'DELETE' AND TG_TABLE_NAME = 'crm_note_mentions'
                AND NOT EXISTS (
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'member_id')::uuid))
            OR (TG_OP = 'DELETE' AND TG_TABLE_NAME = 'crm_favourites'
                AND NOT EXISTS (
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'user_id')::uuid))
            OR (TG_OP = 'DELETE' AND TG_TABLE_NAME = 'crm_saved_views'
                AND old_j ->> 'owner_id' IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'owner_id')::uuid))
            OR (TG_OP = 'UPDATE' AND changed <@ ARRAY['created_by']
                AND new_j ->> 'created_by' IS NULL
                AND NOT EXISTS (
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'created_by')::uuid))
            -- 008: a task's Waiting on person or assigner, cleared because
            -- that profile was deleted (ON DELETE SET NULL), and an inbox
            -- item removed with its owner (ON DELETE CASCADE).
            OR (TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'tasks'
                AND changed <@ ARRAY['waiting_on_id', 'assigned_by']
                AND NOT EXISTS (
                    SELECT 1 FROM unnest(changed) AS c
                    WHERE new_j ->> c IS NOT NULL
                       OR EXISTS (SELECT 1 FROM profiles WHERE id = (old_j ->> c)::uuid)))
            OR (TG_OP = 'DELETE' AND TG_TABLE_NAME = 'inbox_items'
                AND NOT EXISTS (
                    SELECT 1 FROM profiles WHERE id = (old_j ->> 'owner_id')::uuid))) THEN
        actor := '{"kind": "system", "user_id": null, "channel": null}';
    END IF;
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'A CRM change must be made through the CRM.';
    END IF;

    act := nullif(current_setting('crm.action', true), '');
    IF act IS NULL THEN
        noun := CASE TG_TABLE_NAME
            WHEN 'crm_companies' THEN 'company'
            WHEN 'crm_people' THEN 'person'
            WHEN 'crm_opportunities' THEN 'opportunity'
            WHEN 'crm_workspace_settings' THEN 'setting'
            WHEN 'crm_select_options' THEN 'option'
            WHEN 'crm_mailboxes' THEN 'mailbox'
            ELSE regexp_replace(regexp_replace(TG_TABLE_NAME, '^crm_', ''), 's$', '')
        END;
        act := noun || '.' || CASE
            WHEN TG_OP = 'INSERT' THEN 'created'
            WHEN TG_OP = 'DELETE' THEN 'purged'
            WHEN old_j ->> 'deleted_at' IS NULL AND new_j ->> 'deleted_at' IS NOT NULL
                THEN 'deleted'
            WHEN old_j ->> 'deleted_at' IS NOT NULL AND new_j ->> 'deleted_at' IS NULL
                THEN 'restored'
            ELSE 'updated'
        END;
    END IF;

    INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                           target_table, target_id, before, after, suggestion_id)
    VALUES (
        (actor ->> 'user_id')::uuid,
        actor ->> 'kind',
        actor ->> 'channel',
        -- crm_workspaces has no workspace_id column: NEW.workspace_id would
        -- raise there, so the workspace is read from the row as jsonb.
        -- 008: a task link with no workspace (a TVA project link) takes its
        -- task's, null for a personal task or one already gone.
        coalesce(r ->> 'workspace_id',
                 CASE WHEN TG_TABLE_NAME = 'crm_workspaces' THEN r ->> 'id' END,
                 CASE WHEN TG_TABLE_NAME = 'task_links' THEN (
                     SELECT k.workspace_id::text FROM tasks k
                     WHERE k.id = (r ->> 'task_id')::uuid
                 ) END)::uuid,
        act,
        TG_TABLE_NAME,
        r ->> 'id',
        before_j,
        after_j,
        -- 061 (009 FR-CRMAI-REV-05): the Review Queue item an approval made
        -- this change for; only the approval path sets it on the actor.
        (actor ->> 'suggestion_id')::uuid
    );
    RETURN NULL;
END;
$$;

-- ---------------------------------------------------------------------------
-- 3. Conversation entries — data-model §9.3 (pure helpers)
-- ---------------------------------------------------------------------------

-- An item's entries: its `source_link.threads`; for an item made before
-- 074 (`source_key` `gmail:<mailbox>:<thread>`), the one entry it stands
-- for, built from its link, its source version and its evidence (a deal
-- document's evidence has `message_at` instead of the two dates).
CREATE OR REPLACE FUNCTION public.crm_capture_entries(p_item jsonb)
RETURNS jsonb
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
    SELECT CASE
        WHEN jsonb_typeof(p_item -> 'source_link' -> 'threads') = 'array'
            THEN p_item -> 'source_link' -> 'threads'
        WHEN p_item ->> 'source_key' LIKE 'gmail:%' THEN jsonb_build_array(jsonb_build_object(
            'ref', p_item ->> 'source_key',
            'mailbox_id', p_item -> 'source_link' ->> 'mailbox_id',
            'thread_id', p_item -> 'source_link' ->> 'thread_id',
            'message_id', p_item ->> 'source_version',
            'first_at', coalesce(ev ->> 'first_at', ev ->> 'message_at'),
            'latest_at', coalesce(ev ->> 'latest_at', ev ->> 'message_at'),
            'message_count', coalesce(ev -> 'message_count', '0'::jsonb),
            'owner_replied', coalesce(ev -> 'owner_replied', 'null'::jsonb),
            'origin', coalesce(ev ->> 'origin', 'discovery'),
            'reason', ev ->> 'reason',
            'confidence', ev ->> 'confidence',
            'person_email', coalesce(p_item -> 'suggestion' ->> 'email', ev ->> 'email'),
            'first_name', p_item -> 'suggestion' ->> 'first_name',
            'last_name', p_item -> 'suggestion' ->> 'last_name'
        ))
        ELSE '[]'::jsonb
    END
    FROM (SELECT p_item -> 'suggestion' -> 'evidence' AS ev) AS e;
$$;

-- The newest entry: the greatest (latest_at, message_id); NULL for none.
CREATE OR REPLACE FUNCTION public.crm_capture_newest(p_threads jsonb)
RETURNS jsonb
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
    SELECT e
    FROM jsonb_array_elements(coalesce(p_threads, '[]'::jsonb)) AS x(e)
    ORDER BY (e ->> 'latest_at')::timestamptz DESC NULLS LAST,
             (e ->> 'message_id') COLLATE "C" DESC NULLS LAST
    LIMIT 1;
$$;

-- One entry into a list: an entry with the same `ref` is replaced unless
-- the new one is older; otherwise the entry is added at the end. An entry
-- of a deal document stays one whichever of the two is kept (PD-39), so
-- discovery never makes it releasable.
CREATE OR REPLACE FUNCTION public.crm_capture_merge(p_threads jsonb, p_entry jsonb)
RETURNS jsonb
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
    SELECT CASE
        WHEN EXISTS (
            SELECT 1 FROM jsonb_array_elements(t) AS x(e) WHERE e ->> 'ref' = p_entry ->> 'ref'
        ) THEN (
            SELECT jsonb_agg(
                CASE WHEN e ->> 'ref' <> p_entry ->> 'ref' THEN e
                     ELSE CASE WHEN coalesce((p_entry ->> 'latest_at')::timestamptz, '-infinity')
                                    >= coalesce((e ->> 'latest_at')::timestamptz, '-infinity')
                               THEN p_entry ELSE e END
                          || CASE WHEN 'deal_document' IN (e ->> 'origin', p_entry ->> 'origin')
                                  THEN jsonb_build_object('origin', 'deal_document')
                                  ELSE '{}'::jsonb END
                END
                ORDER BY n)
            FROM jsonb_array_elements(t) WITH ORDINALITY AS x(e, n)
        )
        ELSE t || jsonb_build_array(p_entry)
    END
    FROM (SELECT coalesce(p_threads, '[]'::jsonb) AS t) AS s;
$$;

-- The totals a capture item shows (data-model §9.3): the first date, the
-- latest, the messages summed, the conversations counted, whether the
-- owner replied in any (NULL when no entry says), and the newest entry's
-- reason and confidence. Everything else in the evidence is kept.
CREATE OR REPLACE FUNCTION public.crm_capture_evidence(p_evidence jsonb, p_threads jsonb)
RETURNS jsonb
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
    SELECT CASE WHEN jsonb_array_length(coalesce(p_threads, '[]'::jsonb)) = 0
        THEN coalesce(p_evidence, '{}'::jsonb)
        ELSE coalesce(p_evidence, '{}'::jsonb) || jsonb_build_object(
            'first_at', (
                SELECT e -> 'first_at' FROM jsonb_array_elements(p_threads) AS x(e)
                WHERE e ->> 'first_at' IS NOT NULL
                ORDER BY (e ->> 'first_at')::timestamptz LIMIT 1
            ),
            'latest_at', (
                SELECT e -> 'latest_at' FROM jsonb_array_elements(p_threads) AS x(e)
                WHERE e ->> 'latest_at' IS NOT NULL
                ORDER BY (e ->> 'latest_at')::timestamptz DESC LIMIT 1
            ),
            'message_count', (
                SELECT coalesce(sum(coalesce((e ->> 'message_count')::integer, 0)), 0)
                FROM jsonb_array_elements(p_threads) AS x(e)
            ),
            'conversations', jsonb_array_length(p_threads),
            'owner_replied', (
                SELECT CASE
                    WHEN bool_or((e ->> 'owner_replied')::boolean) THEN to_jsonb(true)
                    WHEN count(e ->> 'owner_replied') > 0 THEN to_jsonb(false)
                    ELSE 'null'::jsonb
                END
                FROM jsonb_array_elements(p_threads) AS x(e)
            ),
            'reason', crm_capture_newest(p_threads) -> 'reason',
            'confidence', crm_capture_newest(p_threads) -> 'confidence'
        )
    END;
$$;

-- An item's `source_link` for its entries: the list, and the newest
-- entry's mailbox and thread at the top level (so 073's "Open
-- conversation" keeps working); an Academy entry has no mailbox, and its
-- item none at the top level.
CREATE OR REPLACE FUNCTION public.crm_capture_link(p_link jsonb, p_threads jsonb)
RETURNS jsonb
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
    SELECT (coalesce(p_link, '{}'::jsonb) - 'threads' - 'mailbox_id' - 'thread_id')
        || CASE WHEN newest ->> 'mailbox_id' IS NOT NULL
                THEN jsonb_build_object('mailbox_id', newest ->> 'mailbox_id',
                                        'thread_id', newest ->> 'thread_id')
                WHEN jsonb_array_length(coalesce(p_threads, '[]'::jsonb)) = 0
                THEN coalesce(p_link, '{}'::jsonb) - 'threads'
                ELSE '{}'::jsonb END
        || jsonb_build_object('threads', coalesce(p_threads, '[]'::jsonb))
    FROM (SELECT crm_capture_newest(p_threads) AS newest) AS n;
$$;

-- A new lead's suggested person is its newest conversation's sender
-- (FR-CAP-GRP-03): when conversations leave (`p_entries` before, `p_kept`
-- after) and the newest is another one, the suggestion's email and names
-- become that entry's, and the job title, which no entry carries, is
-- dropped. A deal signal names no person and is returned as it is.
CREATE OR REPLACE FUNCTION public.crm_capture_person(
    p_type text, p_suggestion jsonb, p_entries jsonb, p_kept jsonb
)
RETURNS jsonb
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
    SELECT CASE
        WHEN p_type IS DISTINCT FROM 'new_lead' OR n IS NULL
             OR n IS NOT DISTINCT FROM crm_capture_newest(p_entries)
            THEN p_suggestion
        ELSE (coalesce(p_suggestion, '{}'::jsonb) - 'email' - 'first_name' - 'last_name' - 'title')
             || jsonb_strip_nulls(jsonb_build_object(
                    'email', n -> 'person_email',
                    'first_name', n -> 'first_name',
                    'last_name', n -> 'last_name'))
    END
    FROM (SELECT crm_capture_newest(p_kept) AS n) AS s;
$$;

-- ---------------------------------------------------------------------------
-- 5. crm_capture_release_thread — FR-CAP-GRP-07 (defined before its caller)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_capture_release_thread(
    p_actor jsonb, p_ref text, p_keep text
)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws      uuid := (p_actor ->> 'workspace_id')::uuid;
    row_r   crm_review_items%ROWTYPE;
    r       record;
    entries jsonb;
    kept    jsonb;
    removed integer := 0;
    gone    integer := 0;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('system', 'sync') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;
    IF p_ref IS NULL OR p_keep IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Nothing to change.';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('crm_capture:' || ws::text));
    PERFORM set_config('crm.actor', p_actor::text, true);

    FOR r IN
        SELECT id FROM crm_review_items
        WHERE workspace_id = ws AND status = 'open'
          AND type IN ('new_lead', 'deal_signal') AND source_kind = 'email_thread'
          AND dedupe_key <> p_keep
          AND ((source_link -> 'threads') @> jsonb_build_array(jsonb_build_object('ref', p_ref))
               OR source_key = p_ref)
        ORDER BY id
    LOOP
        -- An approval may have claimed it meanwhile: only an open row changes.
        SELECT * INTO row_r FROM crm_review_items
        WHERE id = r.id AND workspace_id = ws AND status = 'open'
        FOR UPDATE;
        CONTINUE WHEN NOT FOUND;
        entries := crm_capture_entries(to_jsonb(row_r));
        -- PD-39: a deal document's entry is never released.
        SELECT coalesce(jsonb_agg(e ORDER BY n), '[]'::jsonb) INTO kept
        FROM jsonb_array_elements(entries) WITH ORDINALITY AS x(e, n)
        WHERE NOT (e ->> 'ref' = p_ref
                   AND coalesce(e ->> 'origin', 'discovery') <> 'deal_document');
        CONTINUE WHEN jsonb_array_length(kept) = jsonb_array_length(entries);
        removed := removed + 1;
        IF jsonb_array_length(kept) = 0 THEN
            UPDATE crm_review_items
            SET status = 'expired', decided_at = now(),
                source_link = CASE WHEN source_link ? 'threads'
                                   THEN jsonb_set(source_link, '{threads}', '[]'::jsonb)
                                   ELSE source_link END
            WHERE id = row_r.id;
            INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                                   target_table, target_id, before, after, suggestion_id)
            VALUES ((p_actor ->> 'user_id')::uuid, p_actor ->> 'kind', p_actor ->> 'channel', ws,
                    'review.expired', 'crm_review_items', row_r.id::text,
                    jsonb_build_object('status', 'open'), jsonb_build_object('status', 'expired'),
                    row_r.id);
            gone := gone + 1;
        ELSE
            UPDATE crm_review_items t
            SET source_link = crm_capture_link(t.source_link, kept),
                suggestion = jsonb_set(
                    crm_capture_person(t.type, t.suggestion, entries, kept), '{evidence}',
                    crm_capture_evidence(t.suggestion -> 'evidence', kept)),
                source_version = crm_capture_newest(kept) ->> 'message_id',
                revision = t.revision + 1
            WHERE t.id = row_r.id;
        END IF;
    END LOOP;
    RETURN jsonb_build_object('removed', removed, 'expired', gone);
END;
$$;

-- ---------------------------------------------------------------------------
-- 4. crm_capture_group_upsert — FR-CAP-GRP-01, -02, -05, -06, -07
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_capture_group_upsert(
    p_actor jsonb, p_item jsonb, p_entry jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws       uuid := (p_actor ->> 'workspace_id')::uuid;
    ref      text := p_entry ->> 'ref';
    msg      text := p_entry ->> 'message_id';
    key      text := p_item ->> 'dedupe_key';
    document boolean := coalesce(p_entry ->> 'origin', '') = 'deal_document';
    row_r    crm_review_items%ROWTYPE;
    threads  jsonb;
    newest   jsonb;
    take     boolean;
    sugg     jsonb;
    vals     jsonb;
    link     jsonb;
    deal     jsonb;
    made     uuid;
    rev      integer;
    released integer := 0;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('sync', 'system') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;
    IF jsonb_typeof(p_item) IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_entry) IS DISTINCT FROM 'object'
       OR ref IS NULL OR msg IS NULL OR key IS NULL
       OR coalesce(p_item ->> 'type', '') NOT IN ('new_lead', 'deal_signal') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Nothing to change.';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('crm_capture:' || ws::text));
    PERFORM set_config('crm.actor', p_actor::text, true);

    -- 1. FR-CAP-GRP-05 (PD-24): a conversation state a decided item listed
    --    never comes back, whatever is re-judged or re-applied. An item a
    --    consolidation merged, or one a refused approval expired, is no
    --    decision. A deal document's state is per company (PD-39): one
    --    document to two companies keeps a signal for each. It is compared
    --    by the decided item's target, not its key, so a deal signal decided
    --    before 074 (its key per conversation) still counts (review F1).
    IF EXISTS (
        SELECT 1 FROM crm_review_items r
        WHERE r.workspace_id = ws AND r.status <> 'open'
          AND r.type IN ('new_lead', 'deal_signal') AND r.source_kind = 'email_thread'
          AND NOT (r.result ? 'superseded_by')
          AND NOT (r.status = 'expired' AND r.refusal IS NOT NULL)
          AND (NOT document
               OR (r.type = 'deal_signal' AND r.record_type = 'company'
                   AND r.record_id = (p_item ->> 'record_id')::uuid))
          AND ((r.source_link -> 'threads')
                   @> jsonb_build_array(jsonb_build_object('ref', ref, 'message_id', msg))
               OR (r.source_key = ref AND r.source_version = msg))
    ) THEN
        RETURN jsonb_build_object('id', NULL, 'created', false, 'skipped', true, 'released', 0);
    END IF;

    -- 2. FR-CAP-GRP-07: the conversation leaves every other open item.
    IF NOT document THEN
        released := (crm_capture_release_thread(p_actor, ref, key) ->> 'removed')::integer;
    END IF;

    -- 3. The open item of the key, read FOR UPDATE: one claimed meanwhile is
    --    no longer open, and a fresh one is made (PD-38).
    SELECT * INTO row_r FROM crm_review_items
    WHERE workspace_id = ws AND dedupe_key = key AND status = 'open'
    FOR UPDATE;

    IF NOT FOUND THEN
        threads := jsonb_build_array(p_entry);
        -- 069's insert (crm_review_upsert), the entry the item's list.  -- 074
        INSERT INTO crm_review_items AS t (
            workspace_id, type, owner_id, record_type, record_id, member_id, suggestion,
            current_values, source_kind, source_key, source_version, source_link, dedupe_key,
            model, generated_at
        )
        VALUES (
            ws,
            p_item ->> 'type',
            (p_item ->> 'owner_id')::uuid,
            p_item ->> 'record_type',
            (p_item ->> 'record_id')::uuid,
            NULL,
            jsonb_set(coalesce(p_item -> 'suggestion', '{}'), '{evidence}',
                      crm_capture_evidence(p_item -> 'suggestion' -> 'evidence', threads)),
            coalesce(p_item -> 'current_values', '{}'),
            p_item ->> 'source_kind',
            p_item ->> 'source_key',
            msg,
            crm_capture_link(p_item -> 'source_link', threads),
            key,
            p_item ->> 'model',
            (p_item ->> 'generated_at')::timestamptz
        )
        RETURNING t.id, t.revision INTO made, rev;
        take := true;
    ELSE
        threads := crm_capture_merge(crm_capture_entries(to_jsonb(row_r)), p_entry);
        newest := crm_capture_newest(threads);
        -- The stage, the person and the deals offered come from one entry,
        -- the newest, whatever order the entries arrive in (FR-CAP-GRP-06).
        take := newest ->> 'ref' = ref AND newest ->> 'message_id' IS NOT DISTINCT FROM msg;
        sugg := CASE WHEN take THEN coalesce(p_item -> 'suggestion', '{}') ELSE row_r.suggestion END;
        sugg := jsonb_set(sugg, '{evidence}', crm_capture_evidence(sugg -> 'evidence', threads));
        vals := CASE WHEN take THEN coalesce(p_item -> 'current_values', '{}')
                     ELSE row_r.current_values END;
        link := crm_capture_link(row_r.source_link, threads);
        UPDATE crm_review_items t
        SET suggestion = sugg,
            current_values = vals,
            source_key = p_item ->> 'source_key',
            source_link = link,
            source_version = newest ->> 'message_id',
            model = CASE WHEN take THEN p_item ->> 'model' ELSE t.model END,
            generated_at = CASE WHEN take THEN (p_item ->> 'generated_at')::timestamptz
                                ELSE t.generated_at END,
            -- 069: a refresh that changes what the item shows is a new revision.
            revision = t.revision + CASE
                WHEN (t.suggestion, t.current_values, t.source_version, t.source_link)
                     IS DISTINCT FROM (sugg, vals, newest ->> 'message_id', link)
                THEN 1 ELSE 0 END
        WHERE t.id = row_r.id
        RETURNING t.id, t.revision INTO made, rev;
    END IF;

    IF take THEN
        -- Q-07: the deal picker, each choice an open deal of the workspace.
        DELETE FROM crm_review_item_deals WHERE item_id = made;
        FOR deal IN SELECT value FROM jsonb_array_elements(coalesce(p_item -> 'deal_choices', '[]'))
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
    END IF;

    RETURN jsonb_build_object('id', made, 'created', row_r.id IS NULL, 'skipped', false,
                              'revision', rev, 'released', released);
END;
$$;

-- ---------------------------------------------------------------------------
-- 6. crm_capture_consolidate — FR-CAP-GRP-08 (PD-26, PD-40)
-- ---------------------------------------------------------------------------

-- p_plan: [{source_key, dedupe_key, record_type, record_id, company_label,
-- members: [uuid]}], grouped by Python. Per group, under the capture lock:
-- the members still open (and an open item already holding the key) are
-- read FOR UPDATE; the newest by creation, then id, survives with every
-- member's entries; the others expire first, each pointing at it.
CREATE OR REPLACE FUNCTION public.crm_capture_consolidate(p_actor jsonb, p_plan jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws       uuid := (p_actor ->> 'workspace_id')::uuid;
    g        jsonb;
    e        jsonb;
    members  uuid[];
    ids      uuid[];
    r        crm_review_items%ROWTYPE;
    survivor crm_review_items%ROWTYPE;
    donor    crm_review_items%ROWTYPE;
    threads  jsonb;
    newest   jsonb;
    sugg     jsonb;
    label    text;
    groups   integer := 0;
    gone     integer := 0;
    rekeyed  integer := 0;
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('system', 'sync') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;
    IF jsonb_typeof(p_plan) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Nothing to change.';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('crm_capture:' || ws::text));
    PERFORM set_config('crm.actor', p_actor::text, true);

    FOR g IN SELECT value FROM jsonb_array_elements(p_plan) LOOP
        IF g ->> 'source_key' IS NULL OR g ->> 'dedupe_key' IS NULL THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Nothing to change.';
        END IF;
        members := ARRAY(
            SELECT (m #>> '{}')::uuid FROM jsonb_array_elements(coalesce(g -> 'members', '[]')) m
        );
        -- Only what is still open is read or changed: a decision made
        -- meanwhile is never touched.
        ids := ARRAY(
            SELECT t.id FROM crm_review_items t
            WHERE t.workspace_id = ws AND t.status = 'open'
              AND t.type IN ('new_lead', 'deal_signal') AND t.source_kind = 'email_thread'
              AND t.record_id IS NOT DISTINCT FROM (g ->> 'record_id')::uuid
              AND (t.id = ANY (members) OR t.dedupe_key = g ->> 'dedupe_key')
            ORDER BY t.created_at DESC, t.id DESC
            FOR UPDATE
        );
        CONTINUE WHEN cardinality(ids) = 0;
        SELECT * INTO survivor FROM crm_review_items WHERE id = ids[1];
        -- A group of one already on its key changes nothing (idempotent).
        CONTINUE WHEN cardinality(ids) = 1 AND survivor.dedupe_key = g ->> 'dedupe_key'
                      AND survivor.source_key = g ->> 'source_key'
                      AND survivor.source_link ? 'threads';
        groups := groups + 1;

        threads := '[]'::jsonb;
        FOR r IN SELECT * FROM crm_review_items WHERE id = ANY (ids) ORDER BY created_at, id LOOP
            FOR e IN SELECT value FROM jsonb_array_elements(crm_capture_entries(to_jsonb(r))) LOOP
                threads := crm_capture_merge(threads, e);
            END LOOP;
        END LOOP;
        newest := crm_capture_newest(threads);
        -- The member holding the newest entry gives the suggestion, current
        -- values and deal choices (FR-CAP-GRP-06).
        SELECT t.* INTO donor FROM crm_review_items t
        WHERE t.id = ANY (ids)
          AND crm_capture_entries(to_jsonb(t)) @> jsonb_build_array(jsonb_strip_nulls(
                  jsonb_build_object('ref', newest -> 'ref', 'message_id', newest -> 'message_id')))
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT 1;
        IF NOT FOUND THEN
            donor := survivor;
        END IF;
        label := coalesce(nullif(g ->> 'company_label', ''),
                          donor.suggestion ->> 'company_name', 'this company');

        -- The others first, so the survivor can take the key.
        FOR r IN SELECT * FROM crm_review_items WHERE id = ANY (ids) AND id <> survivor.id LOOP
            UPDATE crm_review_items
            SET status = 'expired', decided_at = now(),
                result = jsonb_build_object(
                    'superseded_by', survivor.id,
                    'reason', left('Merged into one suggestion for ' || label || '.', 500))
            WHERE id = r.id;
            INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                                   target_table, target_id, before, after, suggestion_id)
            VALUES ((p_actor ->> 'user_id')::uuid, p_actor ->> 'kind', p_actor ->> 'channel', ws,
                    'review.expired', 'crm_review_items', r.id::text,
                    jsonb_build_object('status', 'open'),
                    jsonb_build_object('status', 'expired', 'superseded_by', survivor.id),
                    r.id);
            gone := gone + 1;
        END LOOP;

        sugg := jsonb_set(donor.suggestion, '{evidence}',
                          crm_capture_evidence(donor.suggestion -> 'evidence', threads));
        UPDATE crm_review_items t
        SET source_key = g ->> 'source_key',
            dedupe_key = g ->> 'dedupe_key',
            source_link = crm_capture_link(t.source_link, threads),
            suggestion = sugg,
            current_values = donor.current_values,
            source_version = newest ->> 'message_id',
            revision = t.revision + 1
        WHERE t.id = survivor.id;
        IF donor.id <> survivor.id THEN
            DELETE FROM crm_review_item_deals WHERE item_id = survivor.id;
            INSERT INTO crm_review_item_deals (workspace_id, item_id, opportunity_id)
            SELECT ws, survivor.id, d.opportunity_id FROM crm_review_item_deals d
            WHERE d.item_id = donor.id
            ON CONFLICT DO NOTHING;
        END IF;
        IF survivor.source_key IS DISTINCT FROM g ->> 'source_key'
           OR survivor.dedupe_key IS DISTINCT FROM g ->> 'dedupe_key' THEN
            rekeyed := rekeyed + 1;
        END IF;
    END LOOP;
    RETURN jsonb_build_object('groups', groups, 'expired', gone, 'rekeyed', rekeyed);
END;
$$;

-- ---------------------------------------------------------------------------
-- 7. crm_review_release — 068's, capture items keep their conversations
-- ---------------------------------------------------------------------------

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
    item     crm_review_items%ROWTYPE;  -- 074
    other    crm_review_items%ROWTYPE;  -- 074
    captured boolean := false;          -- 074
    entries  jsonb;                     -- 074
    kept     jsonb;                     -- 074
    threads  jsonb;                     -- 074
    newest   jsonb;                     -- 074
    take     boolean;                   -- 074
    sugg     jsonb;                     -- 074
    e        jsonb;                     -- 074
BEGIN
    IF ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs a workspace.';
    END IF;
    IF coalesce(p_actor ->> 'kind', '') NOT IN ('user', 'system') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A CRM change needs an actor.';
    END IF;

    IF p_refusal IS NOT NULL THEN
        -- 074: a capture item takes the workspace's capture lock before its
        -- row, the order the 074 functions take them (PD-38).
        SELECT * INTO item FROM crm_review_items WHERE id = p_item_id AND workspace_id = ws;
        IF FOUND AND item.type IN ('new_lead', 'deal_signal')
           AND item.source_kind = 'email_thread'
           AND (item.source_key LIKE 'capture:%' OR item.source_key LIKE 'gmail:%') THEN
            captured := true;
            PERFORM pg_advisory_xact_lock(hashtext('crm_capture:' || ws::text));
        END IF;
        -- 074 end.
        -- 068: a newer open item of the same key replaces this one.
        SELECT dedupe_key INTO item_key
        FROM crm_review_items
        WHERE id = p_item_id AND workspace_id = ws AND status = 'approved'
        FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such suggestion.';
        END IF;
        IF captured THEN  -- 074: as it is now it is locked.
            SELECT * INTO item FROM crm_review_items WHERE id = p_item_id;
            entries := crm_capture_entries(to_jsonb(item));
        END IF;
        IF EXISTS (
            SELECT 1 FROM crm_review_items
            WHERE workspace_id = ws AND dedupe_key = item_key AND status = 'open'
              AND id <> p_item_id
        ) THEN
            -- 074: the refused item's conversations join the open item that
            -- took its key, as the group upsert merges (FR-CAP-GRP-07).
            IF captured THEN
                SELECT * INTO other FROM crm_review_items
                WHERE workspace_id = ws AND dedupe_key = item_key AND status = 'open'
                  AND id <> p_item_id
                FOR UPDATE;
                IF FOUND THEN
                    threads := crm_capture_entries(to_jsonb(other));
                    -- A conversation a third open item holds now stays
                    -- there (a deal document's entry excepted, PD-39).
                    SELECT coalesce(jsonb_agg(x.e ORDER BY x.n), '[]'::jsonb) INTO kept
                    FROM jsonb_array_elements(entries) WITH ORDINALITY AS x(e, n)
                    WHERE coalesce(x.e ->> 'origin', 'discovery') = 'deal_document'
                       OR NOT EXISTS (
                           SELECT 1 FROM crm_review_items o
                           WHERE o.workspace_id = ws AND o.status = 'open'
                             AND o.id <> p_item_id AND o.id <> other.id
                             AND o.type IN ('new_lead', 'deal_signal')
                             AND o.source_kind = 'email_thread'
                             AND ((o.source_link -> 'threads')
                                      @> jsonb_build_array(jsonb_build_object('ref', x.e ->> 'ref'))
                                  OR o.source_key = x.e ->> 'ref'));
                    FOR e IN SELECT value FROM jsonb_array_elements(kept) LOOP
                        threads := crm_capture_merge(threads, e);
                    END LOOP;
                    newest := crm_capture_newest(threads);
                    -- The newest state is the refused item's unless the
                    -- open item already lists it.
                    take := NOT crm_capture_entries(to_jsonb(other)) @> jsonb_build_array(
                                jsonb_strip_nulls(jsonb_build_object(
                                    'ref', newest -> 'ref',
                                    'message_id', newest -> 'message_id')));
                    sugg := CASE WHEN take
                                 THEN crm_capture_person(item.type, item.suggestion, entries, kept)
                                 ELSE other.suggestion END;
                    sugg := jsonb_set(sugg, '{evidence}',
                                      crm_capture_evidence(sugg -> 'evidence', threads));
                    UPDATE crm_review_items t
                    SET source_link = crm_capture_link(t.source_link, threads),
                        suggestion = sugg,
                        current_values = CASE WHEN take THEN item.current_values
                                              ELSE t.current_values END,
                        source_version = newest ->> 'message_id',
                        revision = t.revision + CASE
                            WHEN (t.suggestion, t.source_version, t.source_link)
                                 IS DISTINCT FROM
                                 (sugg, newest ->> 'message_id',
                                  crm_capture_link(t.source_link, threads))
                            THEN 1 ELSE 0 END
                    WHERE t.id = other.id;
                    IF take THEN
                        DELETE FROM crm_review_item_deals WHERE item_id = other.id;
                        INSERT INTO crm_review_item_deals (workspace_id, item_id, opportunity_id)
                        SELECT ws, other.id, d.opportunity_id FROM crm_review_item_deals d
                        WHERE d.item_id = p_item_id
                        ON CONFLICT DO NOTHING;
                    END IF;
                END IF;
            END IF;
            -- 074 end.
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
        -- 074: reopened, it leaves out every conversation another open
        -- capture item holds now (a deal document's entry excepted, PD-39);
        -- left with none, it expires instead, keeping the refusal.
        IF captured THEN
            SELECT coalesce(jsonb_agg(x.e ORDER BY x.n), '[]'::jsonb) INTO kept
            FROM jsonb_array_elements(entries) WITH ORDINALITY AS x(e, n)
            WHERE coalesce(x.e ->> 'origin', 'discovery') = 'deal_document'
               OR NOT EXISTS (
                   SELECT 1 FROM crm_review_items o
                   WHERE o.workspace_id = ws AND o.status = 'open' AND o.id <> p_item_id
                     AND o.type IN ('new_lead', 'deal_signal')
                     AND o.source_kind = 'email_thread'
                     AND ((o.source_link -> 'threads')
                              @> jsonb_build_array(jsonb_build_object('ref', x.e ->> 'ref'))
                          OR o.source_key = x.e ->> 'ref'));
            IF jsonb_array_length(entries) > 0 AND jsonb_array_length(kept) = 0 THEN
                UPDATE crm_review_items
                SET status = 'expired', decided_at = now(), refusal = left(p_refusal, 500),
                    source_link = CASE WHEN source_link ? 'threads'
                                       THEN jsonb_set(source_link, '{threads}', '[]'::jsonb)
                                       ELSE source_link END
                WHERE id = p_item_id AND workspace_id = ws AND status = 'approved';
                INSERT INTO audit_log (actor_id, actor_label, channel, workspace_id, action,
                                       target_table, target_id, before, after, suggestion_id)
                VALUES (who, p_actor ->> 'kind', p_actor ->> 'channel', ws, 'review.expired',
                        'crm_review_items', p_item_id::text,
                        jsonb_build_object('status', 'approved'),
                        jsonb_build_object('status', 'expired'), p_item_id);
                RETURN;
            END IF;
            IF jsonb_array_length(kept) < jsonb_array_length(entries) THEN
                UPDATE crm_review_items t
                SET source_link = crm_capture_link(t.source_link, kept),
                    suggestion = jsonb_set(
                        crm_capture_person(t.type, t.suggestion, entries, kept), '{evidence}',
                        crm_capture_evidence(t.suggestion -> 'evidence', kept)),
                    source_version = crm_capture_newest(kept) ->> 'message_id',
                    revision = t.revision + 1
                WHERE t.id = p_item_id;
            END IF;
        END IF;
        -- 074 end.
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

-- ---------------------------------------------------------------------------
-- 8. The `capture_routing` setting — data-model §9.2a (FR-CAP-ACAD-03)
-- ---------------------------------------------------------------------------

ALTER TABLE crm_workspace_settings DROP CONSTRAINT IF EXISTS crm_workspace_settings_key_check;
ALTER TABLE crm_workspace_settings ADD CONSTRAINT crm_workspace_settings_key_check
    CHECK (key IN ('stage_probabilities', 'flag_thresholds', 'automations', 'email_sync', 'ai',
                   'capture', 'capture_routing'));  -- 074

DO $$
BEGIN
    PERFORM set_config(
        'crm.actor',
        '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}',
        true
    );

    -- A workspace accepts course enquiries from Labs unless its Admin
    -- switches it off (PD-41); read only in the Academy.
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    SELECT w.id, 'capture_routing', '{"accept_routed": true}'::jsonb
    FROM crm_workspaces w
    ON CONFLICT (workspace_id, key) DO NOTHING;
END;
$$;

-- A workspace created later gets the same rows, written as the system: the
-- actor (and action) that created the workspace are put back afterwards.
CREATE OR REPLACE FUNCTION public.crm_workspaces_seed_ai()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    was_actor  text := current_setting('crm.actor', true);
    was_action text := current_setting('crm.action', true);
BEGIN
    PERFORM set_config(
        'crm.actor',
        '{"kind": "system", "user_id": null, "channel": null, "workspace_id": null}',
        true
    );
    PERFORM set_config('crm.action', '', true);
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    VALUES (NEW.id, 'ai',
            '{"budget_usd": 0, "phases": {"1": false, "2": false, "3": false, "4": false}}'::jsonb)
    ON CONFLICT (workspace_id, key) DO NOTHING;
    -- 066 (FR-CRMAI-RULE-01): 037's switches and thresholds, with the four
    -- rule switches off and the six rule parameters.
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    VALUES (NEW.id, 'automations',
            '{"email_records": true, "stale_notice": true, "digests": true,
              "task_reminders": true, "campaign_conversion": true,
              "no_reply_draft": false, "stuck_notice": false, "lead_cold": false,
              "campaign_followthrough": false}'::jsonb)
    ON CONFLICT (workspace_id, key) DO NOTHING;
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    VALUES (NEW.id, 'flag_thresholds',
            '{"reply_owed_red_hours": 24, "reply_owed_amber_hours": 12,
              "silent_red_days": 3, "silent_amber_days": 2, "close_amber_days": 7,
              "stale_red_days": 21, "stale_amber_days": 14,
              "stuck_discovery_days": 14, "stuck_qualification_days": 21,
              "stuck_proposal_days": 14, "stuck_negotiation_days": 21,
              "no_reply_days": 5, "lead_cold_days": 30}'::jsonb)
    ON CONFLICT (workspace_id, key) DO NOTHING;
    -- 072 (010 FR-CAP-DIS-01): email capture's discovery, off.
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    VALUES (NEW.id, 'capture', '{"mode": "off"}'::jsonb)
    ON CONFLICT (workspace_id, key) DO NOTHING;
    -- 074 (010 FR-CAP-ACAD-03): course enquiries from Labs accepted.
    INSERT INTO crm_workspace_settings (workspace_id, key, value)
    VALUES (NEW.id, 'capture_routing', '{"accept_routed": true}'::jsonb)
    ON CONFLICT (workspace_id, key) DO NOTHING;
    PERFORM set_config('crm.actor', coalesce(was_actor, ''), true);
    PERFORM set_config('crm.action', coalesce(was_action, ''), true);
    RETURN NULL;
END;
$$;

-- ---------------------------------------------------------------------------
-- 9. crm_capture_apply_routed — FR-CAP-ACAD-02, FR-CAP-RPT-09 (plan R1.4)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_capture_apply_routed(
    p_actor jsonb, p_run uuid, p_routed jsonb, p_counts jsonb
)
RETURNS boolean
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws        uuid := (p_actor ->> 'workspace_id')::uuid;
    run       crm_capture_dry_runs%ROWTYPE;
    latest_id uuid;
BEGIN
    IF p_actor ->> 'kind' IS DISTINCT FROM 'user' OR ws IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A dry run is applied by a person.';
    END IF;
    -- Checked one step at a time: jsonb_each and jsonb_object_keys raise
    -- on anything but an object.
    IF jsonb_typeof(p_routed) IS DISTINCT FROM 'object'
       OR jsonb_typeof(p_counts) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Malformed routing.';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_each(p_routed) e WHERE jsonb_typeof(e.value) <> 'boolean')
       OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p_counts) AS k)
          IS DISTINCT FROM ARRAY['academy', 'academy_unrouted']
       OR jsonb_typeof(p_counts -> 'academy') <> 'number'
       OR jsonb_typeof(p_counts -> 'academy_unrouted') <> 'number' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Malformed routing.';
    END IF;
    IF (p_counts ->> 'academy')::numeric < 0
       OR (p_counts ->> 'academy_unrouted')::numeric < 0 THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Malformed routing.';
    END IF;
    -- 073 crm_capture_apply's lock and checks, so this and Apply see the
    -- same run.
    PERFORM pg_advisory_xact_lock(hashtext('crm_capture:' || ws::text));
    SELECT * INTO run FROM crm_capture_dry_runs
    WHERE workspace_id = ws AND id = p_run
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such dry run.';
    END IF;
    SELECT r.id INTO latest_id FROM crm_capture_dry_runs r
    WHERE r.workspace_id = ws
    ORDER BY r.started_at DESC, r.id DESC
    LIMIT 1;
    IF run.finished_at IS NULL OR run.cancelled OR run.applied_at IS NOT NULL
       OR latest_id IS DISTINCT FROM run.id
       OR run.finished_at < now() - interval '7 days' THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'This dry run cannot be applied.';
    END IF;

    UPDATE crm_capture_candidates c SET
        finding = c.finding || jsonb_build_object(
            'routed', p_routed -> c.id::text, 'duplicate_company_id', NULL),
        updated_at = now()
    WHERE c.workspace_id = ws AND c.dry_run AND c.status = 'judged'
      AND p_routed ? c.id::text
      AND c.finding ->> 'business' = 'academy';

    UPDATE crm_capture_dry_runs SET
        counts = coalesce(counts, public.crm_capture_report(ws, p_run)) || p_counts
    WHERE workspace_id = ws AND id = p_run;
    RETURN true;
END;
$$;

-- ---------------------------------------------------------------------------
-- Grants — data-model §9.5
-- ---------------------------------------------------------------------------

REVOKE ALL ON FUNCTION public.crm_audit_row() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_entries(jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_newest(jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_merge(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_evidence(jsonb, jsonb)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_link(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_person(text, jsonb, jsonb, jsonb)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_release_thread(jsonb, text, text)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_group_upsert(jsonb, jsonb, jsonb)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_consolidate(jsonb, jsonb)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_review_release(jsonb, uuid, text, jsonb)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_workspaces_seed_ai() FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.crm_capture_apply_routed(jsonb, uuid, jsonb, jsonb)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_audit_row() TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_entries(jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_newest(jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_merge(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_evidence(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_link(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_person(text, jsonb, jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_release_thread(jsonb, text, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_group_upsert(jsonb, jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_consolidate(jsonb, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_review_release(jsonb, uuid, text, jsonb) TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_workspaces_seed_ai() TO service_role;
GRANT EXECUTE ON FUNCTION public.crm_capture_apply_routed(jsonb, uuid, jsonb, jsonb)
    TO service_role;
