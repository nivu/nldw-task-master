-- 021_invoicing.sql
--
-- Spec 006 FR-MILE-05..08 — the client invoicing tracker. A milestone already
-- carries when it was invoiced (012); it now carries the invoice's number and
-- when the client paid it, so money owed can be told apart from money banked.
--
-- A milestone cannot be paid before it is invoiced: paid_on needs invoiced_on.
-- No column grant to `authenticated`: project_milestones is backend-only (012).

ALTER TABLE project_milestones ADD COLUMN IF NOT EXISTS invoice_number text;
ALTER TABLE project_milestones ADD COLUMN IF NOT EXISTS paid_on date;

ALTER TABLE project_milestones DROP CONSTRAINT IF EXISTS project_milestones_paid_after_invoiced;
ALTER TABLE project_milestones
    ADD CONSTRAINT project_milestones_paid_after_invoiced
        CHECK (paid_on IS NULL OR invoiced_on IS NOT NULL);

-- Days a client has to pay an invoice. An invoice unpaid this many days after
-- its invoice date is overdue.
INSERT INTO app_settings (key, value, description) VALUES
    (
        'invoice_payment_terms_days',
        '30',
        'Spec 006 FR-MILE-06. Days a client has to pay an invoice; unpaid after '
        'invoiced_on plus this many days, it is payment overdue.'
    )
ON CONFLICT (key) DO NOTHING;
