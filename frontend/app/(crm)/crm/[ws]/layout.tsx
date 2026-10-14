"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { CrmShell } from "@/components/crm/shell";
import { Button } from "@/components/ui/button";
import { getCrmBootstrap, getCrmWorkspaces } from "@/lib/api/crm";
import { useAsync } from "@/lib/use-async";

/**
 * Every page of one workspace — spec 007 FR-CRM-UI-01, -02; plan §5.9.
 *
 * One `/crm/bootstrap` call on load gives the shell its workspace, the
 * person's role there, the lists and their display preferences. A workspace
 * the person is not in answers "No such workspace." like one that does not
 * exist (FR-CRM-WS-02), and nothing of it renders.
 */
export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const { ws } = useParams<{ ws: string }>();
  const { data, error, reload } = useAsync(
    () => Promise.all([getCrmBootstrap(ws), getCrmWorkspaces()]),
    [ws]
  );

  if (error) {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-4 p-6 text-center">
        <p className="text-sm text-muted-foreground">{error}</p>
        <Button variant="outline" nativeButton={false} render={<Link href="/crm" />}>
          Open your workspace
        </Button>
      </main>
    );
  }
  if (!data) return null;

  const [bootstrap, workspaces] = data;
  return (
    <CrmShell bootstrap={bootstrap} workspaces={workspaces} reload={reload}>
      {children}
    </CrmShell>
  );
}
