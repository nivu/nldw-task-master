"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { MyTasks } from "@/components/tasks/my-tasks";
import { getMe } from "@/lib/api/portal";
import { useAsync } from "@/lib/use-async";

/**
 * My Tasks in the TVA shell — spec 008 FR-TASK-LIST-08. The page itself is
 * `components/tasks/my-tasks.tsx`, shared with the CRM shell. A project
 * opens on the Projects page for those who have it. Below phase 1 it shows
 * nothing and goes Home (FR-TASK-ROLL-03).
 */
export default function TasksPage() {
  const router = useRouter();
  const { data: me } = useAsync(getMe, []);
  const off = me !== null && me.tasks.level < 1;
  useEffect(() => {
    if (off) router.replace("/home");
  }, [off, router]);
  if (!me || off) return null;
  const projects = me.account_type === "staff" && me.capabilities.manage_projects;
  return <MyTasks projectsHref={projects ? "/projects" : undefined} level={me.tasks.level} meId={me.id} />;
}
