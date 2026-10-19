"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { setCrmAiSetting } from "@/lib/api/crm";
import type { CrmAiOverview, CrmAiPhase, CrmAiPhases, CrmAiSpendJob } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";

/** FR-CRMAI-FLAG-01 — what each phase switches on. */
const PHASES: { key: CrmAiPhase; label: string; detail: string }[] = [
  { key: "1", label: "Phase 1: email summaries", detail: "Summaries of synced email threads, and the Review Queue." },
  { key: "2", label: "Phase 2: meetings and enrichment", detail: "Meeting notes from Fireflies, and company details from their websites." },
  { key: "3", label: "Phase 3: AI fields and follow-up rules", detail: "AI fields on companies and deals, and the follow-up rules." },
  { key: "4", label: "Phase 4: contact recency", detail: "How recently we were in touch with each company and person." },
];

const JOBS: { key: CrmAiSpendJob; label: string }[] = [
  { key: "summaries", label: "Email summaries" },
  { key: "meetings", label: "Meetings" },
  { key: "enrichment", label: "Enrichment" },
  { key: "ai_fields", label: "AI fields" },
  { key: "followup_drafts", label: "Follow-up drafts" },
  { key: "capture", label: "Email capture" },
];

const usd = (amount: string) =>
  `$${Number(amount).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/**
 * Settings, AI — spec 009 FR-CRMAI-LLM-07, -09, FR-CRMAI-FLAG-01, -03.
 *
 * The monthly budget in US dollars, month-to-date spend in total and per job,
 * and the phase switches. Spend is an estimate from token counts and a rate
 * table, so the page says so. The budget and the phases are one setting, so
 * every save sends both. Admins only; the server refuses everyone else.
 */
export function AiSettings({
  workspace,
  overview,
  onChanged,
}: {
  workspace: string;
  overview: CrmAiOverview;
  onChanged: () => void;
}) {
  const [budget, setBudget] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(budgetUsd: number, phases: CrmAiPhases) {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await setCrmAiSetting(workspace, { budget_usd: budgetUsd, phases });
      setSaved(true);
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      {error && (
        <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}
      {!overview.rates_configured && (
        <p role="status" className="rounded-md bg-muted p-3 text-sm">
          The AI rates are not set yet, so no AI job runs whatever the budget and phases say.
          They are set on the server.
        </p>
      )}

      <section className="space-y-3">
        <h3 className="text-sm font-medium">Spend in {overview.month} (estimate)</h3>
        <p className="text-sm text-muted-foreground">{overview.estimate_note}</p>
        <table className="w-full max-w-md text-sm">
          <tbody className="divide-y">
            {JOBS.map(({ key, label }) => (
              <tr key={key}>
                <td className="py-1.5">{label}</td>
                <td className="py-1.5 text-right tabular-nums">{usd(overview.spend.by_job[key] ?? "0")}</td>
              </tr>
            ))}
            <tr className="font-medium">
              <td className="py-1.5">Total</td>
              <td className="py-1.5 text-right tabular-nums">
                {usd(overview.spend.total_usd)} of {usd(overview.budget_usd)}
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          if (budget === null) return;
          await save(Number(budget), overview.phases);
          setBudget(null);
        }}
      >
        <div className="space-y-1.5">
          <Label htmlFor="ai-budget">Monthly budget (USD)</Label>
          <Input
            id="ai-budget"
            inputMode="decimal"
            pattern="[0-9]+(\.[0-9]{1,2})?"
            value={budget ?? overview.budget_usd}
            onChange={(e) => setBudget(e.target.value)}
            className="h-8 w-32"
            required
          />
        </div>
        <Button type="submit" disabled={busy || budget === null}>
          {busy ? "Saving…" : "Save budget"}
        </Button>
        {saved && <span className="text-sm text-muted-foreground">Saved.</span>}
      </form>
      <p className="text-sm text-muted-foreground">
        From 0 to 10000 US dollars. At zero no AI work runs. When month-to-date spend reaches the
        budget, AI jobs stop until it is raised or the month turns (Asia/Kolkata months).
      </p>

      <section className="space-y-2">
        <h3 className="text-sm font-medium">Phases</h3>
        <p className="text-sm text-muted-foreground">
          Switched off, a phase&apos;s jobs stop at their next run and its output is hidden; what was
          stored returns when it is switched on again.
        </p>
        <ul className="divide-y">
          {PHASES.map(({ key, label, detail }) => (
            <li key={key} className="flex items-start gap-3 py-2">
              <input
                id={`ai-phase-${key}`}
                type="checkbox"
                className="mt-1 size-4"
                checked={overview.phases[key]}
                disabled={busy}
                onChange={(e) =>
                  save(Number(overview.budget_usd), { ...overview.phases, [key]: e.target.checked })
                }
              />
              <label htmlFor={`ai-phase-${key}`} className="text-sm">
                <span className="block font-medium">
                  {label}: {overview.phases[key] ? "on" : "off"}
                </span>
                <span className="text-muted-foreground">{detail}</span>
              </label>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
