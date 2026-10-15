"use client";

import { formatDate } from "@/lib/crm/format";
import type { MePreferences } from "@/lib/api/types";

/** One row of either audit: the workspace's own, or the TVA admin's. */
export interface AuditRow {
  id: number;
  at: string;
  actor_name: string;
  action: string;
  /** What the change was about: a record type, a person or a workspace. */
  subject: string | null;
  /** Shown only by the workspace audit, to its own admins. */
  detail?: string | null;
}

function timeOf(at: string, timeZone: MePreferences["timezone"]): string {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
  }).format(new Date(at));
}

/**
 * An audit trail — spec 007 FR-CRM-AUD-03 (a workspace's own, admins only)
 * and contracts §2.4 (the TVA admin's account-administration events, which
 * carry no record content). Dates read "14 Oct 2026" in the viewer's zone
 * (FR-CRM-UI-07).
 */
export function AuditTable({
  rows,
  timeZone,
  onOlder,
}: {
  rows: AuditRow[];
  timeZone: MePreferences["timezone"];
  onOlder?: () => void;
}) {
  if (rows.length === 0) {
    return <p className="p-3 text-sm text-muted-foreground">Nothing recorded yet.</p>;
  }
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th className="p-2 font-medium">When</th>
              <th className="p-2 font-medium">Who</th>
              <th className="p-2 font-medium">What</th>
              <th className="p-2 font-medium">About</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.map((row) => (
              <tr key={row.id} className="align-top">
                <td className="whitespace-nowrap p-2 text-muted-foreground">
                  {formatDate(row.at, timeZone)} {timeOf(row.at, timeZone)}
                </td>
                <td className="p-2">{row.actor_name}</td>
                <td className="p-2 font-mono text-xs">{row.action}</td>
                <td className="p-2">
                  {row.subject ?? "—"}
                  {row.detail && (
                    <span className="block max-w-md truncate font-mono text-xs text-muted-foreground">
                      {row.detail}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {onOlder && (
        <button
          type="button"
          className="text-sm text-muted-foreground underline-offset-2 hover:underline"
          onClick={onOlder}
        >
          Older entries
        </button>
      )}
    </div>
  );
}
