"use client";

import { useState } from "react";

import { AmountField } from "@/components/crm/amount-field";
import { FieldEditor } from "@/components/crm/field-editor";
import { HandoffDialog } from "@/components/crm/handoff-dialog";
import { NameClashDialog } from "@/components/crm/name-clash-dialog";
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
import { moveCrmStage, previewCrmStage, searchPersonChoices, type ProjectChoice } from "@/lib/api/crm";
import type {
  CrmCurrency,
  CrmOpportunity,
  StageChanges,
  StageKey,
  StageMoveResult,
  StagePreview,
} from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import type { NumberFormat } from "@/lib/crm/format";
import { useAsync } from "@/lib/use-async";

/**
 * Move a deal — spec 007 FR-CRM-PIPE-07 to -14, contracts §2.6.
 *
 * The dialog asks the server first (`…/stage/preview`, which writes nothing)
 * and then asks the person for exactly the fields the move lacks, nothing
 * more. What the move will do — the probability, the company's lead stage —
 * is said before it happens. A refusal is the server's one sentence, shown
 * verbatim. Closed Lost has its own dialog (`lost-dialog.tsx`).
 *
 * In Nunnari Labs (FR-CRM-TVA) the preview also says what happens to the
 * deal's TVA project, and a project name clash at Proposal is resolved in
 * `name-clash-dialog.tsx` (another name, or a link by a TVA manager). Closed
 * Won in Labs is the handoff (`handoff-dialog.tsx`), not a plain move.
 */

export const STAGE_ORDER: StageKey[] = [
  "discovery",
  "qualification",
  "proposal",
  "negotiation",
  "closed_won",
  "closed_lost",
];

/** The fields this dialog can ask for, in the order a refusal names them. */
const ASKABLE = [
  "point_of_contact_id",
  "money",
  "expected_close_on",
  "proposal_sent_on",
  "next_step",
  "next_step_on",
] as const;

const LABELS: Record<string, string> = {
  point_of_contact_id: "Point of contact",
  money: "Amount",
  expected_close_on: "Expected close date",
  proposal_sent_on: "Proposal sent on",
  next_step: "Next step",
  next_step_on: "Next step date",
};

function asked(missing: StagePreview["missing"]): (typeof ASKABLE)[number][] {
  const wanted = new Set(missing.map((m) => (m === "amount" || m === "currency" ? "money" : m)));
  return ASKABLE.filter((f) => wanted.has(f));
}

/** FR-CRM-TVA-04 to -14 — the move's effect on the deal's TVA project. */
function projectSentence(project: NonNullable<StagePreview["would"]["project"]>): string | null {
  switch (project.action) {
    case "create":
      return project.clash
        ? null
        : `A tentative TVA project “${project.name}” will be created at ${project.probability}%.`;
    case "link":
      return `The deal will be linked to the TVA project “${project.name}”.`;
    case "sync_probability":
      return `The TVA project “${project.name}” follows to ${project.probability}%.`;
    case "archive_release":
      return `The TVA project “${project.name}” will be archived and its people released.`;
    case "unarchive_unrelease":
      return `The TVA project “${project.name}” comes back, with the people its loss released.`;
    case "retentative":
      return `The TVA project “${project.name}” goes back to tentative; its revenue and milestones are kept.`;
  }
}

/** A one-line account of what the move does besides the stage. */
export function WouldLine({ would, companyName }: { would: StagePreview["would"]; companyName: string | null }) {
  const parts: string[] = [];
  if (would.probability !== undefined) parts.push(`Probability becomes ${would.probability}%.`);
  if (would.company_lead_stage_label && companyName)
    parts.push(`${companyName} will be at ${would.company_lead_stage_label}.`);
  if (would.tasks_closed)
    parts.push(
      `${would.tasks_closed} open task${would.tasks_closed === 1 ? "" : "s"} will be closed with a note.`
    );
  const project = would.project ? projectSentence(would.project) : null;
  if (project) parts.push(project);
  if (parts.length === 0) return null;
  return <p className="text-xs text-muted-foreground">{parts.join(" ")}</p>;
}

export function StageDialog(props: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspace: string;
  deal: CrmOpportunity;
  stage: StageKey | null;
  stageLabel: string;
  numberFormat: NumberFormat;
  /** The workspace is connected to TVA projects (Nunnari Labs). */
  labs?: boolean;
  onMoved: (result: StageMoveResult) => void;
}) {
  if (props.labs && props.stage === "closed_won") {
    return (
      <HandoffDialog
        open={props.open}
        onOpenChange={props.onOpenChange}
        workspace={props.workspace}
        deal={props.deal}
        numberFormat={props.numberFormat}
        onDone={(result) => {
          props.onMoved(result);
          props.onOpenChange(false);
        }}
      />
    );
  }
  return <MoveDialog {...props} />;
}

