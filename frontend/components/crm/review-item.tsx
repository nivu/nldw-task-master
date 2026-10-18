"use client";

import Link from "next/link";
import { useState } from "react";

import { AiMarker } from "@/components/crm/ai-marker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { approveCrmReview, dismissCrmReview } from "@/lib/api/crm";
import type {
  CrmLists,
  CrmMember,
  CrmReviewItem,
  ReviewApproveIn,
  ReviewDealChoice,
  ReviewItemStatus,
  ViewObjectKind,
} from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { type CrmTimezone, formatDate } from "@/lib/crm/format";
import { OBJECT_PATH } from "@/lib/crm/view-fields";

/**
 * One Review Queue item — spec 009 FR-CRMAI-REV-01, -04, -06; contracts §2.2.1.
 *
 * The suggestion, the value it would replace, the source with a link, and
 * Approve, Edit and approve, Dismiss. A suggestion that may apply to several
 * open deals asks which one first. Approving goes through the same service a
 * manual edit uses, so its refusals are the manual path's own sentences; a
 * task the tasks service refused keeps the item open with the refusal shown.
 * Every generated string is rendered as a text node (FR-CRMAI-LLM-04).
 */

const STATUS_TEXT: Record<ReviewItemStatus, string> = {
  open: "Open",
  approved: "Approved",
  dismissed: "Dismissed",
  expired: "Expired",
};

/** The fields an enrichment conflict may be about (data-model §3.2.1). */
const ENRICHED_FIELD: Record<string, { label: string; list?: string }> = {
  industry_id: { label: "Industry", list: "company.industry" },
  size_id: { label: "Size", list: "company.size" },
  city: { label: "City" },
  country: { label: "Country" },
  description: { label: "Description" },
  linkedin: { label: "LinkedIn" },
};

/** Rule suggestion options (FR-CRMAI-RULE-05, -06). */
const OPTION_TEXT: Record<string, string> = {
  nurturing: "Move to Nurturing",
  disqualified: "Move to Disqualified",
  next_touch: "Set a next touch",
  qualify: "Qualify and create a deal",
  close_out: "Close out as Not interested",
  mark_used: "Mark the draft as used",
};

const text = (value: unknown): string =>
  typeof value === "string" ? value : value === null || value === undefined ? "" : String(value);

/** A record with a page of its own; notes and tasks open nothing here. */
function recordHref(ws: string, recordType: ViewObjectKind | undefined, recordId: string | undefined) {
  if (!recordType || !recordId || recordType === "note" || recordType === "task") return null;
  return `/crm/${ws}/${OBJECT_PATH[recordType]}/${recordId}`;
}

function nextStepLine(nextStep: unknown, nextStepOn: unknown, timeZone: CrmTimezone): string {
  const step = text(nextStep) || "No next step";
  const on = text(nextStepOn);
  return on ? `${step} · ${formatDate(on, timeZone)}` : step;
}

/** What the item would set and what it would replace, in words. */
function describe(
  item: CrmReviewItem,
  deal: ReviewDealChoice | undefined,
  lists: CrmLists,
  timeZone: CrmTimezone
): { suggestion: string; current: string | null; draft?: string } {
  const s = item.suggestion;
  switch (item.type) {
    case "next_step_change": {
      const current = deal
        ? nextStepLine(deal.next_step, deal.next_step_on, timeZone)
        : nextStepLine(item.current.next_step, item.current.next_step_on, timeZone);
      return { suggestion: nextStepLine(s.next_step, s.next_step_on, timeZone), current };
    }
    case "task_from_commitment":
    case "task_from_meeting": {
      const due = text(s.due_on);
      return {
        suggestion: due ? `${text(s.title)} · due ${formatDate(due, timeZone)}` : text(s.title),
        current: null,
      };
    }
    case "deal_link":
      return { suggestion: "Link this note to a deal", current: null };
    case "enrichment_conflict": {
      const field = text(s.field);
      const meta = ENRICHED_FIELD[field];
      const name = meta?.label ?? field;
      const stored = text(item.current[field]);
      const storedLabel = meta?.list
        ? (lists[meta.list] ?? []).find((o) => o.id === stored)?.label ?? stored
        : stored;
      return {
        suggestion: `${name}: ${text(s.label) || text(s.value)}`,
        current: `${name}: ${storedLabel || "empty"}`,
      };
    }
    case "rule_suggestion": {
      const current = Object.entries(item.current)
        .map(([key, value]) => `${key.replace(/_/g, " ")}: ${text(value)}`)
        .join(" · ");
      return {
        suggestion: text(s.rule) ? `Follow-up rule ${text(s.rule)}` : item.type_label,
        current: current || null,
        draft: text(s.draft) || undefined,
      };
    }
  }
}

