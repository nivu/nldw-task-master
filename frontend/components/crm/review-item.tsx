"use client";

import Link from "next/link";
import { useState } from "react";

import { AiMarker } from "@/components/crm/ai-marker";
import { AmountField } from "@/components/crm/amount-field";
import { FieldEditor } from "@/components/crm/field-editor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  approveCrmReview,
  dismissCrmReview,
  searchCompanyChoices,
  searchPersonChoices,
} from "@/lib/api/crm";
import type {
  CrmCurrency,
  CrmLists,
  CrmMember,
  CrmReviewItem,
  ReviewApproveIn,
  ReviewDealChoice,
  ReviewDismissIgnore,
  ReviewItemStatus,
  ViewObjectKind,
} from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { type CrmTimezone, type NumberFormat, formatDate } from "@/lib/crm/format";
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
 *
 * Spec 010 FR-CAP-REV-01, -02: a deal signal shows its evidence (dates,
 * message count, whether the owner replied, attachment names, the reason;
 * never message text) and asks for what its stage needs: a new deal's next
 * step, or Proposal's amount, expected close, point of contact (and the TVA
 * project's name in Labs). Closed Won in Labs is the handoff, done from the
 * deal's page, so the item links there instead of approving.
 *
 * Spec 010 FR-CAP-REV-01 to -03 (S3): a new lead shows the company, domain,
 * lead stage and person it would create, or the existing company it links
 * to; it may have no target. Edit and approve can change any of them or link
 * an existing company. Dismissing a new lead may also ignore its sender or
 * its whole domain (only the sender for public webmail).
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

/** A new lead's stages (spec 010 contracts §1.3). */
const LEAD_STAGE_TEXT: Record<string, string> = {
  new: "New",
  contacted: "Contacted",
  qualified: "Qualified",
};

