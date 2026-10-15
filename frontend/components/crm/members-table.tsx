"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { CrmMember, CrmRole } from "@/lib/api/crm-types";

const ROLE_LABEL: Record<CrmRole, string> = { admin: "Admin", member: "Member", viewer: "Viewer" };

/**
 * A workspace's members — spec 007 contracts §2.2, §2.4.
 *
 * Used by workspace Settings (role and removal, FR-CRM-ACC-12) and by the
 * TVA admin panel (role, removal and account type, FR-CRM-ACC-09, -13). The
 * last-admin and protected-membership refusals come from the server and are
 * shown as they arrive.
 */
export function MembersTable({
  members,
  onRoleChange,
  onRemove,
  extra,
}: {
  members: CrmMember[];
  onRoleChange?: (member: CrmMember, role: CrmRole) => void;
  onRemove?: (member: CrmMember) => void;
  /** One more cell per row, such as the TVA admin's account-type control. */
  extra?: (member: CrmMember) => React.ReactNode;
}) {
  if (members.length === 0) {
    return <p className="p-3 text-sm text-muted-foreground">No members.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs text-muted-foreground">
            <th className="p-3 font-medium">Name</th>
            <th className="p-3 font-medium">Role</th>
            {extra && <th className="p-3 font-medium">Account</th>}
            {onRemove && <th className="p-3" />}
          </tr>
        </thead>
        <tbody className="divide-y">
          {members.map((member) => (
            <tr key={member.membership_id} data-testid="member-row">
              <td className="p-3">
                <span className="font-medium">{member.display_name}</span>
                {member.account_type === "crm_only" && (
                  <Badge variant="outline" className="ml-2">
                    CRM only
                  </Badge>
                )}
                {member.email && (
                  <span className="block text-xs text-muted-foreground">{member.email}</span>
                )}
              </td>
              <td className="p-3">
                {onRoleChange ? (
                  <select
                    aria-label={`Role for ${member.display_name}`}
                    className="h-8 rounded-md border border-input bg-transparent px-2 text-sm"
                    value={member.role}
                    onChange={(e) => onRoleChange(member, e.target.value as CrmRole)}
                  >
                    {(Object.keys(ROLE_LABEL) as CrmRole[]).map((role) => (
                      <option key={role} value={role}>
                        {ROLE_LABEL[role]}
                      </option>
                    ))}
                  </select>
                ) : (
                  ROLE_LABEL[member.role]
                )}
              </td>
              {extra && <td className="p-3">{extra(member)}</td>}
              {onRemove && (
                <td className="p-3 text-right">
                  <Button variant="ghost" size="sm" onClick={() => onRemove(member)}>
                    Remove
                  </Button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