/** The fields "Edit and approve" offers, seeded from the suggestion. */
function initialEdits(item: CrmReviewItem): Record<string, string> {
  const s = item.suggestion;
  switch (item.type) {
    case "next_step_change":
      return { next_step: text(s.next_step), next_step_on: text(s.next_step_on) };
    case "task_from_commitment":
    case "task_from_meeting":
      return {
        title: text(s.title),
        due_on: text(s.due_on),
        assignee_id: "",
        notes: "",
      };
    case "enrichment_conflict":
      return { value: text(s.value) };
    case "rule_suggestion":
      return {
        next_touch_on: "",
        name: item.target.name,
        owner_id: item.owner?.id ?? "",
        next_step: "",
        next_step_on: "",
      };
    default:
      return {};
  }
}

/** The edits as the route takes them: empty optional fields left out. */
function editsPayload(
  item: CrmReviewItem,
  edits: Record<string, string>,
  option: string
): Record<string, unknown> {
  if (item.type === "rule_suggestion") {
    if (option === "next_touch") return { next_touch_on: edits.next_touch_on };
    if (option !== "qualify") return {};
    const deal: Record<string, string> = {};
    for (const key of ["name", "owner_id", "next_step", "next_step_on"]) {
      if (edits[key]) deal[key] = edits[key];
    }
    return { new_opportunity: deal };
  }
  if (item.type === "task_from_commitment" || item.type === "task_from_meeting") {
    const out: Record<string, unknown> = { title: edits.title };
    if (edits.due_on) out.due_on = edits.due_on;
    if (edits.assignee_id) out.assignee_id = edits.assignee_id;
    if (edits.notes) out.notes = edits.notes;
    return out;
  }
  return { ...edits };
}

/** A deal link has nothing to edit; a rule suggestion only by option. */
const EDITABLE = new Set(["next_step_change", "task_from_commitment", "task_from_meeting", "enrichment_conflict"]);

/** The rule options that need a date or the new deal's fields (FR-CRMAI-RULE-05, -06). */
const RULE_EDITS = new Set(["next_touch", "qualify"]);

