"use client";

import { useState } from "react";

import { AuditTable } from "@/components/crm/audit-table";
import { InviteDialog } from "@/components/crm/invite-dialog";
import { MembersTable } from "@/components/crm/members-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  getTvaCrmAudit,
  getTvaCrmMembers,
  getTvaCrmWorkspaces,
  setTvaAllowedDomains,
  tvaInviteCrmMember,
  tvaRemoveCrmMembership,
  tvaSetAccountType,
  tvaUpdateCrmMembership,
} from "@/lib/api/crm";
import type { CrmMember, TvaCrmWorkspace } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { useAsync } from "@/lib/use-async";

/**
 * TVA Admin → CRM workspaces — spec 007 FR-CRM-ACC-01, -05, -06, -13,
 * FR-CRM-CHG-11; plan CT-3.
 *
 * A TVA admin administers every workspace without joining one: members,
 * their roles (the owner's and a TVA admin's included, FR-CRM-ACC-09),
 * invitations, removals, the domain allow-list and account types. None of
 * it opens a CRM record, and the audit below names who changed whose access,
 * never what a record says.
 */
export function CrmWorkspacesPanel() {
  const { data, error, setError, reload } = useAsync(() => getTvaCrmWorkspaces(), []);
  const [auditNonce, setAuditNonce] = useState(0);
  const changed = () => {
    reload();
    setAuditNonce((n) => n + 1);
  };

  return (
    <div className="space-y-4">
      {error && (
        <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}
      {(data ?? []).map((workspace) => (
        <WorkspaceCard
          key={workspace.id}
          workspace={workspace}
          onChanged={changed}
          onError={setError}
        />
      ))}
      <AdministrationAudit nonce={auditNonce} />
    </div>
  );
}

function WorkspaceCard({
  workspace,
  onChanged,
  onError,
}: {
  workspace: TvaCrmWorkspace;
  onChanged: () => void;
  onError: (message: string) => void;
}) {
  const { data, reload } = useAsync(() => getTvaCrmMembers(workspace.id), [workspace.id]);
  const [inviting, setInviting] = useState(false);
  const [domains, setDomains] = useState(workspace.allowed_domains.join(", "));
  const [saving, setSaving] = useState(false);

  function refresh() {
    reload();
    onChanged();
  }

  async function act(run: () => Promise<unknown>) {
    try {
      await run();
      refresh();
    } catch (err) {
      onError(errorMessage(err));
    }
  }

  const accountType = (member: CrmMember) => (
    <select
      aria-label={`Account type for ${member.display_name}`}
      className="h-8 rounded-md border border-input bg-transparent px-2 text-sm"
      value={member.account_type}
      onChange={(e) => {
        const to = e.target.value as CrmMember["account_type"];
        const sure =
          to === "staff" ||
          window.confirm(
            `Make ${member.display_name} CRM-only? They lose TVA: role, lead, time logging and dashboard access.`
          );
        if (sure) act(() => tvaSetAccountType(member.user_id, to));
      }}
    >
      <option value="staff">Staff</option>
      <option value="crm_only">CRM only</option>
    </select>
  );

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex-1">
            <CardTitle className="text-base">
              {workspace.name}{" "}
              <Badge variant="outline" className="ml-1">
                {workspace.member_count} member{workspace.member_count === 1 ? "" : "s"}
              </Badge>
            </CardTitle>
            <CardDescription>Key: {workspace.key}</CardDescription>
          </div>
          <Button onClick={() => setInviting(true)}>Invite</Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 p-0 pb-4">
        <MembersTable
          members={data ?? []}
          extra={accountType}
          onRoleChange={(member, role) =>
            act(() => tvaUpdateCrmMembership(member.membership_id, role))
          }
          onRemove={(member) => {
            if (window.confirm(`Remove ${member.display_name} from ${workspace.name}?`)) {
              act(() => tvaRemoveCrmMembership(member.membership_id));
            }
          }}
        />
        <form
          className="flex flex-wrap items-end gap-2 px-4"
          onSubmit={async (event) => {
            event.preventDefault();
            setSaving(true);
            try {
              const saved = await setTvaAllowedDomains(
                workspace.id,
                domains.split(/[\s,]+/).filter(Boolean)
              );
              setDomains(saved.allowed_domains.join(", "));
              refresh();
            } catch (err) {
              onError(errorMessage(err));
            } finally {
              setSaving(false);
            }
          }}
        >
          <label className="min-w-64 flex-1 space-y-1.5 text-sm">
            <span className="block text-muted-foreground">
              Allowed domains (whom its admins may invite)
            </span>
            <Input value={domains} onChange={(e) => setDomains(e.target.value)} className="h-8" />
          </label>
          <Button type="submit" variant="outline" disabled={saving}>
            Save domains
          </Button>
        </form>
      </CardContent>
      <InviteDialog
        open={inviting}
        onOpenChange={setInviting}
        workspaceName={workspace.name}
        allowedDomains={workspace.allowed_domains}
        allowBypass
        onInvite={(invitation) => tvaInviteCrmMember(workspace.id, invitation)}
        onDone={refresh}
      />
    </Card>
  );
}

function AdministrationAudit({ nonce }: { nonce: number }) {
  const { data, error } = useAsync(() => getTvaCrmAudit({ limit: 50 }), [nonce]);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Account administration</CardTitle>
        <CardDescription>
          Invitations, removals, role and account-type changes and allow-list edits, in every
          workspace.
        </CardDescription>
      </CardHeader>
      <CardContent className="p-0 pb-4">
        {error && <p className="px-4 text-sm text-destructive">{error}</p>}
        <AuditTable
          timeZone="Asia/Kolkata"
          rows={(data ?? []).map((r) => ({
            id: r.id,
            at: r.at,
            actor_name: r.actor_name,
            action: r.action,
            subject: [r.target_name, r.role, r.workspace_key].filter(Boolean).join(" · ") || null,
          }))}
        />
      </CardContent>
    </Card>
  );
}
