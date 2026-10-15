-- 040_crm_mailbox_owner_left.sql
--
-- Spec 007 (sales CRM) FR-CRM-SYNC-01, FR-CRM-ACC-12; data-model §5.3;
-- plan §5.11.
--
-- A mailbox is read only while its owner is a member of its workspace
-- (FR-CRM-SYNC-01). That was checked when a mailbox was added or changed,
-- but removing the owner's membership left the mailbox active, so the sync
-- kept reading the person's Gmail into a workspace they had left.
--
-- 1. `crm_remove_membership()` replaced (037's, plus one statement): the
--    removed person's mailboxes in the workspace are paused in the same
--    transaction, audited as `mailbox.updated` under the actor already set.
--    A CRM-only account deactivated because it has no workspace left
--    therefore has no active mailbox either. The sync also skips any
--    mailbox whose owner is not an active member (services/crm/email_sync.py),
--    which covers a TVA account deactivated by other means.
--
-- EXECUTE stays revoked from PUBLIC, anon and authenticated (data-model
-- §5.12).
--
-- Reversible: restore 037's crm_remove_membership(); mailboxes paused by it
-- stay paused until an admin resumes them.

-- ---------------------------------------------------------------------------
-- 1. crm_remove_membership — data-model §5.3 (037's, plus the mailboxes)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.crm_remove_membership(p_actor jsonb, p_membership_id uuid)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    ws          uuid := (p_actor ->> 'workspace_id')::uuid;
    who         uuid := (p_actor ->> 'user_id')::uuid;
    m           crm_memberships%ROWTYPE;
    person      profiles%ROWTYPE;
    tokens_n    integer := 0;
    oauth_n     integer := 0;
    deactivated boolean := false;
BEGIN
    PERFORM set_config('crm.actor', p_actor::text, true);

    -- Admins first, in id order, then the row: the same order crm_apply
    -- takes, so concurrent removals and demotions queue rather than deadlock.
    PERFORM 1 FROM crm_memberships
    WHERE workspace_id = ws AND role = 'admin' ORDER BY id FOR UPDATE;
    SELECT * INTO m FROM crm_memberships
    WHERE id = p_membership_id AND workspace_id = ws FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such member in this workspace.';
    END IF;
    IF m.role = 'admin' AND NOT EXISTS (
        SELECT 1 FROM crm_memberships
        WHERE workspace_id = ws AND role = 'admin' AND id <> m.id
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'A workspace needs at least one admin.';
    END IF;

    SELECT * INTO person FROM profiles WHERE id = m.user_id FOR UPDATE;
    IF (person.is_owner OR (person.role = 'admin' AND person.account_type = 'staff'))
       AND NOT crm_is_tva_admin(who) THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22',
            MESSAGE = 'Ask a TVA admin to change this membership.';
    END IF;

    -- 037: what the bell told them about this workspace goes with them
    -- (FR-CRM-WS-02). Notifications are a log, so this writes no audit row.
    DELETE FROM crm_notifications WHERE workspace_id = ws AND user_id = m.user_id;

    PERFORM set_config('crm.action', 'membership.removed', true);
    DELETE FROM crm_memberships WHERE id = m.id;
    PERFORM set_config('crm.action', '', true);

    -- 040: the sync stops reading their Gmail for this workspace
    -- (FR-CRM-SYNC-01: a mailbox's owner is a member). Audited by
    -- crm_audit_row as mailbox.updated under the same actor.
    UPDATE crm_mailboxes SET is_active = false, updated_by = who
    WHERE workspace_id = ws AND owner_id = m.user_id AND is_active;

    -- FR-CRM-ACC-12, Q-44: a CRM-only person with no workspace left has no
    -- reason to sign in. Staff keep their TVA account.
    IF person.account_type = 'crm_only' AND person.is_active
       AND NOT EXISTS (SELECT 1 FROM crm_memberships WHERE user_id = person.id) THEN
        UPDATE profiles
        SET is_active = false, deactivated_reason = 'crm_no_membership'
        WHERE id = person.id;
        UPDATE api_tokens SET revoked_at = now()
        WHERE user_id = person.id AND revoked_at IS NULL;
        GET DIAGNOSTICS tokens_n = ROW_COUNT;
        UPDATE oauth_refresh_tokens SET revoked_at = now()
        WHERE user_id = person.id AND revoked_at IS NULL;
        GET DIAGNOSTICS oauth_n = ROW_COUNT;
        deactivated := true;

        -- TVA rows (no workspace): the account and its tokens are TVA's. The
        -- admin who removed the membership did this themselves, so the rows
        -- carry their own kind: a `system` row would read "(via CRM deal)".
        INSERT INTO audit_log (actor_id, actor_label, channel, action, target_table,
                               target_id, before, after)
        VALUES (who, coalesce(p_actor ->> 'kind', 'user'), p_actor ->> 'channel',
                'user.deactivated', 'profiles',
                person.id::text, jsonb_build_object('is_active', true),
                jsonb_build_object('is_active', false,
                                   'deactivated_reason', 'crm_no_membership'));
        IF tokens_n + oauth_n > 0 THEN
            INSERT INTO audit_log (actor_id, actor_label, channel, action, target_table,
                                   target_id, after)
            VALUES (who, coalesce(p_actor ->> 'kind', 'user'), p_actor ->> 'channel',
                    'token.revoked', 'api_tokens',
                    person.id::text,
                    jsonb_build_object('personal_tokens', tokens_n, 'oauth_tokens', oauth_n));
        END IF;
    END IF;

    RETURN jsonb_build_object('deactivated', deactivated);
END;
$$;

REVOKE ALL ON FUNCTION public.crm_remove_membership(jsonb, uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_remove_membership(jsonb, uuid) TO service_role;
