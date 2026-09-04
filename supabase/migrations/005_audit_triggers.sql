-- 005_audit_triggers.sql
--
-- NFR-06: the audit log MUST be append-only and MUST NOT be editable from the
-- application.
--
-- "We agreed not to update it" is not an enforcement mechanism. The backend
-- holds the service-role key, which bypasses RLS, so RLS alone cannot stop a
-- bug (or a person with the key) from rewriting history. A trigger runs for
-- every writer including the service role and the table owner, so that is
-- where the guarantee has to live.
--
-- This matters beyond tidiness: §7.5 notes the audit log is a prerequisite for
-- the performance analytics discussed for later versions, and those are only
-- defensible if the underlying record is known to be untampered.
--
-- NOT REVERSIBLE in a meaningful sense: dropping these triggers would silently
-- remove the guarantee that makes the log worth keeping. Remove them only with
-- an explicit decision recorded in the spec.

CREATE OR REPLACE FUNCTION public.audit_log_is_append_only()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    RAISE EXCEPTION
        'audit_log is append-only: % is not permitted (NFR-06)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER audit_log_no_update
    BEFORE UPDATE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION public.audit_log_is_append_only();

CREATE TRIGGER audit_log_no_delete
    BEFORE DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION public.audit_log_is_append_only();

-- TRUNCATE bypasses row-level triggers entirely, so it needs its own
-- statement-level guard.
CREATE TRIGGER audit_log_no_truncate
    BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION public.audit_log_is_append_only();

-- Belt as well as braces: revoke the privileges outright so the error arrives
-- at permission-check time rather than from a trigger, and so tooling that
-- inspects grants reports the table honestly.
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM authenticated, anon, service_role;

-- ============================================================
-- Booking state transitions — FR-APPR-07
--
-- "Every state transition MUST be recorded with actor and timestamp."
--
-- The backend writes richer entries of its own (it knows the intent behind a
-- change, which the database cannot infer). This trigger is the safety net
-- that catches any transition the application forgets to log, so the record is
-- complete even when the code is not.
--
-- The actor is read from the row itself rather than from a session variable:
-- the backend sets decided_by/created_by on every write, and a NULL there
-- means the nightly sweep did it, which is exactly the 'system' case.
-- ============================================================
CREATE OR REPLACE FUNCTION public.log_booking_transition()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_actor uuid;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.status IS NOT DISTINCT FROM OLD.status THEN
        RETURN NEW;  -- not a state transition; nothing to record
    END IF;

    v_actor := COALESCE(NEW.decided_by, NEW.created_by);

    INSERT INTO audit_log (actor_id, actor_label, action, target_table, target_id, before, after)
    VALUES (
        v_actor,
        CASE WHEN v_actor IS NULL THEN 'system' ELSE 'user' END,
        CASE WHEN TG_OP = 'INSERT'
             THEN 'booking.created'
             ELSE 'booking.' || NEW.status
        END,
        'bookings',
        NEW.id::text,
        CASE WHEN TG_OP = 'UPDATE'
             THEN jsonb_build_object('status', OLD.status, 'duration', OLD.duration,
                                     'category', OLD.category)
             ELSE NULL
        END,
        jsonb_build_object('status', NEW.status, 'duration', NEW.duration,
                           'category', NEW.category, 'date', NEW.date,
                           'user_id', NEW.user_id)
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER bookings_audit
    AFTER INSERT OR UPDATE ON bookings
    FOR EACH ROW EXECUTE FUNCTION public.log_booking_transition();

-- Note: `reason` and `decision_note` are deliberately NOT copied into the
-- audit log. NFR-05 limits a reason to the person, their lead and an admin;
-- the audit log is admin-readable and long-lived, so copying a sick-leave
-- reason into it would widen and outlive that access. The log records that a
-- transition happened, not what the person's medical situation was.
