-- 044_crm_notification_refs.sql
--
-- Spec 007 (sales CRM) FR-CRM-WS-02, FR-CRM-NOTIF-01; data-model §2
-- ("References inside the workspace"), §3.19, §5.14. Found by S12's
-- security sweep (backend/tests/db/test_crm_security_db.py).
--
-- A notification names the record it opens with the polymorphic pair
-- `record_type` / `record_id`, which cannot be a foreign key. Every other
-- such pair in the CRM (Drive, task, note and email links, favourites) is
-- held to its own workspace by `crm_check_refs()` (027); 037 left the bell's
-- out. This adds the same trigger, so a notification can never name a
-- record of another workspace, whoever writes it.
--
-- It fires on insert, and on an update of the columns it checks only: a
-- notification of a record purged since must still be markable read.
-- A notification with no record (a job failure, an invitation) is not
-- checked, as `crm_check_refs()` skips a NULL `record_id`.
--
-- Reversible: DROP TRIGGER crm_notifications_check_refs ON crm_notifications.

DROP TRIGGER IF EXISTS crm_notifications_check_refs ON crm_notifications;
CREATE TRIGGER crm_notifications_check_refs
    BEFORE INSERT OR UPDATE OF workspace_id, record_type, record_id ON crm_notifications
    FOR EACH ROW EXECUTE FUNCTION public.crm_check_refs();