/** Claude's deal-stage hint on a new lead (contracts §6.1). */
const DEAL_HINT_TEXT: Record<string, string> = {
  discovery: "Discovery",
  proposal: "Proposal",
  closed_won: "Closed Won",
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

/** A record with a page of its own; notes and tasks open nothing here, nor
 *  a new lead with no target (spec 010). */
function recordHref(ws: string, recordType: ViewObjectKind | undefined, recordId: string | undefined) {
  if (!recordType || !recordId || recordType === "note" || recordType === "task") return null;
  return `/crm/${ws}/${OBJECT_PATH[recordType]}/${recordId}`;
}

/** Spec 010 contracts §6.2 — what a deal signal would do, as To review says it. */
function dealSignalLine(s: Record<string, unknown>, timeZone: CrmTimezone): string {
  if (s.action === "create") return "New deal at Discovery";
  if (s.stage === "proposal") {
    const sent = text(s.proposal_sent_on);
    return sent ? `Move to Proposal, proposal sent on ${formatDate(sent, timeZone)}` : "Move to Proposal";
  }
  if (s.stage === "closed_won") return "Possibly Closed Won: check the signed document";
  return "Deal signal";
}

/** The domain of an address, lower-cased; null without one. */
function domainOf(address: unknown): string | null {
  const at = text(address).trim().toLowerCase().split("@");
  return at.length === 2 && at[1] ? at[1] : null;
}

/** Spec 010 contracts §6.1 — what approving a new lead would create, in words. */
function newLeadLines(item: CrmReviewItem): string {
  const s = item.suggestion;
  const stage = LEAD_STAGE_TEXT[text(s.lead_stage)] ?? "New";
  const company =
    item.target && item.target.record_type === "company"
      ? `Use ${item.target.name}, already in the CRM (lead stage ${stage} if it is still New)`
      : `Create the company ${text(s.company_name) || "with no name"}` +
        `${text(s.domain) ? ` · ${text(s.domain)}` : " · no domain"} · lead stage ${stage}`;
  const name = [text(s.first_name), text(s.last_name)].filter(Boolean).join(" ");
  const person =
    `Add ${name || "the person"}` +
    `${text(s.title) ? ` (${text(s.title)})` : ""}` +
    `${text(s.email) ? ` · ${text(s.email)}` : ""}`;
  return `${company}\n${person}\n${fileLabel(conversationCount(item))}`;
}

/** Spec 010 FR-CAP-GRP-03 — the approval of a new lead, in words. */
function fileLabel(n: number): string {
  return n > 1 ? `File these ${n} conversations` : "File this conversation";
}

/** Spec 010 Revision 1 (FR-CAP-GRP) — how many conversations a capture item
 *  holds; a pre-074 item has one. */
function conversationCount(item: CrmReviewItem): number {
  const threads = item.source.link?.threads;
  if (Array.isArray(threads) && threads.length > 0) return threads.length;
  const e = item.suggestion.evidence as Record<string, unknown> | undefined;
  return e && typeof e.conversations === "number" && e.conversations > 0 ? e.conversations : 1;
}

/** Spec 010 FR-CAP-REV-01 — the evidence a capture item carries, in words. */
function evidenceRows(item: CrmReviewItem, timeZone: CrmTimezone): { label: string; value: string }[] {
  const e = item.suggestion.evidence;
  if (!e || typeof e !== "object") return [];
  const ev = e as Record<string, unknown>;
  const rows: { label: string; value: string }[] = [];
  const first = text(ev.first_at);
  const latest = text(ev.latest_at) || text(ev.message_at);
  if (first && latest && formatDate(first, timeZone) !== formatDate(latest, timeZone)) {
    rows.push({ label: "When", value: `${formatDate(first, timeZone)} to ${formatDate(latest, timeZone)}` });
  } else if (latest || first) {
    rows.push({ label: "When", value: formatDate(latest || first, timeZone) });
  }
  if (typeof ev.conversations === "number") {
    rows.push({
      label: "Conversations",
      value: `${ev.conversations} conversation${ev.conversations === 1 ? "" : "s"}`,
    });
  }
  if (typeof ev.message_count === "number") {
    rows.push({ label: "Messages", value: `${ev.message_count} email${ev.message_count === 1 ? "" : "s"}` });
  }
  if (typeof ev.owner_replied === "boolean") {
    rows.push({ label: "Replied", value: ev.owner_replied ? "Yes" : "No" });
  }
  const attachments = Array.isArray(ev.attachments) ? ev.attachments.map(text).filter(Boolean) : [];
  if (attachments.length > 0) rows.push({ label: "Attachments", value: attachments.join(", ") });
  if (text(ev.reason)) rows.push({ label: "Why", value: text(ev.reason) });
  if (text(ev.confidence)) rows.push({ label: "Confidence", value: text(ev.confidence) });
  return rows;
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
    case "deal_signal":
      return { suggestion: dealSignalLine(s, timeZone), current: null };
    case "new_lead":
      return { suggestion: newLeadLines(item), current: null };
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
        name: item.target?.name ?? "",
        owner_id: item.owner?.id ?? "",
        next_step: "",
        next_step_on: "",
      };
    case "deal_signal":
      if (s.action === "create") {
        return {
          name: text(s.name) || (item.target?.name ?? ""),
          next_step: text(s.next_step),
          next_step_on: text(s.next_step_on),
        };
      }
      return {
        amount: text(s.amount),
        currency: text(s.currency) || "INR",
        expected_close_on: text(s.expected_close_on),
        proposal_sent_on: text(s.proposal_sent_on),
        point_of_contact_id: text(s.point_of_contact_id),
        next_step: text(s.next_step),
        next_step_on: text(s.next_step_on),
        project_name: "",
      };
    case "new_lead":
      return {
        company_name: text(s.company_name),
        domain: text(s.domain),
        lead_stage: text(s.lead_stage) || "new",
        first_name: text(s.first_name),
        last_name: text(s.last_name),
        email: text(s.email),
        title: text(s.title),
        link_company_id: "",
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
  if (item.type === "deal_signal") {
    const out: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(edits)) {
      if (value) out[key] = value;
    }
    // A currency alone, with no amount, is not a change.
    if (!out.amount) delete out.currency;
    return out;
  }
  if (item.type === "new_lead") {
    // Only what the reviewer changed; a cleared field is sent as null (a
    // cleared domain makes the company with none, FR-CAP-DIS-09).
    const seeded = initialEdits(item);
    const out: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(edits)) {
      if (key === "link_company_id") {
        if (value) out[key] = value;
      } else if (value.trim() !== seeded[key]) {
        out[key] = value.trim() || null;
      }
    }
    // A linked company keeps its own name and domain.
    if (out.link_company_id) {
      delete out.company_name;
      delete out.domain;
    }
    return out;
  }
  return { ...edits };
}

