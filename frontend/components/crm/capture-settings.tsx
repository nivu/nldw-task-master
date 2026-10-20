"use client";

import { useState } from "react";

import { AiMarker } from "@/components/crm/ai-marker";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  addCrmCaptureIgnore,
  applyCrmCaptureDryRun,
  getCrmSettings,
  removeCrmCaptureIgnore,
  startCrmCaptureDryRun,
  updateCrmSetting,
} from "@/lib/api/crm";
import type {
  AiProvenance,
  CrmCaptureConfidence,
  CrmCaptureFinding,
  CrmCaptureIgnoreIn,
  CrmCaptureMode,
  CrmCaptureOutcome,
  CrmCaptureOverview,
  CrmCaptureRecallFate,
  CrmCaptureRecallSummary,
  CrmCaptureRouting,
} from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { type CrmTimezone, formatDate } from "@/lib/crm/format";
import { useAsync } from "@/lib/use-async";

/** The workspace course enquiries are sent to (FR-CAP-ACAD-03). */
const ACADEMY_KEY = "academy";

/** Dry run is started by its button, never chosen here; Live is first
 *  entered through Apply, then may be chosen again (contracts §1.2). */
const MODES: { value: CrmCaptureMode; label: string }[] = [
  { value: "off", label: "Off" },
  { value: "dry_run", label: "Dry run (started with Run dry run)" },
  { value: "live", label: "Live" },
];

const OUTCOMES: { key: CrmCaptureOutcome; label: string }[] = [
  { key: "new_lead", label: "New lead" },
  { key: "deal_signal", label: "Deal signal" },
  { key: "other_business", label: "Other business" },
  { key: "not_relevant", label: "Not relevant" },
];

const CONFIDENCES: { key: CrmCaptureConfidence; label: string }[] = [
  { key: "high", label: "High" },
  { key: "medium", label: "Medium" },
  { key: "low", label: "Low" },
];

const CONFIDENCE_TEXT: Record<CrmCaptureConfidence, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
};

/** Each recall row's reason as the page names it (contracts §7.1). */
const FATE_TEXT: Record<CrmCaptureRecallFate, string> = {
  found: "Found",
  filed_known: "Filed as known",
  judged_otherwise: "Judged otherwise",
  known_now: "Contacts added later (not sent to Claude)",
  ignored: "On the ignore list",
  internal_only: "Skipped: internal only",
  calendar: "Skipped: calendar notification",
  automated: "Skipped: automated mail",
  outside_scope: "Outside Primary and Sent",
  not_judged: "Not judged: Claude or Gmail failed",
  academy_unrouted: "Course enquiry, no Academy workspace",
  skipped_rules: "Skipped by the rules",
};

/** The untestable reasons in display order (FR-CAP-RPT-06). */
const UNTESTED_TEXT: [keyof CrmCaptureRecallSummary["untested"], string][] = [
  ["known", "contacts now in the CRM (filed automatically)"],
  ["ignored", "on the ignore list"],
  ["internal_only", "internal-only"],
  ["calendar", "calendar notifications"],
  ["automated", "automated mail"],
  ["outside_scope", "outside Primary and Sent"],
  ["not_judged", "could not be judged"],
  ["skipped_rules", "skipped by the rules"],
];

/** The reason is Claude's; the marker says so (009 FR-CRMAI-LLM-04). */
const REASON_AI: AiProvenance = { source_kind: "email_thread", model: null, generated_at: null };

