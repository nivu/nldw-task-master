-- 045_crm_convert_pending.sql
--
-- Spec 007 (sales CRM) FR-CRM-CHG-02, FR-CRM-ACC-14; data-model §5.15. Found
-- by the final whole-branch review.
--
-- A CRM-only account is left out of every TVA approval queue. Converting a
-- staff member while their leave or a comp-off claim waits for a decision
-- would leave it there, undecided, for good. crm_convert_account (027) now
-- refuses staff -> crm_only while the person has a booking or a comp-off
-- claim in `pending`, with CRM22 and a sentence safe to show; the backend
-- answers the same first (api/crm/tva_admin.py). Nothing else changes: this
-- is 027's definition with that one check added, and the same grants.
--
-- Reversible: re-run 027's CREATE OR REPLACE FUNCTION crm_convert_account and
-- its REVOKE / GRANT.

CREATE OR REPLACE FUNCTION public.crm_convert_account(p_actor jsonb, p_user_id uuid, p_to text)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    who      uuid := (p_actor ->> 'user_id')::uuid;
    person   profiles%ROWTYPE;
    after_p  profiles%ROWTYPE;
    holds    boolean;
BEGIN
    IF p_to IS NULL OR p_to NOT IN ('staff', 'crm_only') THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM22', MESSAGE = 'Unknown account type.';
    END IF;
    -- The lock tva_staff_only() waits for (025): an allocation, project lead
    -- or reporting line written meanwhile either committed before this point
    -- and is seen below, or waits and is then refused by that trigger.
    SELECT * INTO person FROM profiles WHERE id = p_user_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'CRM04', MESSAGE = 'No such person.';
    END IF;

    IF p_to = 'crm_only' AND person.account_type <> 'crm_only' THEN
        IF person.is_owner THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'The owner''s account cannot be CRM-only.';
        END IF;
        IF EXISTS (SELECT 1 FROM profiles WHERE lead_id = person.id) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'This person is someone''s lead; give their reports another lead first.';
        END IF;
        IF EXISTS (SELECT 1 FROM projects WHERE lead_id = person.id) THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'This person leads a project; give it another lead first.';
        END IF;
        -- Released allocations (028) do not count. The column is looked up so
        -- this function stays right before and after 028.
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM allocations WHERE user_id = $1'
                || CASE WHEN EXISTS (
                       SELECT 1 FROM pg_attribute
                       WHERE attrelid = 'public.allocations'::regclass
                         AND attname = 'released_at' AND NOT attisdropped)
                   THEN ' AND released_at IS NULL' ELSE '' END
                || ')'
        INTO holds USING person.id;
        IF holds THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'This person is allocated to a project; remove the allocation first.';
        END IF;
        -- 045: a CRM-only account leaves every approval queue, so leave or a
        -- comp-off claim still waiting would never be decided.
        IF EXISTS (SELECT 1 FROM bookings WHERE user_id = person.id AND status = 'pending') THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'This person has leave waiting for a decision; decide or withdraw it first.';
        END IF;
        IF EXISTS (SELECT 1 FROM compoff_credits
                   WHERE user_id = person.id AND status = 'pending') THEN
            RAISE EXCEPTION USING ERRCODE = 'CRM22',
                MESSAGE = 'This person has a comp-off claim waiting for a decision; decide it first.';
        END IF;
        UPDATE profiles
        SET account_type = 'crm_only', role = 'user', lead_id = NULL, logs_time = false,
            dashboard_access = false
        WHERE id = person.id
        RETURNING * INTO after_p;
    ELSIF p_to = 'staff' AND person.account_type <> 'staff' THEN
        UPDATE profiles SET account_type = 'staff' WHERE id = person.id
        RETURNING * INTO after_p;
    ELSE
        RETURN jsonb_build_object('id', person.id, 'account_type', person.account_type,
                                  'changed', false);
    END IF;

    INSERT INTO audit_log (actor_id, actor_label, channel, action, target_table, target_id,
                           before, after)
    VALUES (who, coalesce(p_actor ->> 'kind', 'user'), p_actor ->> 'channel',
            'user.account_type', 'profiles', person.id::text,
            jsonb_build_object('account_type', person.account_type, 'role', person.role,
                               'lead_id', person.lead_id, 'logs_time', person.logs_time,
                               'dashboard_access', person.dashboard_access),
            jsonb_build_object('account_type', after_p.account_type, 'role', after_p.role,
                               'lead_id', after_p.lead_id, 'logs_time', after_p.logs_time,
                               'dashboard_access', after_p.dashboard_access));

    RETURN jsonb_build_object('id', after_p.id, 'account_type', after_p.account_type,
                              'role', after_p.role, 'changed', true);
END;
$$;

REVOKE ALL ON FUNCTION public.crm_convert_account(jsonb, uuid, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_convert_account(jsonb, uuid, text) TO service_role;
