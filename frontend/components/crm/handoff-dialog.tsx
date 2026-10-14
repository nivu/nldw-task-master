"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { confirmCrmHandoff, getCrmHandoff } from "@/lib/api/crm";
import type {
  CrmOpportunity,
  HandoffMilestone,
  HandoffPreview,
  HandoffResult,
  PhaseSpan,
  ProjectCategory,
} from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { formatAmount, type NumberFormat } from "@/lib/crm/format";
import { useAsync } from "@/lib/use-async";

/**
 * The Closed Won handoff — spec 007 FR-CRM-TVA-07 to -10, FR-CRM-FX-05,
 * FR-CRM-AUTO-01 (automation 10); owner decision D8.
 *
 * In Nunnari Labs a deal is won by confirming its TVA project: name,
 * category, lead (an active staff member), delivery dates and optional
 * support and pre phases, pre-filled from the deal and the project's
 * existing phases. Revenue and billing milestones are INR whatever the deal's
 * currency; revenue is pre-filled from the deal's amount at the won-date
 * rate. Only a TVA manager or admin sets them, and only they see the
 * project's existing TVA revenue; anyone else sees them read-only, and the
 * Labs admins get a task to set them in TVA. Cancelling leaves the deal where
 * it is.
 */

const CATEGORIES: { value: ProjectCategory; label: string }[] = [
  { value: "client", label: "Client" },
  { value: "poc", label: "POC" },
  { value: "product", label: "Product" },
];

/** Exact paise from a decimal string, so the running difference never
 *  suffers floating-point error. Display only: the server checks money. */
function paise(value: string): bigint | null {
  const match = /^\s*(\d+)(?:\.(\d{0,2}))?\s*$/.exec(value);
  if (!match) return null;
  return BigInt(match[1]) * BigInt(100) + BigInt((match[2] ?? "").padEnd(2, "0") || "0");
}

function rupees(value: bigint): string {
  const negative = value < BigInt(0);
  const abs = negative ? -value : value;
  const whole = abs / BigInt(100);
  const cents = (abs % BigInt(100)).toString().padStart(2, "0");
  return `${negative ? "-" : ""}${whole}.${cents}`;
}

function SpanInputs({
  id,
  label,
  span,
  onChange,
}: {
  id: string;
  label: string;
  span: PhaseSpan;
  onChange: (span: PhaseSpan) => void;
}) {
  return (
    <fieldset className="grid grid-cols-2 gap-2">
      <legend className="col-span-2 mb-1 text-sm font-medium">{label}</legend>
      <div className="space-y-1">
        <Label htmlFor={`${id}-start`} className="text-xs text-muted-foreground">
          Starts
        </Label>
        <Input
          id={`${id}-start`}
          type="date"
          value={span.starts_on}
          onChange={(e) => onChange({ ...span, starts_on: e.target.value })}
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor={`${id}-end`} className="text-xs text-muted-foreground">
          Ends
        </Label>
        <Input
          id={`${id}-end`}
          type="date"
          value={span.ends_on}
          onChange={(e) => onChange({ ...span, ends_on: e.target.value })}
        />
      </div>
    </fieldset>
  );
}

