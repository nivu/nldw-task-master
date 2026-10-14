"use client";

import { CapacityPanel } from "@/components/crm/capacity-panel";
import { useCrm } from "@/components/crm/shell";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getCrmCapacity } from "@/lib/api/crm";
import { useAsync } from "@/lib/use-async";

/**
 * Capacity — spec 007 FR-CRM-CAP-01, -02. Labs only, for admins and staff
 * members; Pipeline links here. Hiding it from anyone else is tidiness: the
 * server refuses Viewers and CRM-only accounts whatever this page shows.
 */
export default function CapacityPage() {
  const { bootstrap } = useCrm();
  const allowed =
    bootstrap.workspace.tva_linked &&
    bootstrap.me.account_type === "staff" &&
    (bootstrap.role === "admin" || bootstrap.role === "member");

  return (
    <div className="space-y-4">
      <h1 className="font-heading text-lg font-semibold">Capacity</h1>
      {allowed ? (
        <CapacityCard workspace={bootstrap.workspace.key} timeZone={bootstrap.me.timezone} />
      ) : (
        <p className="text-sm text-muted-foreground">
          The capacity check is for Nunnari Labs admins and staff members.
        </p>
      )}
    </div>
  );
}

function CapacityCard({
  workspace,
  timeZone,
}: {
  workspace: string;
  timeZone: Parameters<typeof CapacityPanel>[0]["timeZone"];
}) {
  const { data, error } = useAsync(() => getCrmCapacity(workspace), [workspace]);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">The next 13 weeks</CardTitle>
        <CardDescription>
          Hours tentative projects would need, against hours nobody is promised to yet (supply
          less confirmed work, net of holidays and leave). The same figures as TVA&apos;s
          forecast.
        </CardDescription>
      </CardHeader>
      <CardContent className="p-0 sm:px-4 sm:pb-4">
        {error && <p className="p-4 text-sm text-destructive">{error}</p>}
        {!error && !data && <p className="p-4 text-sm text-muted-foreground">Loading…</p>}
        {data && <CapacityPanel weeks={data.weeks} timeZone={timeZone} />}
      </CardContent>
    </Card>
  );
}