export function ReviewItemCard({
  workspace,
  item,
  timeZone,
  lists,
  members,
  showOwner,
  onDecided,
}: {
  workspace: string;
  item: CrmReviewItem;
  timeZone: CrmTimezone;
  lists: CrmLists;
  /** Assignee choices for a task: the workspace's Admins and Members. */
  members: CrmMember[];
  /** All and Decided name whose item it is. */
  showOwner: boolean;
  onDecided: (message: string) => void;
}) {
  const [dealId, setDealId] = useState<string>(
    item.deal_choices.length === 1 ? item.deal_choices[0].id : ""
  );
  const options = Array.isArray(item.suggestion.options) ? item.suggestion.options.map(text) : [];
  const [option, setOption] = useState<string>(options.length === 1 ? options[0] : "");
  const [editing, setEditing] = useState(false);
  const [edits, setEdits] = useState<Record<string, string>>(() => initialEdits(item));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = item.status === "open";
  const deal = item.deal_choices.find((d) => d.id === dealId);
  const { suggestion, current, draft } = describe(item, deal, lists, timeZone);
  const needsDeal = item.deal_choices.length > 1 && !dealId;
  const needsOption = item.type === "rule_suggestion" && options.length > 1 && !option;
  const targetHref = recordHref(workspace, item.target.record_type, item.target.record_id);
  const sourceHref = recordHref(workspace, item.source.link?.record_type, item.source.link?.record_id);
  const assignees = members.filter((m) => m.role !== "viewer");
  const editable = EDITABLE.has(item.type) || (item.type === "rule_suggestion" && RULE_EDITS.has(option));

  async function approve(withEdits: boolean) {
    setBusy(true);
    setError(null);
    // The version shown: a suggestion refreshed since is refused (409).
    const approval: ReviewApproveIn = { version: item.version };
    if (item.deal_choices.length > 1) approval.deal_id = dealId;
    if (item.type === "rule_suggestion" && options.length > 1) approval.option = option;
    if (withEdits) approval.edits = editsPayload(item, edits, option);
    try {
      await approveCrmReview(workspace, item.id, approval);
      onDecided(`Approved: ${item.type_label}.`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function dismiss() {
    setBusy(true);
    setError(null);
    try {
      await dismissCrmReview(workspace, item.id);
      onDecided(`Dismissed: ${item.type_label}.`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const set = (key: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setEdits((prev) => ({ ...prev, [key]: e.target.value }));
  const field = (key: string) => `review-${item.id}-${key}`;
  const enriched = item.type === "enrichment_conflict" ? ENRICHED_FIELD[text(item.suggestion.field)] : undefined;
  const refusal = error ?? item.refusal;

  return (
    <Card id={`review-${item.id}`} data-testid="review-item">
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
          {item.type_label}
          {item.ai && <AiMarker ai={item.ai} timeZone={timeZone} label={item.source.label} />}
          {!open && <Badge variant="secondary">{STATUS_TEXT[item.status]}</Badge>}
          <span className="font-normal text-muted-foreground">
            ·{" "}
            {targetHref ? (
              <Link href={targetHref} className="underline">
                {item.target.name}
              </Link>
            ) : (
              item.target.name
            )}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-[8rem_1fr]">
          <dt className="text-muted-foreground">Suggestion</dt>
          <dd className="font-medium whitespace-pre-wrap">{suggestion}</dd>
          {current !== null && (
            <>
              <dt className="text-muted-foreground">Now</dt>
              <dd className="whitespace-pre-wrap">{current}</dd>
            </>
          )}
          {draft && (
            <>
              <dt className="text-muted-foreground">Draft</dt>
              <dd className="whitespace-pre-wrap">{draft}</dd>
            </>
          )}
          <dt className="text-muted-foreground">Source</dt>
          <dd>
            {sourceHref ? (
              <Link href={sourceHref} className="underline">
                {item.source.label}
              </Link>
            ) : (
              item.source.label
            )}
          </dd>
          {showOwner && (
            <>
              <dt className="text-muted-foreground">For</dt>
              <dd>{item.owner?.name ?? "The workspace's Admins"}</dd>
            </>
          )}
          <dt className="text-muted-foreground">{open ? "Expires" : "Decided"}</dt>
          <dd>
            {open
              ? formatDate(item.expires_on, timeZone)
              : item.decided_at
                ? `${formatDate(item.decided_at, timeZone)}${item.decided_by?.name ? ` by ${item.decided_by.name}` : ""}`
                : "—"}
          </dd>
        </dl>

        {open && item.deal_choices.length > 1 && (
          <div className="space-y-1.5">
            <Label htmlFor={field("deal")}>Deal</Label>
            <select
              id={field("deal")}
              className="h-8 w-full max-w-md rounded-md border bg-background px-2 text-sm"
              value={dealId}
              onChange={(e) => setDealId(e.target.value)}
            >
              <option value="">Choose the deal this applies to</option>
              {item.deal_choices.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                  {item.type === "next_step_change" ? ` (${nextStepLine(d.next_step, d.next_step_on, timeZone)})` : ""}
                </option>
              ))}
            </select>
          </div>
        )}

        {open && options.length > 1 && (
          <fieldset className="space-y-1">
            <legend className="text-muted-foreground">Choose one</legend>
            {options.map((key) => (
              <label key={key} className="flex items-center gap-2">
                <input
                  type="radio"
                  name={field("option")}
                  value={key}
                  checked={option === key}
                  onChange={() => setOption(key)}
                />
                {OPTION_TEXT[key] ?? key}
              </label>
            ))}
          </fieldset>
        )}

        {open && editing && (
          <form
            className="space-y-3 rounded-md border p-3"
            onSubmit={(e) => {
              e.preventDefault();
              approve(true);
            }}
          >
            {item.type === "next_step_change" && (
              <div className="grid gap-3 sm:grid-cols-[1fr_10rem]">
                <div className="space-y-1.5">
                  <Label htmlFor={field("next_step")}>Next step</Label>
                  <Input id={field("next_step")} value={edits.next_step} onChange={set("next_step")} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={field("next_step_on")}>By</Label>
                  <Input
                    id={field("next_step_on")}
                    type="date"
                    value={edits.next_step_on}
                    onChange={set("next_step_on")}
                  />
                </div>
              </div>
            )}
            {(item.type === "task_from_commitment" || item.type === "task_from_meeting") && (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor={field("title")}>Task</Label>
                  <Input
                    id={field("title")}
                    value={edits.title}
                    maxLength={200}
                    onChange={set("title")}
                    required
                  />
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor={field("due_on")}>Due</Label>
                    <Input id={field("due_on")} type="date" value={edits.due_on} onChange={set("due_on")} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={field("assignee_id")}>Assignee</Label>
                    <select
                      id={field("assignee_id")}
                      className="h-8 w-full rounded-md border bg-background px-2 text-sm"
                      value={edits.assignee_id}
                      onChange={set("assignee_id")}
                    >
                      <option value="">As suggested</option>
                      {assignees.map((m) => (
                        <option key={m.user_id} value={m.user_id}>
                          {m.display_name}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={field("notes")}>Notes</Label>
                  <Textarea id={field("notes")} value={edits.notes} onChange={set("notes")} rows={2} />
                </div>
              </>
            )}
            {item.type === "enrichment_conflict" && (
              <div className="space-y-1.5">
                <Label htmlFor={field("value")}>{enriched?.label ?? "Value"}</Label>
                {enriched?.list ? (
                  <select
                    id={field("value")}
                    className="h-8 w-full max-w-md rounded-md border bg-background px-2 text-sm"
                    value={edits.value}
                    onChange={set("value")}
                  >
                    {(lists[enriched.list] ?? [])
                      .filter((o) => o.is_active || o.id === edits.value)
                      .map((o) => (
                        <option key={o.id} value={o.id}>
                          {o.label}
                        </option>
                      ))}
                  </select>
                ) : text(item.suggestion.field) === "description" ? (
                  <Textarea id={field("value")} value={edits.value} onChange={set("value")} rows={3} />
                ) : (
                  <Input id={field("value")} value={edits.value} onChange={set("value")} />
                )}
              </div>
            )}
            {item.type === "rule_suggestion" && option === "next_touch" && (
              <div className="space-y-1.5">
                <Label htmlFor={field("next_touch_on")}>Next touch</Label>
                <Input
                  id={field("next_touch_on")}
                  type="date"
                  className="max-w-[10rem]"
                  value={edits.next_touch_on}
                  onChange={set("next_touch_on")}
                  required
                />
              </div>
            )}
            {item.type === "rule_suggestion" && option === "qualify" && (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor={field("name")}>Deal name</Label>
                  <Input id={field("name")} value={edits.name} onChange={set("name")} required />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={field("owner_id")}>Owner</Label>
                  <select
                    id={field("owner_id")}
                    className="h-8 w-full max-w-md rounded-md border bg-background px-2 text-sm"
                    value={edits.owner_id}
                    onChange={set("owner_id")}
                    required
                  >
                    <option value="">Choose the owner</option>
                    {assignees.map((m) => (
                      <option key={m.user_id} value={m.user_id}>
                        {m.display_name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="grid gap-3 sm:grid-cols-[1fr_10rem]">
                  <div className="space-y-1.5">
                    <Label htmlFor={field("next_step")}>Next step</Label>
                    <Input id={field("next_step")} value={edits.next_step} onChange={set("next_step")} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={field("next_step_on")}>By</Label>
                    <Input
                      id={field("next_step_on")}
                      type="date"
                      value={edits.next_step_on}
                      onChange={set("next_step_on")}
                    />
                  </div>
                </div>
              </>
            )}
            <div className="flex flex-wrap gap-2">
              <Button type="submit" size="sm" disabled={busy || needsDeal || needsOption}>
                {busy ? "Saving…" : "Save and approve"}
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            </div>
          </form>
        )}

        {refusal && (
          <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
            {refusal}
          </div>
        )}

        {open && !editing && (
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => approve(false)} disabled={busy || needsDeal || needsOption}>
              Approve
            </Button>
            {editable && (
              <Button size="sm" variant="outline" onClick={() => setEditing(true)} disabled={busy}>
                Edit and approve
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={dismiss} disabled={busy}>
              Dismiss
            </Button>
            {needsDeal && (
              <span className="self-center text-xs text-muted-foreground">Choose the deal this applies to.</span>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
