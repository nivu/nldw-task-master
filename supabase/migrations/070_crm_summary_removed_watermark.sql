-- 070_crm_summary_removed_watermark.sql
--
-- Spec 009 (CRM intelligence layer) FR-CRMAI-SUM-02; data-model §3.3
-- (amended). A fix to 062: "a summary outlives the message it was built
-- from".
--
-- A thread is due a summary when its latest qualifying message is newer
-- than the summary's watermark (`crm_pending_threads`, 062). 062's
-- `crm_remove_emails` deleted a summary only when its thread was left with
-- no stored message. When only the watermark message was removed (deleted
-- in Gmail, or its label removed, FR-CRM-SYNC-09) and older messages
-- stayed, the thread's latest message was older than the watermark, so the
-- thread was never due again, and the summary, its asks and its
-- commitments kept describing a message no longer stored.
--
-- 1. `crm_remove_emails()` (062's) replaced: a summary whose watermark
--    message is among the removed messages is deleted too (its links go
--    with it by CASCADE). The thread, if it still has qualifying messages,
--    is then due a summary again and is summarised from what is stored.
--    Only the lines marked "070" are new.
--
-- Grants as 062's: EXECUTE revoked from PUBLIC, anon and authenticated and
-- granted to service_role (data-model §5).
--
-- Reversible: re-run 062's `crm_remove_emails`, with its grants.

CREATE OR REPLACE FUNCTION public.crm_remove_emails(
    p_workspace uuid, p_mailbox uuid, p_ids text[]
)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path = public, extensions
AS $$
DECLARE
    records jsonb;
    removed integer;
    -- 062: the threads the removed messages were on.
    threads text[];
BEGIN
    SELECT coalesce(jsonb_agg(DISTINCT jsonb_build_object(
               'record_type', l.record_type, 'record_id', l.record_id)), '[]')
    INTO records
    FROM crm_email_links l
    JOIN crm_email_messages m ON m.workspace_id = l.workspace_id AND m.id = l.message_id
    WHERE m.workspace_id = p_workspace AND m.mailbox_id = p_mailbox
      AND m.gmail_message_id = ANY (coalesce(p_ids, '{}'));

    SELECT coalesce(array_agg(DISTINCT m.thread_id), '{}')
    INTO threads
    FROM crm_email_messages m
    WHERE m.workspace_id = p_workspace AND m.mailbox_id = p_mailbox
      AND m.gmail_message_id = ANY (coalesce(p_ids, '{}'));

    DELETE FROM crm_email_messages
    WHERE workspace_id = p_workspace AND mailbox_id = p_mailbox
      AND gmail_message_id = ANY (coalesce(p_ids, '{}'));
    GET DIAGNOSTICS removed = ROW_COUNT;

    -- 062 (009 FR-CRMAI-SUM-02): a thread left with no stored message loses
    -- its summary (its links and nothing else go with it, by CASCADE).
    -- 070: so does a summary whose watermark message was removed; its
    -- thread is summarised again from the messages still stored.
    DELETE FROM crm_email_summaries s
    WHERE s.workspace_id = p_workspace AND s.mailbox_id = p_mailbox
      AND s.thread_id = ANY (threads)
      AND (
          s.watermark_message_id = ANY (coalesce(p_ids, '{}'))  -- 070
          OR NOT EXISTS (
              SELECT 1 FROM crm_email_messages m
              WHERE m.workspace_id = p_workspace AND m.mailbox_id = p_mailbox
                AND m.thread_id = s.thread_id
          )
      );

    RETURN jsonb_build_object('removed', removed, 'records', records);
END;
$$;

REVOKE ALL ON FUNCTION public.crm_remove_emails(uuid, uuid, text[])
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crm_remove_emails(uuid, uuid, text[]) TO service_role;
