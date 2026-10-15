"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { getCrmWorkspaces } from "@/lib/api/crm";
import { errorMessage, getMe } from "@/lib/api/portal";
import type { Me } from "@/lib/api/types";

/**
 * `/crm` — spec 007 FR-CRM-WS-04, FR-CRM-UI-03, -04; plan §5.9.
 *
 * Sends the person to the workspace they used last. Someone with no
 * membership is told so: a staff member is offered TVA, and nothing of any
 * workspace is shown (FR-CRM-UI-03).
 */
export default function CrmHome() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [none, setNone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCrmWorkspaces()
      .then((workspaces) => {
        const last = workspaces.find((w) => w.last_used) ?? workspaces[0];
        if (last) {
          router.replace(`/crm/${last.key}`);
          return;
        }
        setNone(true);
        return getMe().then(setMe);
      })
      .catch((err) => setError(errorMessage(err)));
  }, [router]);

  if (!none && !error) return null;

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 p-6 text-center">
      <p className="max-w-sm text-sm text-muted-foreground">
        {error ?? "You are not a member of any CRM workspace. A workspace admin can invite you."}
      </p>
      {me?.account_type === "staff" && (
        <Button nativeButton={false} render={<Link href="/home" />}>
          Back to TVA
        </Button>
      )}
    </main>
  );
}
