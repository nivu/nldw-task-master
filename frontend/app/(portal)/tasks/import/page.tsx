"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { LifeOsImport } from "@/components/tasks/lifeos-import";
import { getMe } from "@/lib/api/portal";
import { useAsync } from "@/lib/use-async";

/**
 * The Life OS import — spec 008 FR-TASK-IMP-01, TP-10. Owner only (the
 * server answers 403 to anyone else): `capabilities.grant_dashboard` is
 * `profiles.is_owner`. Anyone else, or the owner below phase 1, goes to
 * My Tasks.
 */
export default function LifeOsImportPage() {
  const router = useRouter();
  const { data: me } = useAsync(getMe, []);
  const allowed =
    me !== null && me.account_type === "staff" && me.capabilities.grant_dashboard && me.tasks.level >= 1;
  useEffect(() => {
    if (me !== null && !allowed) router.replace("/tasks");
  }, [me, allowed, router]);
  if (!me || !allowed) return null;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="font-heading text-lg font-semibold">Import Life OS</h1>
        <p className="text-sm text-muted-foreground">Bring the Life OS sheet&apos;s tasks into My Tasks, once.</p>
      </div>
      <LifeOsImport />
    </div>
  );
}
