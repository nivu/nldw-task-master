"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useCrm } from "@/components/crm/shell";
import { MyTasks } from "@/components/tasks/my-tasks";
import { getMe } from "@/lib/api/portal";
import { useAsync } from "@/lib/use-async";

/**
 * My Tasks in the CRM shell — spec 008 FR-TASK-LIST-08 (T6): the same page
 * as `/tasks`, for everyone with a workspace, CRM-only sellers included.
 * Below phase 1 it shows nothing and goes to the workspace (FR-TASK-ROLL-03).
 */
export default function CrmMyTasksPage() {
  const router = useRouter();
  const { bootstrap } = useCrm();
  const ws = bootstrap.workspace.key;
  const { data: me } = useAsync(getMe, []);
  const off = me !== null && me.tasks.level < 1;
  useEffect(() => {
    if (off) router.replace(`/crm/${ws}`);
  }, [off, router, ws]);
  if (!me || off) return null;
  return <MyTasks level={me.tasks.level} meId={me.id} />;
}