const usd = (amount: string) =>
  `$${Number(amount).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/**
 * Settings, Email capture — spec 010 FR-CAP-DIS-01, -07, -10, FR-CAP-RPT-01
 * to -03; plan §5.11.
 *
 * The mode, the Run dry run button and the latest dry run's report: counts,
 * the estimated cost, every lead and deal signal Claude found (low
 * confidence listed apart) and the recall check over the labelled
 * conversations. A dry run writes decisions only: no record and no Review
 * Queue item. No message text is ever shown; the reason is generated plain
 * text. Admins only; the server refuses everyone else.
 *
 * S3 (FR-CAP-RPT-04, FR-CAP-DIS-03): Apply turns a finished dry run's high-
 * and medium-confidence findings into Review Queue items with no new Claude
 * call and sets the mode to Live; Live can be chosen again once a dry run
 * was applied. The ignore list is edited here and grows from dismissals.
 *
 * Revision 1 (FR-CAP-RPT-09, FR-CAP-ACAD-03): when a run found course
 * enquiries, the counts name those to be sent (sent, once applied) to the
 * Academy and those with no Academy workspace to take them; in the Academy,
 * the *Accept course enquiries from Labs* switch.
 */
export function CaptureSettings({
  workspace,
  overview,
  timeZone,
  onChanged,
}: {
  workspace: string;
  overview: CrmCaptureOverview;
  timeZone: CrmTimezone;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [ignoreKind, setIgnoreKind] = useState<CrmCaptureIgnoreIn["kind"]>("domain");
  const [ignoreValue, setIgnoreValue] = useState("");
  const run = overview.dry_run;
  const running = run !== null && run.finished_at === null;
  const capturing = overview.mailboxes.filter((m) => m.capture_source === "primary_sent");

  // The server's own rule: any dry run of the workspace was applied (not
  // only the latest), and a mailbox is on Primary and Sent.
  const liveAllowed = overview.mode === "live" || overview.live_allowed;
  // The recall is the report's only once the run finished (contracts §2.1).
  const recallReady = run !== null && run.finished_at !== null && !run.cancelled;

  /** Runs one change; a string it returns is shown once it succeeds. */
  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const done = await work();
      if (typeof done === "string") setNotice(done);
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function changeMode(next: CrmCaptureMode) {
    if (next === overview.mode) return;
    if (
      running &&
      !window.confirm(
        "Switch email capture off? The dry run in progress stops and cannot be applied; " +
          "you can run a new one later."
      )
    ) {
      return;
    }
    act(() => updateCrmSetting(workspace, "capture", { mode: next }));
  }

  function startDryRun() {
    if (
      overview.mode === "live" &&
      !window.confirm(
        "Run a dry run? Live capture pauses while it runs, until this dry run is applied " +
          "or Live is chosen again."
      )
    ) {
      return;
    }
    act(() => startCrmCaptureDryRun(workspace));
  }

  function apply() {
    if (run === null) return;
    if (
      !window.confirm(
        "Apply this dry run? Its high- and medium-confidence leads and deal signals become " +
          "Review Queue items (no new Claude calls), and email capture switches to Live."
      )
    ) {
      return;
    }
    act(async () => {
      const made = (await applyCrmCaptureDryRun(workspace, run.id)).items_created;
      return `Applied: ${made} Review Queue item${made === 1 ? "" : "s"} made. Email capture is Live.`;
    });
  }

  function addIgnore(e: React.FormEvent) {
    e.preventDefault();
    act(async () => {
      await addCrmCaptureIgnore(workspace, { kind: ignoreKind, value: ignoreValue });
      setIgnoreValue("");
    });
  }

  function removeIgnore(id: string, value: string) {
    if (!window.confirm(`Remove ${value} from the ignore list? New mail from it will be judged again.`)) return;
    act(() => removeCrmCaptureIgnore(workspace, id));
  }

  const leads = run?.findings.filter((f) => f.confidence !== "low") ?? [];
  const low = run?.findings.filter((f) => f.confidence === "low") ?? [];

  return (
    <div className="space-y-6">
      {error && (
        <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}
      {notice && (
        <p role="status" className="rounded-md bg-muted p-3 text-sm">
          {notice}
        </p>
      )}

      {workspace === ACADEMY_KEY && <RoutingSwitch workspace={workspace} />}

      <section className="space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <label className="space-y-1 text-sm">
            <span className="block text-muted-foreground">Mode</span>
            <select
              className="h-8 rounded-md border bg-background px-2 text-sm"
              value={overview.mode}
              disabled={busy}
              onChange={(e) => changeMode(e.target.value as CrmCaptureMode)}
            >
              {MODES.map((m) => (
                <option
                  key={m.value}
                  value={m.value}
                  disabled={m.value === "dry_run" || (m.value === "live" && !liveAllowed)}
                >
                  {m.value === "live" && !liveAllowed ? "Live (after a dry run is applied)" : m.label}
                </option>
              ))}
            </select>
          </label>
          <Button
            disabled={busy || running}
            onClick={startDryRun}
          >
            Run dry run
          </Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={onChanged}>
            Refresh
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">
          A dry run asks Claude about every unknown conversation in Primary and Sent since each
          mailbox&apos;s start date and reports what it found. It creates no company, person or
          Review Queue item. It runs in the background, hour by hour, until every conversation is
          judged; its cost counts against the AI budget.
        </p>
        {capturing.length === 0 ? (
          <p role="status" className="rounded-md bg-muted p-3 text-sm">
            No mailbox is on Primary and Sent yet. Set one in Mailboxes first.
          </p>
        ) : (
          <p className="text-sm">
            Reading{" "}
            {capturing
              .map((m) => `${m.email} from ${formatDate(m.capture_start_date, timeZone)}`)
              .join(", ")}
            .
          </p>
        )}
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-medium">Ignore list</h3>
        <p className="text-sm text-muted-foreground">
          Conversations whose outside people are all on this list are skipped before anything is
          read. Dismissing a new lead can add its sender or its domain here.
        </p>
        {overview.ignores.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing is ignored.</p>
        ) : (
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="p-2 font-normal">Ignored</th>
                  <th className="p-2 font-normal">Kind</th>
                  <th className="p-2 font-normal">Added</th>
                  <th className="p-2 font-normal">
                    <span className="sr-only">Remove</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {overview.ignores.map((entry) => (
                  <tr key={entry.id}>
                    <td className="p-2 break-all">{entry.value}</td>
                    <td className="p-2">{entry.kind === "domain" ? "Domain" : "Address"}</td>
                    <td className="p-2 whitespace-nowrap">
                      {formatDate(entry.created_at, timeZone)}
                      {entry.created_by_name ? ` by ${entry.created_by_name}` : ""}
                      {entry.from_item_id ? " · from a dismissal" : ""}
                    </td>
                    <td className="p-2 text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={busy}
                        onClick={() => removeIgnore(entry.id, entry.value)}
                      >
                        Remove
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <form className="flex flex-wrap items-end gap-2" onSubmit={addIgnore}>
          <label className="space-y-1 text-sm">
            <span className="block text-muted-foreground">Kind</span>
            <select
              className="h-8 rounded-md border bg-background px-2 text-sm"
              value={ignoreKind}
              disabled={busy}
              onChange={(e) => setIgnoreKind(e.target.value as CrmCaptureIgnoreIn["kind"])}
            >
              <option value="domain">Domain</option>
              <option value="address">Address</option>
            </select>
          </label>
          <label className="space-y-1 text-sm">
            <span className="block text-muted-foreground">
              {ignoreKind === "domain" ? "Domain" : "Email address"}
            </span>
            <Input
              className="h-8 w-64 max-w-full"
              value={ignoreValue}
              maxLength={400}
              placeholder={ignoreKind === "domain" ? "example.com" : "name@example.com"}
              onChange={(e) => setIgnoreValue(e.target.value)}
              required
            />
          </label>
          <Button type="submit" size="sm" disabled={busy || ignoreValue.trim() === ""}>
            Add
          </Button>
        </form>
      </section>

      {run === null && <p className="text-sm text-muted-foreground">No dry run yet.</p>}

      {run !== null && (
        <section className="space-y-3">
          <h3 className="text-sm font-medium">Latest dry run</h3>
          <p role="status" className="text-sm">
            Started {formatDate(run.started_at, timeZone)}
            {run.finished_at === null
              ? " · still running; the counts below are as they stand."
              : run.cancelled
                ? ` · stopped ${formatDate(run.finished_at, timeZone)} before it finished; the counts are as they stood.`
                : ` · finished ${formatDate(run.finished_at, timeZone)}.`}
            {run.applied_at ? ` Applied ${formatDate(run.applied_at, timeZone)}.` : ""}
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button disabled={busy || !run.applicable} onClick={apply}>
              Apply
            </Button>
            <p className="text-sm text-muted-foreground">
              {run.applicable
                ? "Makes Review Queue items from the high- and medium-confidence findings below, with no new Claude calls, and switches capture to Live."
                : run.applied_at
                  ? "This dry run was applied."
                  : "Only the latest dry run can be applied, once it has finished, within 7 days; a stopped one is run again."}
            </p>
          </div>

          <table className="w-full max-w-md text-sm">
            <tbody className="divide-y">
              <CountRow label="Conversations considered" value={run.counts.considered} />
              <CountRow label="Still to judge" value={run.counts.pending} />
              <CountRow label="Skipped by the rules" value={run.counts.skipped_rules} />
              <CountRow label="Skipped by the ignore list" value={run.counts.ignored} />
              <CountRow label="Filed as known" value={run.counts.known} />
              <CountRow label="Judged by Claude" value={run.counts.judged} />
              <CountRow label="Could not be judged" value={run.counts.failed} />
              <CountRow label="Conversations filed in all" value={run.counts.filed_known_threads} />
              {run.counts.academy > 0 ? (
                <>
                  {/* A dry run routes nothing: only Apply hands enquiries over. */}
                  <CountRow
                    label={
                      run.applied_at
                        ? "Course enquiries sent to the Academy"
                        : "Course enquiries to be sent to the Academy"
                    }
                    value={run.counts.academy - run.counts.academy_unrouted}
                  />
                  <CountRow
                    label="Course enquiries with no Academy workspace"
                    value={run.counts.academy_unrouted}
                  />
                </>
              ) : null}
              <tr className="font-medium">
                <td className="py-1.5">Estimated cost</td>
                <td className="py-1.5 text-right tabular-nums">{usd(run.estimated_cost_usd)}</td>
              </tr>
            </tbody>
          </table>

          <div className="overflow-x-auto">
            <table className="w-full max-w-md text-sm">
              <caption className="pb-1 text-left text-muted-foreground">
                Judged, by outcome and confidence
              </caption>
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="py-1.5 font-normal">Outcome</th>
                  {CONFIDENCES.map((c) => (
                    <th key={c.key} className="py-1.5 text-right font-normal">
                      {c.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y">
                {OUTCOMES.map((o) => (
                  <tr key={o.key}>
                    <td className="py-1.5">{o.label}</td>
                    {CONFIDENCES.map((c) => (
                      <td key={c.key} className="py-1.5 text-right tabular-nums">
                        {run.counts.by_outcome?.[o.key]?.[c.key] ?? 0}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h3 className="pt-2 text-sm font-medium">Leads and deal signals</h3>
          <FindingsTable findings={leads} timeZone={timeZone} empty="None found yet." />

          <h3 className="pt-2 text-sm font-medium">Low confidence</h3>
          <p className="text-sm text-muted-foreground">
            Recorded but never made into a Review Queue item.
          </p>
          <FindingsTable findings={low} timeZone={timeZone} empty="None." />

          <h3 className="pt-2 text-sm font-medium">Recall check</h3>
          <p className="text-sm text-muted-foreground">
            Your conversations labelled Sales or Leads since the start date, each judged as if it
            had no label.
          </p>
          <p className="text-sm">
            {recallReady ? run.recall.line : "Shown once the dry run finishes."}
          </p>
          {recallReady && run.recall.summary && <RecallSummaryList summary={run.recall.summary} />}
          {recallReady && run.recall.rows.length > 0 && (
            <div className="overflow-x-auto rounded-md border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-muted-foreground">
                    <th className="p-2 font-normal">Conversation</th>
                    <th className="p-2 font-normal">Latest</th>
                    <th className="p-2 font-normal">What capture did</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {run.recall.rows.map((r) => (
                    <tr key={r.thread_id}>
                      <td className="p-2 break-all">
                        {r.subject ?? <span className="text-muted-foreground">Not stored</span>}
                      </td>
                      <td className="p-2 whitespace-nowrap">
                        {r.latest_at ? formatDate(r.latest_at, timeZone) : "—"}
                      </td>
                      <td className="p-2">{FATE_TEXT[r.fate]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

/**
 * The Academy's *Accept course enquiries from Labs* switch — spec 010
 * FR-CAP-ACAD-03, contracts §7.4a. On by default; a missing value reads as
 * on. Admins only; the server refuses everyone else.
 */
function RoutingSwitch({ workspace }: { workspace: string }) {
  const { data, error, setError, reload } = useAsync(() => getCrmSettings(workspace), [workspace]);
  const [busy, setBusy] = useState(false);
  const routing = (data?.settings.capture_routing ?? null) as CrmCaptureRouting | null;
  const on = data ? routing?.accept_routed !== false : true;

  async function toggle(next: boolean) {
    setBusy(true);
    setError(null);
    try {
      await updateCrmSetting(workspace, "capture_routing", { accept_routed: next });
      reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-2">
      {error && (
        <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}
      <div className="flex items-start gap-3">
        <input
          id="capture-routing"
          type="checkbox"
          className="mt-1 size-4"
          checked={on}
          disabled={!data || busy}
          onChange={(e) => toggle(e.target.checked)}
        />
        <label htmlFor="capture-routing" className="text-sm">
          <span className="block font-medium">
            Accept course enquiries from Labs: {on ? "on" : "off"}
          </span>
          <span className="text-muted-foreground">
            When Claude finds a course or training enquiry in a Labs mailbox, it is suggested in
            this workspace&apos;s Review Queue instead. Only what came from the email crosses;
            approving it creates the company, the people and a note. Switched off, nothing is sent
            here.
          </span>
        </label>
      </div>
    </section>
  );
}

/** The recall line's numbers as a list; reasons with a zero count are left out (FR-CAP-RPT-08). */
function RecallSummaryList({ summary }: { summary: CrmCaptureRecallSummary }) {
  const reasons = UNTESTED_TEXT.filter(([key]) => summary.untested[key] > 0);
  return (
    <table className="w-full max-w-md text-sm">
      <tbody className="divide-y">
        <CountRow label="Labelled conversations" value={summary.total} />
        <CountRow label="Could be tested" value={summary.tested} />
        <CountRow label="Found" value={summary.found} />
        {summary.academy_unrouted > 0 && (
          <CountRow
            label="Of those, course enquiries the Academy could not take"
            value={summary.academy_unrouted}
          />
        )}
        {reasons.map(([key, label]) => (
          <CountRow key={key} label={`Not tested: ${label}`} value={summary.untested[key]} />
        ))}
      </tbody>
    </table>
  );
}

function CountRow({ label, value }: { label: string; value: number }) {
  return (
    <tr>
      <td className="py-1.5">{label}</td>
      <td className="py-1.5 text-right tabular-nums">{value}</td>
    </tr>
  );
}

function FindingsTable({
  findings,
  timeZone,
  empty,
}: {
  findings: CrmCaptureFinding[];
  timeZone: CrmTimezone;
  empty: string;
}) {
  if (findings.length === 0) return <p className="text-sm text-muted-foreground">{empty}</p>;
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th className="p-2 font-normal">Outcome</th>
            <th className="p-2 font-normal">Company</th>
            <th className="p-2 font-normal">Person</th>
            <th className="p-2 font-normal">Dates</th>
            <th className="p-2 font-normal">Confidence</th>
            <th className="p-2 font-normal">Reason</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {findings.map((f) => (
            <tr key={f.candidate_id} className="align-top">
              <td className="p-2 whitespace-nowrap">
                {f.outcome === "new_lead" ? "New lead" : "Deal signal"}
                {f.business === "academy" && (
                  <span className="block text-xs text-muted-foreground">Course enquiry, for the Academy</span>
                )}
              </td>
              <td className="p-2">
                <span className="block">{f.company_name ?? "—"}</span>
                {f.domain && (
                  <span className="block break-all text-xs text-muted-foreground">
                    {f.domain}
                    {f.domain_from_text ? " (from the text; check it)" : ""}
                  </span>
                )}
              </td>
              <td className="p-2">
                <span className="block">{f.person_name ?? "—"}</span>
                {f.person_email && (
                  <span className="block break-all text-xs text-muted-foreground">
                    {f.person_email}
                  </span>
                )}
              </td>
              <td className="p-2 whitespace-nowrap">
                {f.first_at ? formatDate(f.first_at, timeZone) : "—"}
                {f.latest_at && f.latest_at !== f.first_at
                  ? ` to ${formatDate(f.latest_at, timeZone)}`
                  : ""}
                <span className="block text-xs text-muted-foreground">
                  {f.message_count} {f.message_count === 1 ? "email" : "emails"}
                  {f.owner_replied ? " · you replied" : ""}
                </span>
              </td>
              <td className="p-2">{CONFIDENCE_TEXT[f.confidence]}</td>
              <td className="p-2">
                <span className="inline-flex items-start gap-1.5">
                  <AiMarker ai={REASON_AI} timeZone={timeZone} />
                  <span>{f.reason ?? "—"}</span>
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