function HandoffForm({
  workspace,
  deal,
  preview,
  numberFormat,
  onCancel,
  onDone,
}: {
  workspace: string;
  deal: CrmOpportunity;
  preview: HandoffPreview;
  numberFormat: NumberFormat;
  onCancel: () => void;
  onDone: (result: HandoffResult) => void;
}) {
  const empty: PhaseSpan = { starts_on: "", ends_on: "" };
  const [name, setName] = useState(preview.project.name);
  const [category, setCategory] = useState<ProjectCategory>(
    preview.project.category === "internal" ? "client" : preview.project.category
  );
  const [leadId, setLeadId] = useState(preview.project.lead_id ?? "");
  const [delivery, setDelivery] = useState<PhaseSpan>(preview.project.phases.delivery ?? empty);
  const [support, setSupport] = useState<PhaseSpan | null>(preview.project.phases.support ?? null);
  const [pre, setPre] = useState<PhaseSpan | null>(preview.project.phases.pre ?? null);
  const [revenue, setRevenue] = useState(preview.revenue.suggested_inr ?? "");
  const [milestones, setMilestones] = useState<HandoffMilestone[]>(preview.milestones);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const manager = preview.can_set_revenue;

  const total = milestones.reduce<bigint | null>((sum, m) => {
    const p = paise(m.amount);
    return sum === null || p === null ? null : sum + p;
  }, BigInt(0));
  const revenuePaise = paise(revenue);
  const difference = total !== null && revenuePaise !== null ? revenuePaise - total : null;

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      const result = await confirmCrmHandoff(workspace, deal.id, {
        project_name: name.trim(),
        category,
        lead_id: leadId,
        phases: {
          delivery,
          ...(support ? { support } : {}),
          ...(pre ? { pre } : {}),
        },
        ...(manager && revenue.trim() ? { revenue: revenue.trim() } : {}),
        ...(manager && milestones.length ? { milestones } : {}),
        expect_updated_at: deal.updated_at,
      });
      onDone(result);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      id="handoff-form"
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        confirm();
      }}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="handoff-name">Project name</Label>
          <Input
            id="handoff-name"
            required
            maxLength={200}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="handoff-category">Category</Label>
          <select
            id="handoff-category"
            className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm"
            value={category}
            onChange={(e) => setCategory(e.target.value as ProjectCategory)}
          >
            {CATEGORIES.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="handoff-lead">Project lead</Label>
          <select
            id="handoff-lead"
            required
            className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm"
            value={leadId}
            onChange={(e) => setLeadId(e.target.value)}
          >
            <option value="">Choose a staff member…</option>
            {preview.leads.map((lead) => (
              <option key={lead.id} value={lead.id}>
                {lead.display_name}
              </option>
            ))}
          </select>
        </div>
      </div>

      <SpanInputs id="handoff-delivery" label="Delivery phase" span={delivery} onChange={setDelivery} />
      {support ? (
        <div className="space-y-1">
          <SpanInputs id="handoff-support" label="Support phase" span={support} onChange={setSupport} />
          <Button type="button" variant="ghost" size="sm" onClick={() => setSupport(null)}>
            Remove support phase
          </Button>
        </div>
      ) : (
        <Button type="button" variant="outline" size="sm" onClick={() => setSupport(empty)}>
          Add a support phase
        </Button>
      )}
      {preview.offer_pre_phase &&
        (pre ? (
          <div className="space-y-1">
            <SpanInputs id="handoff-pre" label="Pre-project (discovery) phase" span={pre} onChange={setPre} />
            <Button type="button" variant="ghost" size="sm" onClick={() => setPre(null)}>
              Remove pre phase
            </Button>
          </div>
        ) : (
          <Button type="button" variant="outline" size="sm" className="ml-2" onClick={() => setPre(empty)}>
            Add a pre-project phase
          </Button>
        ))}

      <section className="space-y-2 border-t pt-3">
        <div className="space-y-1.5">
          <Label htmlFor="handoff-revenue">Revenue (INR)</Label>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              id="handoff-revenue"
              inputMode="decimal"
              className="w-48"
              value={revenue}
              readOnly={!manager}
              onChange={(e) => setRevenue(e.target.value)}
            />
            {preview.revenue.won_fx_rate && deal.currency && deal.currency !== "INR" && (
              <span className="text-xs text-muted-foreground">
                at {preview.revenue.won_fx_rate} INR per {deal.currency} ({preview.revenue.won_fx_month?.slice(0, 7)})
              </span>
            )}
            {preview.revenue.existing_tva_revenue && (
              <span className="text-xs text-muted-foreground">
                TVA has {formatAmount(preview.revenue.existing_tva_revenue, "INR", numberFormat)} now
              </span>
            )}
          </div>
          {preview.revenue.message && <p className="text-xs text-destructive">{preview.revenue.message}</p>}
        </div>
        {!manager && (
          <p className="text-xs text-muted-foreground">
            Only a TVA manager or admin sets revenue and milestones. The project is confirmed without them, and
            the Labs admins get a task to set them in TVA Projects.
          </p>
        )}

        <div className="space-y-2">
          <p className="text-sm font-medium">Billing milestones (INR)</p>
          {milestones.map((m, i) => (
            <div key={i} className="grid grid-cols-[1fr_9rem_8rem_auto] items-center gap-2">
              <Input
                aria-label={`Milestone ${i + 1} name`}
                value={m.name}
                readOnly={!manager}
                onChange={(e) =>
                  setMilestones((all) => all.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))
                }
              />
              <Input
                aria-label={`Milestone ${i + 1} due date`}
                type="date"
                value={m.due_on}
                readOnly={!manager}
                onChange={(e) =>
                  setMilestones((all) => all.map((x, j) => (j === i ? { ...x, due_on: e.target.value } : x)))
                }
              />
              <Input
                aria-label={`Milestone ${i + 1} amount (INR)`}
                inputMode="decimal"
                value={m.amount}
                readOnly={!manager}
                onChange={(e) =>
                  setMilestones((all) => all.map((x, j) => (j === i ? { ...x, amount: e.target.value } : x)))
                }
              />
              {manager && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setMilestones((all) => all.filter((_, j) => j !== i))}
                >
                  Remove
                </Button>
              )}
            </div>
          ))}
          {manager && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setMilestones((all) => [...all, { name: "", due_on: "", amount: "" }])}
            >
              Add a milestone
            </Button>
          )}
          {manager && milestones.length > 0 && difference !== null && (
            <p className={`text-xs ${difference === BigInt(0) ? "text-muted-foreground" : "text-destructive"}`}>
              {difference === BigInt(0)
                ? "Milestones add up to the revenue."
                : `Milestones differ from the revenue by ${formatAmount(rupees(difference < BigInt(0) ? -difference : difference), "INR", numberFormat)} (${difference > BigInt(0) ? "under" : "over"}).`}
            </p>
          )}
        </div>
      </section>

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={busy || !leadId || !delivery.starts_on || !delivery.ends_on}>
          {busy ? "Confirming…" : "Confirm handoff and mark won"}
        </Button>
      </DialogFooter>
    </form>
  );
}

export function HandoffDialog({
  open,
  onOpenChange,
  workspace,
  deal,
  numberFormat,
  onDone,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspace: string;
  deal: CrmOpportunity;
  numberFormat: NumberFormat;
  onDone: (result: HandoffResult) => void;
}) {
  const { data: preview, error } = useAsync<HandoffPreview | null>(
    () => (open ? getCrmHandoff(workspace, deal.id) : Promise.resolve(null)),
    [open, workspace, deal.id, deal.updated_at]
  );
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Hand off {deal.name}</DialogTitle>
          <DialogDescription>
            The deal is Closed Won when its TVA project is confirmed with a lead and delivery dates.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
            {error}
          </p>
        )}
        {!preview && !error && <p className="text-sm text-muted-foreground">Loading…</p>}
        {preview && (
          <HandoffForm
            key={deal.updated_at}
            workspace={workspace}
            deal={deal}
            preview={preview}
            numberFormat={numberFormat}
            onCancel={() => onOpenChange(false)}
            onDone={onDone}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
