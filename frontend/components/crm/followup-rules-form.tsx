"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { updateCrmSetting } from "@/lib/api/crm";
import type { AutomationSwitches, FlagThresholds } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";

type RuleSwitch = "no_reply_draft" | "stuck_notice" | "lead_cold" | "campaign_followthrough";
type RuleParameter =
  | "stuck_discovery_days"
  | "stuck_qualification_days"
  | "stuck_proposal_days"
  | "stuck_negotiation_days"
  | "no_reply_days"
  | "lead_cold_days";

/** FR-CRMAI-RULE-02 to -06 — each rule, its switch and its parameters. */
const RULES: {
  key: RuleSwitch;
  label: string;
  detail: string;
  parameters: { key: RuleParameter; label: string }[];
}[] = [
  {
    key: "no_reply_draft",
    label: "R-1 No reply after outreach",
    detail:
      "When our latest email to a person or company has had no reply for this many days, the AI drafts a follow-up for the owner to review and send from their own mail. Needs phase 1 on and Gmail sync live. Nothing is ever sent.",
    parameters: [{ key: "no_reply_days", label: "No reply after" }],
  },
  {
    key: "stuck_notice",
    label: "R-2 Stuck in stage",
    detail:
      "An open deal turns amber “Stuck in stage” after this many days in its stage, and red at twice that; its owner is told once per stage entry. Switched off, the flag is not shown.",
    parameters: [
      { key: "stuck_discovery_days", label: "Discovery" },
      { key: "stuck_qualification_days", label: "Qualification" },
      { key: "stuck_proposal_days", label: "Proposal" },
      { key: "stuck_negotiation_days", label: "Negotiation" },
    ],
  },
  {
    key: "lead_cold",
    label: "R-3 Lead gone cold",
    detail:
      "A company in Contacted or Nurturing with no activity for this many days gets a suggestion to move it to Nurturing or Disqualified, or from Nurturing to set a next touch.",
    parameters: [{ key: "lead_cold_days", label: "No activity for" }],
  },
  {
    key: "campaign_followthrough",
    label: "R-4 Campaign follow-through",
    detail:
      "A campaign member Engaged or Meeting booked for more than 7 days, with no deal and no open or waiting task, gets a suggestion to qualify it or close it out.",
    parameters: [],
  },
];

/**
 * Settings, Follow-up rules — spec 009 FR-CRMAI-RULE-01 to -08.
 *
 * The four rule switches live in the `automations` setting and their
 * parameters in `flag_thresholds`, so every save sends the stored value with
 * the change on top: the validators refuse a value missing any key. Rules
 * run only while phase 3 is on. R-1, R-3 and R-4 only put suggestions in the
 * Review Queue; nothing changes until someone approves one. People marked Do
 * not contact are skipped. Admins only; the server refuses everyone else.
 */
export function FollowupRulesForm({
  workspace,
  automations,
  thresholds,
  phaseOn,
  onChanged,
}: {
  workspace: string;
  automations: AutomationSwitches;
  thresholds: FlagThresholds;
  /** Phase 3 (FR-CRMAI-FLAG-01). */
  phaseOn: boolean;
  onChanged: () => void;
}) {
  const [draft, setDraft] = useState<Partial<Record<RuleParameter, string>>>({});
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await work();
      onChanged();
      return true;
    } catch (err) {
      setError(errorMessage(err));
      return false;
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      {error && (
        <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}
      {!phaseOn && (
        <p role="status" className="rounded-md bg-muted p-3 text-sm">
          Phase 3 is off, so no rule runs whatever its switch says. It is switched on under AI.
        </p>
      )}
      <form
        className="space-y-3"
        onSubmit={async (event) => {
          event.preventDefault();
          const edited = Object.fromEntries(
            Object.entries(draft).map(([key, value]) => [key, Number(value)])
          );
          if (await act(() => updateCrmSetting(workspace, "flag_thresholds", { ...thresholds, ...edited }))) {
            setDraft({});
            setSaved(true);
          }
        }}
      >
        <ul className="divide-y">
          {RULES.map(({ key, label, detail, parameters }) => (
            <li key={key} className="space-y-2 py-3">
              <div className="flex items-start gap-3">
                <input
                  id={`rule-${key}`}
                  type="checkbox"
                  className="mt-1 size-4"
                  checked={automations[key] ?? true}
                  disabled={busy}
                  onChange={(e) =>
                    act(() =>
                      updateCrmSetting(workspace, "automations", { ...automations, [key]: e.target.checked })
                    )
                  }
                />
                <label htmlFor={`rule-${key}`} className="text-sm">
                  <span className="block font-medium">
                    {label}: {(automations[key] ?? true) ? "on" : "off"}
                  </span>
                  <span className="text-muted-foreground">{detail}</span>
                </label>
              </div>
              {parameters.length > 0 && (
                <div className="grid max-w-xl gap-3 pl-7 sm:grid-cols-2">
                  {parameters.map((p) => (
                    <label key={p.key} className="space-y-1 text-sm">
                      <span className="block text-muted-foreground">{p.label} (days)</span>
                      <Input
                        type="number"
                        min={1}
                        max={365}
                        step={1}
                        required
                        value={draft[p.key] ?? String(thresholds[p.key] ?? "")}
                        onChange={(e) => setDraft((d) => ({ ...d, [p.key]: e.target.value }))}
                        className="h-8"
                      />
                    </label>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
        <p className="text-sm text-muted-foreground">Whole days from 1 to 365.</p>
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={busy}>
            Save days
          </Button>
          {saved && <span className="text-sm text-muted-foreground">Saved.</span>}
        </div>
      </form>
    </div>
  );
}
