"use client";

import { useCrm } from "@/components/crm/shell";
import { Card, CardContent } from "@/components/ui/card";
import { formatDate } from "@/lib/crm/format";

/**
 * Today — spec 007 FR-CRM-UI-02. The heading and the CRM's home; its
 * sections (tasks due, deals to move, follow-ups) arrive with Today itself.
 */
export default function TodayPage() {
  const { bootstrap } = useCrm();
  return (
    <div className="space-y-4">
      <div>
        <h1 className="font-heading text-lg font-semibold">Today</h1>
        <p className="text-sm text-muted-foreground">
          {formatDate(new Date(), bootstrap.me.timezone)} · {bootstrap.workspace.name}
        </p>
      </div>
      <Card>
        <CardContent className="p-4 text-sm text-muted-foreground">
          Welcome, {bootstrap.me.display_name}. Your tasks, deals and follow-ups will appear here.
        </CardContent>
      </Card>
    </div>
  );
}
