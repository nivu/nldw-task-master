"use client";

import Link from "next/link";
import { useState } from "react";

import { useCrm } from "@/components/crm/shell";
import { TodayList } from "@/components/crm/today-list";
import { getCrmMembers, getCrmToday } from "@/lib/api/crm";
import { formatDate } from "@/lib/crm/format";
import { useAsync } from "@/lib/use-async";

/**
 * Today — spec 007 FR-CRM-TODAY-01 to -04, FR-CRM-CHG-13, FR-CRM-UI-02.
 *
 * The CRM's home: what to do today, in the person's own day. A workspace
 * Admin may pick any member and see their Today instead (a sales-only
 * exception to TVA's no-ranking rule); the server refuses anyone else.
 * Replies owed and silent clients come from Gmail sync (S10); while it is
 * off those sections say so, and an Admin is pointed at the Mailboxes
 * settings (FR-CRM-TODAY-02).
 */
export default function TodayPage() {
  const { bootstrap } = useCrm();
  const ws = bootstrap.workspace.key;
  const admin = bootstrap.role === "admin";
  const [person, setPerson] = useState<string>("");
  const members = useAsync(() => (admin ? getCrmMembers(ws) : Promise.resolve([])), [ws, admin]);
  const today = useAsync(() => getCrmToday(ws, person || undefined), [ws, person]);
  const zone = bootstrap.me.timezone;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-heading text-lg font-semibold">Today</h1>
          <p className="text-sm text-muted-foreground">
            {today.data ? formatDate(today.data.as_of, zone) : formatDate(new Date(), zone)} ·{" "}
            {bootstrap.workspace.name}
            {today.data && person && today.data.person.name ? ` · ${today.data.person.name}` : ""}
          </p>
        </div>
        {admin && (members.data?.length ?? 0) > 1 && (
          <label className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">Today of</span>
            <select
              aria-label="Today of"
              className="h-8 rounded-md border bg-background px-2 text-sm"
              value={person}
              onChange={(e) => setPerson(e.target.value)}
            >
              <option value="">Me</option>
              {(members.data ?? [])
                .filter((m) => m.user_id !== bootstrap.me.id)
                .map((m) => (
                  <option key={m.user_id} value={m.user_id}>
                    {m.display_name}
                  </option>
                ))}
            </select>
          </label>
        )}
      </div>
      {admin && !bootstrap.email_sync_on && (
        <p className="text-xs text-muted-foreground">
          Replies owed and silent clients come from Gmail label sync.{" "}
          <Link href={`/crm/${ws}/settings?tab=mailboxes`} className="underline">
            Set it up in Settings
          </Link>
          .
        </p>
      )}
      {today.error ? (
        <p className="text-sm text-muted-foreground">{today.error}</p>
      ) : !today.data ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <TodayList workspace={ws} today={today.data} timeZone={zone} />
      )}
    </div>
  );
}