function MoveDialog({
  open,
  onOpenChange,
  workspace,
  deal,
  stage,
  stageLabel,
  numberFormat,
  onMoved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspace: string;
  deal: CrmOpportunity;
  stage: StageKey | null;
  stageLabel: string;
  numberFormat: NumberFormat;
  labs?: boolean;
  onMoved: (result: StageMoveResult) => void;
}) {
  const [changes, setChanges] = useState<StageChanges>({});
  const [choice, setChoice] = useState<ProjectChoice>({});
  const [resolving, setResolving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: preview, error: previewError } = useAsync<StagePreview | null>(
    () =>
      open && stage
        ? previewCrmStage(workspace, deal.id, stage, {}, undefined, {
            ...(choice.project_name ? { project_name: choice.project_name } : {}),
          })
        : Promise.resolve(null),
    [open, stage, workspace, deal.id, deal.updated_at, choice.project_name]
  );
  const fields = preview ? asked(preview.missing) : [];
  // FR-CRM-TVA-05: a name clash the person resolves here. A link chosen
  // settles it; another name is previewed again.
  const clash = choice.link_project_id ? null : (preview?.would.project?.clash ?? null);
  // A refusal no field here can fix (who may reopen, a reopen-won the
  // project's history refuses): say it and offer nothing to fill in.
  const blocked = preview !== null && !preview.allowed && fields.length === 0 && !clash && !choice.link_project_id;

  function close(next: boolean) {
    if (!next) {
      setChanges({});
      setChoice({});
      setError(null);
    }
    onOpenChange(next);
  }

  function set(patch: StageChanges) {
    setChanges((current) => ({ ...current, ...patch }));
  }

  async function move() {
    if (!stage) return;
    setBusy(true);
    setError(null);
    try {
      const result = await moveCrmStage(workspace, deal.id, stage, changes, deal.updated_at, undefined, choice);
      onMoved(result);
      close(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Move to {stageLabel}</DialogTitle>
          <DialogDescription>
            {fields.length > 0
              ? "This stage needs a few more details."
              : `${deal.name} moves from ${deal.stage_label} to ${stageLabel}.`}
          </DialogDescription>
        </DialogHeader>

        {!preview && !previewError && <p className="text-sm text-muted-foreground">Checking…</p>}
        {(previewError || blocked) && (
          <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
            {previewError ?? preview?.message}
          </p>
        )}

        {preview && !blocked && (
          <form
            id="stage-form"
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              move();
            }}
          >
            {fields.map((field) => (
              <div key={field} className="space-y-1.5">
                <Label htmlFor={`stage-${field}`}>{LABELS[field]}</Label>
                {field === "point_of_contact_id" && (
                  <FieldEditor
                    id={`stage-${field}`}
                    spec={{
                      name: field,
                      label: LABELS[field],
                      type: "relation",
                      choices: [],
                      search: (q, after) => searchPersonChoices(workspace, q, after),
                    }}
                    value={changes.point_of_contact_id ?? null}
                    onChange={(value) => set({ point_of_contact_id: (value as string | null) ?? null })}
                  />
                )}
                {field === "money" && (
                  <AmountField
                    id={`stage-${field}`}
                    value={{
                      amount: changes.amount ?? deal.amount ?? null,
                      currency: (changes.currency ?? deal.currency ?? "INR") as CrmCurrency,
                    }}
                    onChange={(money) => set({ amount: money.amount, currency: money.currency })}
                    numberFormat={numberFormat}
                  />
                )}
                {field === "next_step" && (
                  <Input
                    id={`stage-${field}`}
                    maxLength={500}
                    value={changes.next_step ?? ""}
                    onChange={(e) => set({ next_step: e.target.value })}
                  />
                )}
                {(field === "expected_close_on" || field === "proposal_sent_on" || field === "next_step_on") && (
                  <Input
                    id={`stage-${field}`}
                    type="date"
                    value={changes[field] ?? ""}
                    onChange={(e) => set({ [field]: e.target.value || null })}
                  />
                )}
              </div>
            ))}
            <WouldLine would={preview.would} companyName={deal.company_name} />
            {clash && (
              <div role="alert" className="space-y-2 rounded-md bg-destructive/10 p-2 text-sm text-destructive">
                <p>A project called “{clash.name}” already exists.</p>
                <Button type="button" size="sm" variant="outline" onClick={() => setResolving(true)}>
                  Rename or link…
                </Button>
              </div>
            )}
            {choice.link_project_id && (
              <p className="text-xs text-muted-foreground">
                The deal will be linked to the existing TVA project instead of creating one.
              </p>
            )}
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
          </form>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => close(false)}>
            Cancel
          </Button>
          {!blocked && (
            <Button type="submit" form="stage-form" disabled={busy || !preview || clash !== null}>
              {busy ? "Moving…" : `Move to ${stageLabel}`}
            </Button>
          )}
        </DialogFooter>
        {clash && (
          <NameClashDialog
            open={resolving}
            onOpenChange={setResolving}
            clash={clash}
            dealName={deal.name}
            onChoose={(next) => {
              setChoice(next);
              setResolving(false);
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
