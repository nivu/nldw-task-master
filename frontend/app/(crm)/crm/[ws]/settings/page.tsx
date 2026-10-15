"use client";

import Link from "next/link";
import { useState } from "react";

import { AuditTable } from "@/components/crm/audit-table";
import { FxRatesForm } from "@/components/crm/fx-rates-form";
import { InviteDialog } from "@/components/crm/invite-dialog";
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
  getCrmLists,
  getCrmMembers,
  getCrmSettings,
  inviteCrmMember,
  removeCrmMember,
  updateCrmMember,
  updateCrmSetting,
} from "@/lib/api/crm";
import type { CrmAuditEntry, StageKey, StageProbabilities } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { useAsync } from "@/lib/use-async";

const STAGES: StageKey[] = [
  "discovery",
  "qualification",
  "proposal",
  "negotiation",
  "closed_won",
  "closed_lost",
];

/**
 * Workspace settings — spec 007 FR-CRM-LIST-06, FR-CRM-ACC-04 to -12,
 * FR-CRM-AUD-03. Admins only; the server refuses everyone else whatever this
 * page shows.
 */
export default function SettingsPage() {
  const { bootstrap } = useCrm();
  const ws = bootstrap.workspace.key;
  const [tab, setTab] = useState("members");

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
        {/* FR-CRM-MIG-01: the Twenty importer. */}
        <Button variant="outline" size="sm" nativeButton={false} render={<Link href={`/crm/${ws}/settings/import`} />}>
          Import from Twenty
        </Button>
      </div>
      <Tabs value={tab} onValueChange={(value) => setTab(String(value))}>
        <TabsList className="max-w-full overflow-x-auto">
          <TabsTrigger value="members">Members</TabsTrigger>
          <TabsTrigger value="lists">Lists</TabsTrigger>
          <TabsTrigger value="probabilities">Probabilities</TabsTrigger>
          <TabsTrigger value="fx">FX rates</TabsTrigger>
          <TabsTrigger value="domains">Allowed domains</TabsTrigger>
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
