"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { AiBudgetBanner } from "@/components/crm/ai-budget-banner";
import { AiFieldsEditor } from "@/components/crm/ai-fields-editor";
import { AiSettings } from "@/components/crm/ai-settings";
import { AuditTable } from "@/components/crm/audit-table";
import { FollowupRulesForm } from "@/components/crm/followup-rules-form";
import { FxRatesForm } from "@/components/crm/fx-rates-form";
import { InviteDialog } from "@/components/crm/invite-dialog";
import { JobsTable } from "@/components/crm/jobs-table";
import { ListEditor } from "@/components/crm/list-editor";
import { MembersTable } from "@/components/crm/members-table";
import { useCrm } from "@/components/crm/shell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  getCrmAudit,
  getCrmFxRates,
  addCrmMailbox,
  getCrmAi,
  listCrmAiFields,
  getCrmJobs,
  getCrmLists,
  getCrmMailboxes,
  getCrmMembers,
  getCrmSettings,
  getCrmTargets,
  inviteCrmMember,
  removeCrmMailbox,
  removeCrmMember,
  runCrmEmailSync,
  setCrmTargets,
  updateCrmMailbox,
  updateCrmMember,
  updateCrmSetting,
} from "@/lib/api/crm";
import type {
  AutomationSwitches,
  CrmAuditEntry,
  CrmCaptureSource,
  CrmJobName,
  CrmMailbox,
  CrmTargetIn,
  EmailSyncResult,
  FlagThresholds,
  StageKey,
  StageProbabilities,
} from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { formatDate, todayIn } from "@/lib/crm/format";
import { useAsync } from "@/lib/use-async";

const STAGES: StageKey[] = [
  "discovery",
  "qualification",
  "proposal",
  "negotiation",
  "closed_won",
  "closed_lost",
];

const TABS = [
  "members",
  "lists",
  "probabilities",
  "fx",
  "domains",
  "thresholds",
  "automations",
  "targets",
  "mailboxes",
  "jobs",
  "ai",
  "ai-fields",
  "followup-rules",
  "audit",
];

/**
 * Workspace settings — spec 007 FR-CRM-LIST-06, FR-CRM-ACC-04 to -12,
 * FR-CRM-AUD-03; from 1b the flag thresholds (FR-CRM-FLAG-01), the
 * automation switches (FR-CRM-AUTO-02), the Gmail mailboxes and the sync
 * switch (FR-CRM-SYNC-01, -03), the job log (FR-CRM-JOB-01) and the
 * dashboard's bookings targets (FR-CRM-LIST-06, FR-CRM-DASH-02); from 009
 * the AI budget, spend and phase switches (FR-CRMAI-LLM-09, FR-CRMAI-FLAG-01),
 * the AI field definitions (FR-CRMAI-FIELD-01) and the follow-up rules
 * (FR-CRMAI-RULE-01).
 * Admins only; the server refuses everyone else whatever this page shows.
 */
