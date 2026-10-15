"use client";

import { useState } from "react";

import { FieldEditor, type Money } from "@/components/crm/field-editor";
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
import { convertCampaignMember, searchDealChoices } from "@/lib/api/crm";
import type { Converted, CrmCampaignMember } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";

/**
 * Convert a campaign member to a deal — spec 007 FR-CRM-CAMP-04,
 * FR-CRM-AUTO-01 (automation 8); journey 4.9.
 *
 * Moving a member to Converted asks for the deal: a new one (opened by the
 * same rules as any deal: its company at Qualified or later, a next step
 * and its date) or an existing one. The member then carries the deal, and
 * the deal records the campaign as its source. The company and point of
 * contact of a new deal are the member's own unless the server is told
 * otherwise.
 */
export function ConvertDialog({
  open,
  onOpenChange,
  workspace,
  member,
  canSeeAmounts,
  onConverted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspace: string;
  member: CrmCampaignMember | null;
  /** A Viewer never converts, but the amount is shown only to whoever may see it. */
  canSeeAmounts: boolean;
  onConverted: (done: Converted) => void;
}) {
  const [mode, setMode] = useState<"new" | "existing">("new");
  const [name, setName] = useState("");
  const [nextStep, setNextStep] = useState("");
  const [nextStepOn, setNextStepOn] = useState("");
  const [money, setMoney] = useState<Money>({ amount: null, currency: "INR" });
  const [deal, setDeal] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function close(next: boolean) {
    if (!next) {
      setMode("new");
      setName("");
      setNextStep("");
      setNextStepOn("");
      setMoney({ amount: null, currency: "INR" });
      setDeal(null);
      setError(null);
    }
    onOpenChange(next);
  }

  async function convert() {
    if (!member) return;
    setBusy(true);
    setError(null);
    try {
      const done = await convertCampaignMember(
        workspace,
        member.id,
        mode === "existing" && deal
          ? { opportunity_id: deal }
          : {
              new: {
                name: name.trim(),
                next_step: nextStep.trim(),
                next_step_on: nextStepOn,
                ...(money.amount ? { amount: money.amount, currency: money.currency } : {}),
              },
            }
      );
      onConverted(done);
      close(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const ready =
    mode === "existing" ? Boolean(deal) : Boolean(name.trim() && nextStep.trim() && nextStepOn);
  const who = member?.name ?? "this member";

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Convert {who}</DialogTitle>
          <DialogDescription>
            The member becomes Converted and the deal records this campaign as its source.
          </DialogDescription>
        </DialogHeader>
        <form
          id="convert-form"
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            convert();
          }}
        >
          {error && (
            <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
              {error}
            </p>
          )}
          <fieldset className="flex gap-4 text-sm">
            <legend className="sr-only">Which deal</legend>
            {(
              [
                ["new", "A new deal"],
                ["existing", "An existing deal"],
              ] as const
            ).map(([value, label]) => (
              <label key={value} className="flex items-center gap-1.5">
                <input
                  type="radio"
                  name="convert-mode"
                  value={value}
                  checked={mode === value}
                  onChange={() => setMode(value)}
                />
                {label}
              </label>
            ))}
          </fieldset>
          {mode === "new" ? (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="convert-name">Deal name</Label>
                <Input
                  id="convert-name"
                  maxLength={200}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                />
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="convert-next-step">Next step</Label>
                  <Input
                    id="convert-next-step"
                    maxLength={500}
                    value={nextStep}
                    onChange={(e) => setNextStep(e.target.value)}
                    required
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="convert-next-step-on">Next step date</Label>
                  <Input
                    id="convert-next-step-on"
                    type="date"
                    value={nextStepOn}
                    onChange={(e) => setNextStepOn(e.target.value)}
                    required
                  />
                </div>
              </div>
              {canSeeAmounts && (
                <div className="space-y-1.5">
                  <Label htmlFor="convert-amount">Amount (optional)</Label>
                  <FieldEditor
                    id="convert-amount"
                    spec={{ name: "amount", label: "Amount", type: "currency" }}
                    value={money}
                    onChange={(value) => setMoney((value as Money | null) ?? { amount: null, currency: "INR" })}
                  />
                </div>
              )}
              <p className="text-xs text-muted-foreground">
                The deal is with {member?.company_name ?? member?.person_company_name ?? "the member's company"}
                {member?.person_name ? `, ${member.person_name} its point of contact` : ""}.
              </p>
            </>
          ) : (
            <div className="space-y-1.5">
              <Label htmlFor="convert-deal">Deal</Label>
              <FieldEditor
                id="convert-deal"
                spec={{
                  name: "opportunity_id",
                  label: "Deal",
                  type: "relation",
                  choices: [],
                  search: (q, after) => searchDealChoices(workspace, q, after),
                }}
                value={deal}
                onChange={(value) => setDeal((value as string | null) ?? null)}
              />
            </div>
          )}
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => close(false)}>
            Cancel
          </Button>
          <Button type="submit" form="convert-form" disabled={busy || !ready}>
            {busy ? "Converting…" : "Convert"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
