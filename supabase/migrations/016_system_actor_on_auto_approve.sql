-- 016_system_actor_on_auto_approve.sql
--
-- Spec 001 FR-APPR-07, Q-04 — the nightly sweep's auto-approval is recorded
-- with no actor ('system'), not under the person who asked for the leave.
--
-- 005's trigger took COALESCE(decided_by, created_by) for every row. The sweep
-- leaves decided_by NULL on purpose, so the fallback put the requester's id on
-- the 'booking.approved' entry, labelled 'user'. A transition is now
-- attributed to whoever decided it; only a new booking falls back to its
-- creator. Every other update path sets decided_by, so nothing else changes.
--
-- audit_log already allows a NULL actor (001), so no column changes. Rows
-- already written are left as they are: the log is append-only (NFR-06).

CREATE OR REPLACE FUNCTION public.log_booking_transition()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_actor uuid;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.status IS NOT DISTINCT FROM OLD.status THEN
        RETURN NEW;  -- not a state transition; nothing to record
    END IF;

    v_actor := CASE WHEN TG_OP = 'INSERT'
                    THEN COALESCE(NEW.decided_by, NEW.created_by)
                    ELSE NEW.decided_by
               END;

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
