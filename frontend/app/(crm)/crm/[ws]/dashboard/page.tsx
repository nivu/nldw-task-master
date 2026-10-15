"use client";

import { useState } from "react";

import { DashboardPanels } from "@/components/crm/dashboard-panels";
import { useCrm } from "@/components/crm/shell";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getCrmDashboard, getCrmMembers } from "@/lib/api/crm";
import type { CrmDashboardFilters, CrmEntity } from "@/lib/api/crm-types";
import { NATIVE_SELECT } from "@/lib/crm/view-fields";
import { useAsync } from "@/lib/use-async";

/**
 * The dashboard — spec 007 FR-CRM-DASH-01 to -04, FR-CRM-UI-02; journey 4.10.
 *
 * One fixed dashboard per workspace: Admins see the workspace, Members their
 * own numbers (the server forces it), Viewers none (the server refuses; the
 * sidebar does not offer it). Filters: entity (Labs), owner (Admins), channel
 * and the date range, which sets win rate, time in stage and activity.
 */
export default function DashboardPage() {
  const { bootstrap } = useCrm();
  const ws = bootstrap.workspace.key;
  const admin = bootstrap.role === "admin";
  const [filters, setFilters] = useState<CrmDashboardFilters>({});
  const key = JSON.stringify(filters);
  const { data, error } = useAsync(() => getCrmDashboard(ws, JSON.parse(key)), [ws, key]);
  const members = useAsync(() => (admin ? getCrmMembers(ws) : Promise.resolve([])), [ws, admin]);
  const channels = (bootstrap.lists["channel"] ?? []).filter((o) => o.is_active);
  const set = (patch: Partial<CrmDashboardFilters>) => {
    const next = { ...filters, ...patch };
    for (const k of Object.keys(next) as (keyof CrmDashboardFilters)[]) if (!next[k]) delete next[k];
    setFilters(next);
  };

  if (bootstrap.role === "viewer") {
    return (
      <div className="space-y-4">
        <h1 className="font-heading text-lg font-semibold">Dashboard</h1>
        <p className="text-sm text-muted-foreground">Viewers have no dashboard.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="font-heading text-lg font-semibold">Dashboard</h1>
        {data?.own_numbers_only && (
          <p className="text-sm text-muted-foreground">Your own numbers: the deals, companies and activity you own.</p>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-3" aria-label="Dashboard filters" role="group">
        {bootstrap.workspace.has_entity && (
          <div className="grid gap-1">
            <Label htmlFor="dash-entity">Entity</Label>
            <select
              id="dash-entity"
              className={NATIVE_SELECT}
              value={filters.entity ?? ""}
              onChange={(e) => set({ entity: (e.target.value || undefined) as CrmEntity | undefined })}
            >
              <option value="">All</option>
              <option value="nunnari_labs">Nunnari Labs</option>
              <option value="deepweaver">DeepWeaver</option>
            </select>
          </div>
        )}
        {admin && (
          <div className="grid gap-1">
            <Label htmlFor="dash-owner">Owner</Label>
            <select
              id="dash-owner"
              className={NATIVE_SELECT}
              value={filters.owner_id ?? ""}
              onChange={(e) => set({ owner_id: e.target.value || undefined })}
            >
              <option value="">Everyone</option>
              {(members.data ?? []).map((m) => (
                <option key={m.user_id} value={m.user_id}>
                  {m.display_name}
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="grid gap-1">
          <Label htmlFor="dash-channel">Channel</Label>
          <select
            id="dash-channel"
            className={NATIVE_SELECT}
            value={filters.channel_id ?? ""}
            onChange={(e) => set({ channel_id: e.target.value || undefined })}
          >
            <option value="">All</option>
            {channels.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div className="grid gap-1">
          <Label htmlFor="dash-from">From</Label>
          <Input
            id="dash-from"
            type="date"
            className="h-8 w-40"
            value={filters.from ?? ""}
            onChange={(e) => set({ from: e.target.value || undefined })}
          />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="dash-to">To</Label>
          <Input
            id="dash-to"
            type="date"
            className="h-8 w-40"
            value={filters.to ?? ""}
            onChange={(e) => set({ to: e.target.value || undefined })}
          />
        </div>
      </div>

      {error && (
        <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
          {error}
        </p>
      )}
      {!data && !error && <p className="text-sm text-muted-foreground">Loading…</p>}
      {data && (
        <DashboardPanels
          data={data}
          prefs={{ timezone: bootstrap.me.timezone, number_format: bootstrap.me.number_format }}
        />
      )}
    </div>
  );
}