export default function SettingsPage() {
  const { bootstrap } = useCrm();
  const ws = bootstrap.workspace.key;
  // The late-jobs banner links to ?tab=jobs.
  const asked = useSearchParams().get("tab");
  const [tab, setTab] = useState(asked && TABS.includes(asked) ? asked : "members");

  if (bootstrap.role !== "admin") {
    return (
      <p className="text-sm text-muted-foreground">Only a workspace admin can see settings.</p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="flex-1 font-heading text-lg font-semibold">Settings</h1>
        {/* FR-CRM-DEL-01: deleted records, restorable for 90 days. */}
        <Button variant="outline" size="sm" nativeButton={false} render={<Link href={`/crm/${ws}/settings/trash`} />}>
          Trash
        </Button>
        {/* FR-CRM-MIG-01: the Twenty importer; FR-CRM-IO-01: CSV import. */}
        <Button variant="outline" size="sm" nativeButton={false} render={<Link href={`/crm/${ws}/settings/import`} />}>
          Import
        </Button>
      </div>
      <Tabs value={tab} onValueChange={(value) => setTab(String(value))}>
        <TabsList className="max-w-full overflow-x-auto">
          <TabsTrigger value="members">Members</TabsTrigger>
          <TabsTrigger value="lists">Lists</TabsTrigger>
          <TabsTrigger value="probabilities">Probabilities</TabsTrigger>
          <TabsTrigger value="fx">FX rates</TabsTrigger>
          <TabsTrigger value="domains">Allowed domains</TabsTrigger>
          <TabsTrigger value="thresholds">Thresholds</TabsTrigger>
          <TabsTrigger value="automations">Automations</TabsTrigger>
          <TabsTrigger value="targets">Targets</TabsTrigger>
          <TabsTrigger value="mailboxes">Mailboxes</TabsTrigger>
          <TabsTrigger value="jobs">Jobs</TabsTrigger>
          <TabsTrigger value="ai">AI</TabsTrigger>
          <TabsTrigger value="ai-fields">AI fields</TabsTrigger>
          <TabsTrigger value="followup-rules">Follow-up rules</TabsTrigger>
          <TabsTrigger value="audit">Audit</TabsTrigger>
        </TabsList>
        <TabsContent value="members" className="pt-4">
          <MembersTab workspace={ws} />
        </TabsContent>
        <TabsContent value="lists" className="pt-4">
          <ListsTab workspace={ws} />
        </TabsContent>
        <TabsContent value="probabilities" className="pt-4">
          <ProbabilitiesTab workspace={ws} />
        </TabsContent>
        <TabsContent value="fx" className="pt-4">
          <FxTab workspace={ws} />
        </TabsContent>
        <TabsContent value="domains" className="pt-4">
          <DomainsTab workspace={ws} />
        </TabsContent>
        <TabsContent value="thresholds" className="pt-4">
          <ThresholdsTab workspace={ws} />
        </TabsContent>
        <TabsContent value="automations" className="pt-4">
          <AutomationsTab workspace={ws} />
        </TabsContent>
        <TabsContent value="targets" className="pt-4">
          <TargetsTab workspace={ws} />
        </TabsContent>
        <TabsContent value="mailboxes" className="pt-4">
          <MailboxesTab workspace={ws} />
        </TabsContent>
        <TabsContent value="jobs" className="pt-4">
          <JobsTab workspace={ws} onShowAi={() => setTab("ai")} />
        </TabsContent>
        <TabsContent value="ai" className="pt-4">
          <AiTab workspace={ws} />
        </TabsContent>
        <TabsContent value="ai-fields" className="pt-4">
          <AiFieldsTab workspace={ws} />
        </TabsContent>
        <TabsContent value="followup-rules" className="pt-4">
          <FollowupRulesTab workspace={ws} />
        </TabsContent>
        <TabsContent value="audit" className="pt-4">
          <AuditTab workspace={ws} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function Problem({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
      {message}
    </div>
  );
}

function MembersTab({ workspace }: { workspace: string }) {
  const { bootstrap } = useCrm();
  const { data, error, setError, reload } = useAsync(
    () => Promise.all([getCrmMembers(workspace), getCrmSettings(workspace)]),
    [workspace]
  );
  const [inviting, setInviting] = useState(false);
  const [members, settings] = data ?? [[], null];

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex-1">
            <CardTitle className="text-base">Members</CardTitle>
            <CardDescription>
              Removing someone ends their access at once. A CRM-only person with no workspace left
              is signed out for good, until someone invites them again.
            </CardDescription>
          </div>
          <Button onClick={() => setInviting(true)}>Invite</Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 p-0">
        <div className="px-4">
          <Problem message={error} />
        </div>
        <MembersTable
          members={members}
          onRoleChange={async (member, role) => {
            try {
              await updateCrmMember(workspace, member.membership_id, role);
              reload();
            } catch (err) {
              setError(errorMessage(err));
            }
          }}
          onRemove={async (member) => {
            if (!window.confirm(`Remove ${member.display_name} from ${bootstrap.workspace.name}?`)) return;
            try {
              await removeCrmMember(workspace, member.membership_id);
              reload();
            } catch (err) {
              setError(errorMessage(err));
            }
          }}
        />
      </CardContent>
      <InviteDialog
        open={inviting}
        onOpenChange={setInviting}
        workspaceName={bootstrap.workspace.name}
        allowedDomains={settings?.allowed_domains ?? []}
        onInvite={(invitation) => inviteCrmMember(workspace, invitation)}
        onDone={reload}
      />
    </Card>
  );
}

function ListsTab({ workspace }: { workspace: string }) {
  const { reload: reloadShell } = useCrm();
  const { data, error, reload } = useAsync(() => getCrmLists(workspace), [workspace]);
  return (
    <div className="space-y-3">
      <Problem message={error} />
      {data && (
        <ListEditor
          workspace={workspace}
          lists={data}
          onChanged={() => {
            reload();
            reloadShell();
          }}
        />
      )}
    </div>
  );
}

function ProbabilitiesTab({ workspace }: { workspace: string }) {
  const { bootstrap } = useCrm();
  const { data, error, setError, reload } = useAsync(() => getCrmSettings(workspace), [workspace]);
  const [draft, setDraft] = useState<Partial<Record<StageKey, string>>>({});
  const [saved, setSaved] = useState(false);
  const current = (data?.settings.stage_probabilities ?? {}) as Partial<StageProbabilities>;
  const label = (key: StageKey) =>
    bootstrap.lists["opportunity.stage"]?.find((o) => o.system_key === key)?.label ?? key;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Default probability by stage</CardTitle>
        <CardDescription>
          Whole numbers from 0 to 100. A deal takes its stage&apos;s default unless someone sets
          its own.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-3"
          onSubmit={async (event) => {
            event.preventDefault();
            setError(null);
            setSaved(false);
            const value = Object.fromEntries(
              STAGES.map((key) => [key, Number(draft[key] ?? current[key] ?? 0)])
            );
            try {
              await updateCrmSetting(workspace, "stage_probabilities", value);
              setDraft({});
              setSaved(true);
              reload();
            } catch (err) {
              setError(errorMessage(err));
            }
          }}
        >
          <Problem message={error} />
          <div className="grid max-w-md grid-cols-2 gap-3">
            {STAGES.map((key) => (
              <label key={key} className="space-y-1 text-sm">
                <span className="block text-muted-foreground">{label(key)}</span>
                <Input
                  type="number"
                  min={0}
                  max={100}
                  step={1}
                  value={draft[key] ?? String(current[key] ?? "")}
                  onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                  className="h-8"
                />
              </label>
            ))}
          </div>
          <div className="flex items-center gap-3">
            <Button type="submit">Save</Button>
            {saved && <span className="text-sm text-muted-foreground">Saved.</span>}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function FxTab({ workspace }: { workspace: string }) {
  const { data, error, reload } = useAsync(() => getCrmFxRates(workspace), [workspace]);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">FX rates</CardTitle>
        <CardDescription>
          The rate for a deal&apos;s month converts its amount to INR when it is saved.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Problem message={error} />
        <FxRatesForm workspace={workspace} rates={data ?? []} onChanged={reload} />
      </CardContent>
    </Card>
  );
}

const PERIOD_LABEL: Record<CrmTargetIn["period_kind"], string> = {
  month: "Month",
  quarter: "Quarter",
  fy: "Financial year",
};

/** The first day of the period the date falls in (FY April–March). */
function periodStart(kind: CrmTargetIn["period_kind"], day: string): string {
  const [y, m] = day.split("-").map(Number);
  if (kind === "month") return `${y}-${String(m).padStart(2, "0")}-01`;
  if (kind === "quarter") return `${y}-${String(Math.floor((m - 1) / 3) * 3 + 1).padStart(2, "0")}-01`;
  return `${m >= 4 ? y : y - 1}-04-01`;
}

/**
 * Bookings targets — FR-CRM-LIST-06, FR-CRM-DASH-02: what the workspace (or,
 * in Labs, one entity) means to book in a month, a quarter or a financial
 * year, in INR. The dashboard colours bookings only against one of these.
 */
function TargetsTab({ workspace }: { workspace: string }) {
  const { bootstrap } = useCrm();
  const { data, error, setError, reload } = useAsync(() => getCrmTargets(workspace), [workspace]);
  const [kind, setKind] = useState<CrmTargetIn["period_kind"]>("month");
  const [day, setDay] = useState("");
  const [entity, setEntity] = useState<string>("");
  const [amount, setAmount] = useState("");
  // PUT replaces the whole set, so a save waits for the set the server holds.
  const loaded = data !== null;
  const targets: CrmTargetIn[] = (data ?? []).map((t) => ({
    entity: t.entity,
    period_kind: t.period_kind,
    period_start: t.period_start,
    amount_inr: t.amount_inr,
  }));

  async function save(next: CrmTargetIn[]) {
    setError(null);
    try {
      await setCrmTargets(workspace, next);
      reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Bookings targets</CardTitle>
        <CardDescription>
          In INR. The dashboard shows bookings green, amber or red against the target&apos;s pace; with no
          target it shows no colour. The financial year runs April to March.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Problem message={error} />
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={async (event) => {
            event.preventDefault();
            if (!loaded || !day || !amount) return;
            const start = periodStart(kind, day);
            const added: CrmTargetIn = {
              entity: (entity || null) as CrmTargetIn["entity"],
              period_kind: kind,
              period_start: start,
              amount_inr: amount,
            };
            const others = targets.filter(
              (t) => !(t.period_kind === kind && t.period_start === start && (t.entity ?? "") === entity)
            );
            await save([...others, added]);
            setAmount("");
          }}
        >
          <label className="space-y-1 text-sm">
            <span className="block text-muted-foreground">Period</span>
            <select className="h-8 rounded-md border px-2" value={kind} onChange={(e) => setKind(e.target.value as CrmTargetIn["period_kind"])}>
              {(Object.keys(PERIOD_LABEL) as CrmTargetIn["period_kind"][]).map((k) => (
                <option key={k} value={k}>
                  {PERIOD_LABEL[k]}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-sm">
            <span className="block text-muted-foreground">Any day in it</span>
            <Input type="date" className="h-8" value={day} onChange={(e) => setDay(e.target.value)} required />
          </label>
          {bootstrap.workspace.has_entity && (
            <label className="space-y-1 text-sm">
              <span className="block text-muted-foreground">For</span>
              <select className="h-8 rounded-md border px-2" value={entity} onChange={(e) => setEntity(e.target.value)}>
                <option value="">The whole workspace</option>
                <option value="nunnari_labs">Nunnari Labs</option>
                <option value="deepweaver">DeepWeaver</option>
              </select>
            </label>
          )}
          <label className="space-y-1 text-sm">
            <span className="block text-muted-foreground">Target (INR)</span>
            <Input
              inputMode="decimal"
              className="h-8 w-40"
              value={amount}
              onChange={(e) => setAmount(e.target.value.replace(/[^0-9.]/g, ""))}
              required
            />
          </label>
          <Button type="submit" disabled={!loaded}>
            Set target
          </Button>
        </form>
        {data && data.length === 0 && <p className="text-sm text-muted-foreground">No targets yet.</p>}
        {data && data.length > 0 && (
          <ul className="divide-y rounded-md border text-sm">
            {data.map((t) => (
              <li key={t.id} className="flex flex-wrap items-center gap-3 p-2">
                <span className="flex-1">
                  {PERIOD_LABEL[t.period_kind]} from {formatDate(t.period_start, bootstrap.me.timezone)} to{" "}
                  {formatDate(t.period_end, bootstrap.me.timezone)}
                  {t.entity_label ? ` · ${t.entity_label}` : ""}
                </span>
                <span className="tabular-nums">₹{Number(t.amount_inr).toLocaleString("en-IN")}</span>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => save(targets.filter((o) => !(o.period_kind === t.period_kind && o.period_start === t.period_start && o.entity === t.entity)))}
                >
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function DomainsTab({ workspace }: { workspace: string }) {
  const { data, error } = useAsync(() => getCrmSettings(workspace), [workspace]);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Allowed domains</CardTitle>
        <CardDescription>
          Only addresses at these domains can be invited here. A TVA admin changes this list.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <Problem message={error} />
        {data && data.allowed_domains.length === 0 && (
          <p className="text-sm text-muted-foreground">None yet.</p>
        )}
        <ul className="list-inside list-disc text-sm">
          {data?.allowed_domains.map((d) => <li key={d}>{d}</li>)}
        </ul>
      </CardContent>
    </Card>
  );
}

function AuditTab({ workspace }: { workspace: string }) {
  const { bootstrap } = useCrm();
  const [before, setBefore] = useState<number | undefined>(undefined);
  const { data, error } = useAsync<CrmAuditEntry[]>(
    () => getCrmAudit(workspace, { limit: 50, before }),
    [workspace, before]
  );
  const rows = data ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Audit</CardTitle>
        <CardDescription>Every change in this workspace: who, when and what.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 p-0 pb-4">
        <div className="px-4">
          <Problem message={error} />
        </div>
        <AuditTable
          timeZone={bootstrap.me.timezone}
          rows={rows.map((r) => ({
            id: r.id,
            at: r.at,
            actor_name: r.actor_name,
            action: r.action,
            subject: r.record_type,
            detail: r.after ? JSON.stringify(r.after) : null,
          }))}
          onOlder={rows.length === 50 ? () => setBefore(rows[rows.length - 1].id) : undefined}
        />
      </CardContent>
    </Card>
  );
}

/** FR-CRM-FLAG-01 — spec §6.2's thresholds, per workspace. */
const THRESHOLDS: { key: keyof FlagThresholds; label: string; unit: string }[] = [
  { key: "reply_owed_amber_hours", label: "Reply owed: amber after", unit: "hours" },
  { key: "reply_owed_red_hours", label: "Reply owed: red after", unit: "hours" },
  { key: "silent_amber_days", label: "Client silent: amber after", unit: "days" },
  { key: "silent_red_days", label: "Client silent: red after", unit: "days" },
  { key: "close_amber_days", label: "Close date: amber within", unit: "days" },
  { key: "stale_amber_days", label: "Stale deal: amber after", unit: "days" },
  { key: "stale_red_days", label: "Stale deal: red after", unit: "days" },
];

function ThresholdsTab({ workspace }: { workspace: string }) {
  const { data, error, setError, reload } = useAsync(() => getCrmSettings(workspace), [workspace]);
  const [draft, setDraft] = useState<Partial<Record<keyof FlagThresholds, string>>>({});
  const [saved, setSaved] = useState(false);
  const current = (data?.settings.flag_thresholds ?? {}) as Partial<FlagThresholds>;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Follow-up and deal-health thresholds</CardTitle>
        <CardDescription>
          When a flag turns amber and red. Next step and task flags follow their dates; a company
          in Nurturing with no open task and no next touch is always a loose end. Flags are
          recomputed on every change and every 30 minutes.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-3"
          onSubmit={async (event) => {
            event.preventDefault();
            setError(null);
            setSaved(false);
            // Keys this tab does not show (009's rule parameters) are sent back as stored.
            const value = {
              ...current,
              ...Object.fromEntries(
                THRESHOLDS.map(({ key }) => [key, Number(draft[key] ?? current[key] ?? 0)])
              ),
            };
            try {
              await updateCrmSetting(workspace, "flag_thresholds", value);
              setDraft({});
              setSaved(true);
              reload();
            } catch (err) {
              setError(errorMessage(err));
            }
          }}
        >
          <Problem message={error} />
          <div className="grid max-w-xl gap-3 sm:grid-cols-2">
            {THRESHOLDS.map(({ key, label, unit }) => (
              <label key={key} className="space-y-1 text-sm">
                <span className="block text-muted-foreground">
                  {label} ({unit})
                </span>
                <Input
                  type="number"
                  min={0}
                  step={1}
                  value={draft[key] ?? String(current[key] ?? "")}
                  onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                  className="h-8"
                />
              </label>
            ))}
          </div>
          <div className="flex items-center gap-3">
            <Button type="submit">Save</Button>
            {saved && <span className="text-sm text-muted-foreground">Saved.</span>}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

/** FR-CRM-AUTO-02 — automations 2, 5, 6, 7 and 8. The rest are the pipeline
 *  rules themselves and cannot be switched off. */
const AUTOMATIONS: { key: keyof AutomationSwitches; label: string; detail: string }[] = [
  {
    key: "email_records",
    label: "Companies and people from synced email",
    detail: "Unknown senders on labelled mail become companies and people (with email sync).",
  },
  {
    key: "stale_notice",
    label: "Stale-deal notice",
    detail: "Tells a deal's owner when it goes quiet, and the admins at red.",
  },
  {
    key: "digests",
    label: "Daily and weekly digests",
    detail: "Each person's Today at 09:00 their time; admins' week on Mondays.",
  },
  {
    key: "task_reminders",
    label: "Task due reminders",
    detail: "In the bell on the due date, and a message once it is overdue.",
  },
  {
    key: "campaign_conversion",
    label: "Campaign conversion prompt",
    detail: "Converting a campaign member prompts for a deal and records the campaign.",
  },
];

function AutomationsTab({ workspace }: { workspace: string }) {
  const { data, error, setError, reload } = useAsync(() => getCrmSettings(workspace), [workspace]);
  const current = (data?.settings.automations ?? null) as AutomationSwitches | null;

  async function toggle(key: keyof AutomationSwitches, on: boolean) {
    if (!current) return;
    setError(null);
    try {
      await updateCrmSetting(workspace, "automations", { ...current, [key]: on });
      reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Automations</CardTitle>
        <CardDescription>
          Switched off, an automation sends nothing; its runs are still logged under Jobs. Stage
          rules, the TVA lifecycle, follow-up flags and the Closed Lost and Closed Won tasks are
          part of the pipeline and are always on.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Problem message={error} />
        <ul className="divide-y">
          {AUTOMATIONS.map(({ key, label, detail }) => (
            <li key={key} className="flex items-start gap-3 py-2">
              <input
                id={`automation-${key}`}
                type="checkbox"
                className="mt-1 size-4"
                checked={current?.[key] ?? true}
                disabled={!current}
                onChange={(e) => toggle(key, e.target.checked)}
              />
              <label htmlFor={`automation-${key}`} className="text-sm">
                <span className="block font-medium">
                  {label}: {(current?.[key] ?? true) ? "on" : "off"}
                </span>
                <span className="text-muted-foreground">{detail}</span>
              </label>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function JobsTab({ workspace, onShowAi }: { workspace: string; onShowAi: () => void }) {
  const { bootstrap } = useCrm();
  const [job, setJob] = useState<CrmJobName | "">("");
  const { data, error } = useAsync(
    () => getCrmJobs(workspace, job || undefined),
    [workspace, job]
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Jobs</CardTitle>
        <CardDescription>
          Every background run in this workspace. Three failures in a row alert the admins; a job
          more than twice its interval late shows a banner on every page.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 p-0 pb-4">
        <div className="space-y-3 px-4">
          <Problem message={error} />
          {/* FR-CRMAI-LLM-08: AI runs are skipped while the budget is reached. */}
          <AiBudgetBanner reached={bootstrap.ai_budget_reached ?? false} onShowSettings={onShowAi} />
          {data && data.stale_jobs.length > 0 && (
            <p role="status" className="text-sm font-medium">
              Late: {data.stale_jobs.map((s) => s.label).join(", ")}.
            </p>
          )}
          <label className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">Job</span>
            <select
              className="h-8 rounded-md border bg-background px-2 text-sm"
              value={job}
              onChange={(e) => setJob(e.target.value as CrmJobName | "")}
            >
              <option value="">All</option>
              {data?.jobs.map((j) => (
                <option key={j.job} value={j.job}>
                  {j.label} (every {j.every_minutes >= 1440 ? "day" : `${j.every_minutes} min`})
                </option>
              ))}
            </select>
          </label>
        </div>
        <JobsTable runs={data?.runs ?? []} timeZone={bootstrap.me.timezone} />
      </CardContent>
    </Card>
  );
}

/** 009 FR-CRMAI-LLM-09 — the budget, month-to-date spend and phase switches. */
function AiTab({ workspace }: { workspace: string }) {
  const { reload: reloadShell } = useCrm();
  const { data, error, reload } = useAsync(() => getCrmAi(workspace), [workspace]);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">AI</CardTitle>
        <CardDescription>
          The monthly AI budget, this month&apos;s estimated spend and the switch for each phase.
          Every phase starts off and the budget at zero, so nothing runs until you set both.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Problem message={error} />
        {data && <AiBudgetBanner reached={data.budget_reached} />}
        {data && (
          <AiSettings
            workspace={workspace}
            overview={data}
            onChanged={() => {
              reload();
              // The phases and the budget banner travel in bootstrap.
              reloadShell();
            }}
          />
        )}
      </CardContent>
    </Card>
  );
}

/** 009 FR-CRMAI-FIELD-01 — the AI field definitions, the four seeds among them. */
function AiFieldsTab({ workspace }: { workspace: string }) {
  const { data, error, reload } = useAsync(() => listCrmAiFields(workspace), [workspace]);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">AI fields</CardTitle>
        <CardDescription>
          Values the AI computes for each company or deal while phase 3 is on, shown read-only on
          record pages only. They never change a stage, probability, flag or colour.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Problem message={error} />
        {data && <AiFieldsEditor workspace={workspace} fields={data} onChanged={reload} />}
      </CardContent>
    </Card>
  );
}

/** 009 FR-CRMAI-RULE-01 — the four follow-up rules' switches and parameters. */
function FollowupRulesTab({ workspace }: { workspace: string }) {
  const { bootstrap } = useCrm();
  const { data, error, reload } = useAsync(() => getCrmSettings(workspace), [workspace]);
  const automations = (data?.settings.automations ?? null) as AutomationSwitches | null;
  const thresholds = (data?.settings.flag_thresholds ?? null) as FlagThresholds | null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Follow-up rules</CardTitle>
        <CardDescription>
          Four rules that watch for follow-ups going missing, each with its own switch, all off to
          start. Their suggestions wait in the Review Queue: no rule sends an email or changes a
          record by itself.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Problem message={error} />
        {automations && thresholds && (
          <FollowupRulesForm
            workspace={workspace}
            automations={automations}
            thresholds={thresholds}
            phaseOn={bootstrap.ai.phases["3"]}
            onChanged={reload}
          />
        )}
      </CardContent>
    </Card>
  );
}

function labelsOf(text: string): string[] {
  return text
    .split(",")
    .map((label) => label.trim())
    .filter(Boolean);
}

function syncReport(result: EmailSyncResult): string {
  if ("skipped" in result) return `${result.skipped}.`;
  if (result.outcome === "running") {
    return "The sync is still running; its counts will show under Jobs.";
  }
  if (result.outcome === "failed") return "The sync failed; see Jobs for why.";
  return (
    `Read ${result.mailboxes} mailbox${result.mailboxes === 1 ? "" : "es"}: ` +
    `${result.stored} new message${result.stored === 1 ? "" : "s"} stored, ` +
    `${result.removed} removed, ${result.matched_people} people matched, ` +
    `${result.created_companies} companies and ${result.created_people} people created.`
  );
}

/** Spec 010 FR-CAP-SRC-01: the two capture sources, as Settings names them. */
const CAPTURE_SOURCES: { value: CrmCaptureSource; label: string }[] = [
  { value: "labels", label: "Labels only" },
  { value: "primary_sent", label: "Primary and Sent" },
];

/** FR-CAP-SRC-07: the default capture start date. */
const CAPTURE_START_DEFAULT = "2026-04-01";

/**
 * FR-CRM-SYNC-01 to -04 — the Gmail mailboxes this workspace syncs, their
 * owners (members of the workspace) and labels; the sync switch, which stays
 * off until the service-account credential exists (Q-23); and Run now
 * (Admins only, Q-62). Only subjects and participants are stored (Q-29).
 *
 * Spec 010 FR-CAP-SRC-01, -07: each mailbox's capture source (Labels only,
 * or Primary and Sent from a start date). Changing either reads the mailbox
 * afresh, so the change is confirmed first.
 */
function MailboxesTab({ workspace }: { workspace: string }) {
  const { bootstrap, reload: reloadShell } = useCrm();
  const { data, error, setError, reload } = useAsync(
    () => Promise.all([getCrmMailboxes(workspace), getCrmMembers(workspace)]),
    [workspace]
  );
  const [email, setEmail] = useState("");
  const [ownerId, setOwnerId] = useState("");
  const [labels, setLabels] = useState("");
  const [source, setSource] = useState<CrmCaptureSource>("labels");
  const [startDate, setStartDate] = useState(CAPTURE_START_DEFAULT);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<string | null>(null);
  const [mailboxes, members] = data ?? [null, []];
  const sync = mailboxes?.sync;

  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await work();
      reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function add(event: React.FormEvent) {
    event.preventDefault();
    await act(async () => {
      await addCrmMailbox(workspace, {
        email: email.trim(),
        owner_id: ownerId || bootstrap.me.id,
        labels: labelsOf(labels),
        capture_source: source,
        ...(source === "primary_sent" ? { capture_start_date: startDate } : {}),
      });
      setEmail("");
      setLabels("");
      setSource("labels");
      setStartDate(CAPTURE_START_DEFAULT);
    });
  }

  async function changeSource(mailbox: CrmMailbox, next: CrmCaptureSource) {
    if (next === mailbox.capture_source) return;
    const from = formatDate(mailbox.capture_start_date, bootstrap.me.timezone);
    const question =
      next === "primary_sent"
        ? `Capture Primary and Sent mail from known contacts for ${mailbox.email}? ` +
          `The mailbox is read afresh from ${from}.`
        : `Go back to Labels only for ${mailbox.email}? ` +
          "Captured mail that carries none of its labels is removed on the next sync.";
    if (!window.confirm(question)) return;
    await act(() => updateCrmMailbox(workspace, mailbox.id, { capture_source: next }));
  }

  async function changeStart(mailbox: CrmMailbox, input: HTMLInputElement) {
    const next = input.value;
    if (next === mailbox.capture_start_date) return;
    if (
      !next ||
      !window.confirm(`Read ${mailbox.email} afresh from ${formatDate(next, bootstrap.me.timezone)}?`)
    ) {
      input.value = mailbox.capture_start_date;
      return;
    }
    await act(() => updateCrmMailbox(workspace, mailbox.id, { capture_start_date: next }));
  }

  const today = todayIn(bootstrap.me.timezone);

  async function editLabels(mailbox: CrmMailbox) {
    const text = window.prompt(
      `Labels for ${mailbox.email}, separated by commas. Changing them reads the labels afresh.`,
      mailbox.labels.join(", ")
    );
    if (text === null) return;
    await act(() => updateCrmMailbox(workspace, mailbox.id, { labels: labelsOf(text) }));
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Gmail sync</CardTitle>
          <CardDescription>
            Every 15 minutes, threads carrying a mailbox&apos;s labels land on the right people and
            companies: subject, participants, direction and date, never the message itself. The
            first run reads the last 90 days. Email among our own domains only is skipped. A
            mailbox on Primary and Sent also files Primary and Sent mail from people and
            companies already in the CRM, with no label, from its start date; newsletters and
            automated mail are skipped, and an SOW or proposal to a client with an open deal
            becomes a deal signal in Review.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Problem message={error} />
          {sync && !sync.configured && (
            <p role="status" className="rounded-md bg-muted p-3 text-sm">
              The Gmail service-account credential is not configured yet, so sync cannot be
              switched on. A TVA admin sets it up on the sync worker.
            </p>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4"
                checked={sync?.enabled ?? false}
                disabled={!sync || busy || (!sync.configured && !sync.enabled)}
                onChange={(e) =>
                  act(async () => {
                    await updateCrmSetting(workspace, "email_sync", { enabled: e.target.checked });
                    reloadShell();
                  })
                }
              />
              <span>Email sync {sync?.enabled ? "on" : "off"}</span>
            </label>
            <Button
              variant="outline"
              size="sm"
              disabled={!sync?.live || busy}
              onClick={() =>
                act(async () => {
                  setReport(null);
                  setReport(syncReport(await runCrmEmailSync(workspace)));
                })
              }
            >
              Run now
            </Button>
            {report && (
              <span role="status" className="text-sm text-muted-foreground">
                {report}
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Mailboxes</CardTitle>
          <CardDescription>
            A mailbox is its owner&apos;s own address at{" "}
            {(mailboxes?.gmail_domains ?? []).join(", ")}; only a TVA admin can add another
            (such as a shared address). Its owner must be a member here and owns what the sync
            creates. Name your own Gmail labels (for example Sales); Inbox, Sent and the
            categories are refused. An address belongs to one workspace only.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {mailboxes && mailboxes.mailboxes.length === 0 && (
            <p className="text-sm text-muted-foreground">No mailbox yet.</p>
          )}
          {mailboxes && mailboxes.mailboxes.length > 0 && (
            <ul className="divide-y rounded-md border">
              {mailboxes.mailboxes.map((m) => (
                <li key={m.id} className="flex flex-wrap items-start justify-between gap-2 p-3">
                  <div className="min-w-0 space-y-0.5 text-sm">
                    <p className="break-all font-medium">{m.email}</p>
                    <p className="text-muted-foreground">
                      Owner {m.owner_name ?? "—"} · Labels {m.labels.join(", ")}
                      {m.is_active ? "" : " · Paused"}
                    </p>
                    <div className="flex flex-wrap items-center gap-2 pt-1">
                      <label className="flex items-center gap-1.5">
                        <span className="text-muted-foreground">Capture</span>
                        <select
                          aria-label={`Capture source for ${m.email}`}
                          className="h-7 rounded-md border bg-background px-2 text-sm"
                          value={m.capture_source}
                          disabled={busy}
                          onChange={(e) => changeSource(m, e.target.value as CrmCaptureSource)}
                        >
                          {CAPTURE_SOURCES.map((c) => (
                            <option key={c.value} value={c.value}>
                              {c.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      {m.capture_source === "primary_sent" && (
                        <label className="flex items-center gap-1.5">
                          <span className="text-muted-foreground">from</span>
                          <Input
                            key={`${m.id}-${m.capture_start_date}`}
                            type="date"
                            aria-label={`Capture start date for ${m.email}`}
                            className="h-7 w-auto"
                            max={today}
                            defaultValue={m.capture_start_date}
                            disabled={busy}
                            onBlur={(e) => changeStart(m, e.currentTarget)}
                          />
                        </label>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {m.last_synced_at
                        ? `Last synced ${formatDate(m.last_synced_at, bootstrap.me.timezone)}`
                        : "Not synced yet"}
                      {m.last_synced_at && !m.backfilled_at
                        ? m.capture_source === "primary_sent"
                          ? ` · reading from ${formatDate(m.capture_start_date, bootstrap.me.timezone)}`
                          : " · reading the last 90 days"
                        : ""}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    <Button variant="outline" size="sm" disabled={busy} onClick={() => editLabels(m)}>
                      Labels
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() =>
                        act(() => updateCrmMailbox(workspace, m.id, { is_active: !m.is_active }))
                      }
                    >
                      {m.is_active ? "Pause" : "Resume"}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() => {
                        if (
                          window.confirm(
                            `Remove ${m.email}? The emails stored from it are removed too.`
                          )
                        ) {
                          act(() => removeCrmMailbox(workspace, m.id));
                        }
                      }}
                    >
                      Remove
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <form onSubmit={add} className="grid max-w-2xl gap-3 sm:grid-cols-3">
            <label className="space-y-1 text-sm">
              <span className="block text-muted-foreground">Address</span>
              <Input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@nunnarilabs.com"
                className="h-8"
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="block text-muted-foreground">Owner</span>
              <select
                className="h-8 w-full rounded-md border bg-background px-2 text-sm"
                value={ownerId || bootstrap.me.id}
                onChange={(e) => setOwnerId(e.target.value)}
              >
                {members.map((m) => (
                  <option key={m.user_id} value={m.user_id}>
                    {m.display_name}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-sm">
              <span className="block text-muted-foreground">Labels (comma-separated)</span>
              <Input
                required
                value={labels}
                onChange={(e) => setLabels(e.target.value)}
                placeholder="Sales, Leads"
                className="h-8"
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="block text-muted-foreground">Capture</span>
              <select
                className="h-8 w-full rounded-md border bg-background px-2 text-sm"
                value={source}
                onChange={(e) => setSource(e.target.value as CrmCaptureSource)}
              >
                {CAPTURE_SOURCES.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </label>
            {source === "primary_sent" && (
              <label className="space-y-1 text-sm">
                <span className="block text-muted-foreground">Capture from</span>
                <Input
                  type="date"
                  required
                  max={today}
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                  className="h-8"
                />
              </label>
            )}
            <div className="sm:col-span-3">
              <Button type="submit" disabled={busy}>
                Add mailbox
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