/** A deal link has nothing to edit; a rule suggestion only by option; a deal
 *  signal by its stage (below). */
const EDITABLE = new Set([
  "next_step_change",
  "task_from_commitment",
  "task_from_meeting",
  "enrichment_conflict",
  "new_lead",
]);

/** Spec 010: a deal signal's form exists for a new deal, for Proposal, and
 *  for Closed Won outside Labs (a skipped stage's fields are required). */
function dealSignalEditable(item: CrmReviewItem, labs: boolean): boolean {
  return item.suggestion.action === "create" || dealStageForm(item, labs);
}

/** The amount, close date and point-of-contact form of a deal move. */
function dealStageForm(item: CrmReviewItem, labs: boolean): boolean {
  const stage = item.suggestion.stage;
  return item.suggestion.action !== "create" && (stage === "proposal" || (stage === "closed_won" && !labs));
}

/** The rule options that need a date or the new deal's fields (FR-CRMAI-RULE-05, -06). */
const RULE_EDITS = new Set(["next_touch", "qualify"]);

export function ReviewItemCard({
  workspace,
  item,
  timeZone,
  lists,
  members,
  showOwner,
  labs,
  numberFormat,
  onDecided,
}: {
  workspace: string;
  item: CrmReviewItem;
  timeZone: CrmTimezone;
  lists: CrmLists;
  /** The workspace is connected to TVA projects (Nunnari Labs). */
  labs: boolean;
  /** The viewer's amount format, for a deal signal's amount. */
  numberFormat: NumberFormat;
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
  const [ignore, setIgnore] = useState<ReviewDismissIgnore | "">("");

  const open = item.status === "open";
  const deal = item.deal_choices.find((d) => d.id === dealId);
  const { suggestion, current, draft } = describe(item, deal, lists, timeZone);
  const needsDeal = item.deal_choices.length > 1 && !dealId;
  const needsOption = item.type === "rule_suggestion" && options.length > 1 && !option;
  const targetHref = recordHref(workspace, item.target?.record_type, item.target?.record_id);
  const targetName = item.target?.name ?? (text(item.suggestion.company_name) || "New company");
  const sourceHref = recordHref(workspace, item.source.link?.record_type, item.source.link?.record_id);
  const assignees = members.filter((m) => m.role !== "viewer");
  const editable =
    EDITABLE.has(item.type) ||
    (item.type === "rule_suggestion" && RULE_EDITS.has(option)) ||
    (item.type === "deal_signal" && dealSignalEditable(item, labs));
  const evidence = evidenceRows(item, timeZone);
  // Closed Won in Labs is the handoff (FR-CRM-TVA-07): approving would be
  // refused with its sentence, so the item opens the deal instead.
  const handoff = labs && item.type === "deal_signal" && item.suggestion.stage === "closed_won";
  const handoffDeal = deal ?? (item.deal_choices.length === 1 ? item.deal_choices[0] : undefined);
  const dealCreate = item.type === "deal_signal" && item.suggestion.action === "create";
  const dealProposal = item.type === "deal_signal" && dealStageForm(item, labs);
  const newLead = item.type === "new_lead";
  const leadLinked = newLead && (Boolean(edits.link_company_id) || item.target?.record_type === "company");
  // Spec 010 FR-CAP-REV-03: the sender's own domain is what the server adds,
  // even when the lead's company domain came from the text. It is offered
  // only when it is the lead's domain: a public webmail lead has no domain
  // (FR-CAP-DIS-09), and one whose website came from the text names another
  // domain than the sender's, which for webmail the server refuses (plan
  // §5.11: hidden for webmail).
  const senderDomain = newLead ? domainOf(item.suggestion.email) : null;
  const ignoreAddress = newLead && senderDomain ? text(item.suggestion.email).trim().toLowerCase() : null;
  const leadDomain = text(item.suggestion.domain).trim().toLowerCase();
  const ignoreDomain = senderDomain && leadDomain === senderDomain ? senderDomain : null;
  const hint = newLead ? DEAL_HINT_TEXT[text(item.suggestion.deal_stage_hint)] : undefined;
  // A discovered deal signal whose company was matched only through a
  // website named in the email's text (spec 010 contracts §6.2).
  const signalEvidence =
    item.type === "deal_signal" && item.suggestion.evidence && typeof item.suggestion.evidence === "object"
      ? (item.suggestion.evidence as Record<string, unknown>)
      : {};
  const signalFromText = signalEvidence.domain_from_text === true && Boolean(text(signalEvidence.domain));

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
      const done = await dismissCrmReview(workspace, item.id, ignore || undefined);
      onDecided(
        done.ignore
          ? `Dismissed: ${item.type_label}. ${done.ignore.value} is on the ignore list.`
          : `Dismissed: ${item.type_label}.`
      );
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
          {!open && (
            <Badge variant="secondary">
              {item.status === "expired" && item.result?.superseded_by
                ? "Merged into another suggestion"
                : STATUS_TEXT[item.status]}
            </Badge>
          )}
          <span className="font-normal text-muted-foreground">
            ·{" "}
            {targetHref ? (
              <Link href={targetHref} className="underline">
                {targetName}
              </Link>
            ) : (
              targetName
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
          {hint && (
            <>
              <dt className="text-muted-foreground">Deal</dt>
              <dd>Looks ready for a deal at {hint}; add it from the company once approved.</dd>
            </>
          )}
          {newLead && item.suggestion.domain_from_text === true && (
            <>
              <dt className="text-muted-foreground">Domain</dt>
              <dd>Taken from the text, not an address; check it before approving.</dd>
            </>
          )}
          {signalFromText && (
            <>
              <dt className="text-muted-foreground">Matched by</dt>
              <dd>
                {text(signalEvidence.domain)}, taken from the text of an email
                {text(signalEvidence.email) ? ` from ${text(signalEvidence.email)}` : ""}, not an address;
                check it before approving.
              </dd>
            </>
          )}
          {evidence.map((row) => (
            <div key={row.label} className="contents" data-testid="review-evidence">
              <dt className="text-muted-foreground">{row.label}</dt>
              <dd className="whitespace-pre-wrap">{row.value}</dd>
            </div>
          ))}
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
            {dealCreate && (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor={field("name")}>Deal name</Label>
                  <Input id={field("name")} value={edits.name} maxLength={200} onChange={set("name")} />
                </div>
                <div className="grid gap-3 sm:grid-cols-[1fr_10rem]">
                  <div className="space-y-1.5">
                    <Label htmlFor={field("next_step")}>Next step</Label>
                    <Input
                      id={field("next_step")}
                      value={edits.next_step}
                      maxLength={500}
                      onChange={set("next_step")}
                      required
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={field("next_step_on")}>By</Label>
                    <Input
                      id={field("next_step_on")}
                      type="date"
                      value={edits.next_step_on}
                      onChange={set("next_step_on")}
                      required
                    />
                  </div>
                </div>
              </>
            )}
            {dealProposal && (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor={field("amount")}>Amount</Label>
                  <AmountField
                    id={field("amount")}
                    value={{ amount: edits.amount || null, currency: (edits.currency || "INR") as CrmCurrency }}
                    onChange={(money) =>
                      setEdits((prev) => ({ ...prev, amount: money.amount ?? "", currency: money.currency }))
                    }
                    numberFormat={numberFormat}
                  />
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor={field("expected_close_on")}>Expected close date</Label>
                    <Input
                      id={field("expected_close_on")}
                      type="date"
                      value={edits.expected_close_on}
                      onChange={set("expected_close_on")}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={field("proposal_sent_on")}>Proposal sent on</Label>
                    <Input
                      id={field("proposal_sent_on")}
                      type="date"
                      value={edits.proposal_sent_on}
                      onChange={set("proposal_sent_on")}
                    />
                  </div>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={field("point_of_contact_id")}>Point of contact</Label>
                  <FieldEditor
                    id={field("point_of_contact_id")}
                    spec={{
                      name: "point_of_contact_id",
                      label: "Point of contact",
                      type: "relation",
                      choices: [],
                      search: (q, after) => searchPersonChoices(workspace, q, after),
                    }}
                    value={edits.point_of_contact_id || null}
                    onChange={(value) =>
                      setEdits((prev) => ({ ...prev, point_of_contact_id: (value as string | null) ?? "" }))
                    }
                  />
                </div>
                <div className="grid gap-3 sm:grid-cols-[1fr_10rem]">
                  <div className="space-y-1.5">
                    <Label htmlFor={field("next_step")}>Next step</Label>
                    <Input
                      id={field("next_step")}
                      value={edits.next_step}
                      maxLength={500}
                      onChange={set("next_step")}
                    />
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
                {labs && (
                  <div className="space-y-1.5">
                    <Label htmlFor={field("project_name")}>TVA project name</Label>
                    <Input
                      id={field("project_name")}
                      value={edits.project_name}
                      maxLength={200}
                      placeholder="The deal's name"
                      onChange={set("project_name")}
                    />
                  </div>
                )}
              </>
            )}
            {newLead && (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor={field("link_company_id")}>Link to a company already in the CRM</Label>
                  <FieldEditor
                    id={field("link_company_id")}
                    spec={{
                      name: "link_company_id",
                      label: "Company",
                      type: "relation",
                      choices: [],
                      search: (q, after) => searchCompanyChoices(workspace, q, after),
                    }}
                    value={edits.link_company_id || null}
                    onChange={(value) =>
                      setEdits((prev) => ({ ...prev, link_company_id: (value as string | null) ?? "" }))
                    }
                  />
                  <p className="text-xs text-muted-foreground">
                    {item.target?.record_type === "company"
                      ? `Leave empty to use ${item.target.name}.`
                      : "Leave empty to create a new company."}
                  </p>
                </div>
                {!leadLinked && (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="space-y-1.5">
                      <Label htmlFor={field("company_name")}>Company name</Label>
                      <Input
                        id={field("company_name")}
                        value={edits.company_name}
                        maxLength={200}
                        onChange={set("company_name")}
                        required
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor={field("domain")}>Domain</Label>
                      <Input
                        id={field("domain")}
                        value={edits.domain}
                        maxLength={253}
                        placeholder="None"
                        onChange={set("domain")}
                      />
                    </div>
                  </div>
                )}
                <div className="space-y-1.5">
                  <Label htmlFor={field("lead_stage")}>Lead stage</Label>
                  <select
                    id={field("lead_stage")}
                    className="h-8 w-full max-w-[12rem] rounded-md border bg-background px-2 text-sm"
                    value={edits.lead_stage}
                    onChange={set("lead_stage")}
                  >
                    {Object.entries(LEAD_STAGE_TEXT).map(([key, label]) => (
                      <option key={key} value={key}>
                        {label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor={field("first_name")}>First name</Label>
                    <Input
                      id={field("first_name")}
                      value={edits.first_name}
                      maxLength={200}
                      onChange={set("first_name")}
                      required
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={field("last_name")}>Last name</Label>
                    <Input
                      id={field("last_name")}
                      value={edits.last_name}
                      maxLength={200}
                      onChange={set("last_name")}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={field("email")}>Email</Label>
                    <Input
                      id={field("email")}
                      type="email"
                      value={edits.email}
                      maxLength={320}
                      onChange={set("email")}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={field("title")}>Job title</Label>
                    <Input id={field("title")} value={edits.title} maxLength={200} onChange={set("title")} />
                  </div>
                </div>
              </>
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

        {open && handoff && (
          <p className="text-sm text-muted-foreground">
            In Nunnari Labs, Closed Won is done with the handoff from the deal&apos;s page.{" "}
            {handoffDeal ? (
              <>
                <Link href={`/crm/${workspace}/${OBJECT_PATH.opportunity}/${handoffDeal.id}`} className="underline">
                  Open {handoffDeal.name} to hand it off
                </Link>
                , then dismiss this item.
              </>
            ) : (
              "Choose the deal above, then open it to hand it off."
            )}
          </p>
        )}

        {open && !editing && (
          <div className="flex flex-wrap gap-2">
            {!handoff && (
              <Button size="sm" onClick={() => approve(false)} disabled={busy || needsDeal || needsOption}>
                {newLead ? fileLabel(conversationCount(item)) : "Approve"}
              </Button>
            )}
            {editable && (
              <Button size="sm" variant="outline" onClick={() => setEditing(true)} disabled={busy}>
                Edit and approve
              </Button>
            )}
            {ignoreAddress && (
              <select
                aria-label="When dismissing"
                className="h-8 rounded-md border bg-background px-2 text-sm"
                value={ignore}
                disabled={busy}
                onChange={(e) => setIgnore(e.target.value as ReviewDismissIgnore | "")}
              >
                <option value="">Dismiss only</option>
                <option value="address">Dismiss and ignore this sender ({ignoreAddress})</option>
                {ignoreDomain && (
                  <option value="domain">Dismiss and ignore this whole domain ({ignoreDomain})</option>
                )}
              </select>
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
