"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { TokenManager } from "@/components/shared/token-manager";
import { ThemeToggle } from "@/components/shared/theme-toggle";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { getCrmPreferences, updateCrmPreferences } from "@/lib/api/crm";
import { errorMessage } from "@/lib/api/portal";
import type { MePreferences } from "@/lib/api/types";
import { useAsync } from "@/lib/use-async";

/**
 * The CRM account page — spec 007 FR-CRM-ACC-14, -15.
 *
 * Outside any workspace (`/crm/account` is a static segment beside `[ws]`),
 * so it works for someone in several. Display preferences affect the CRM
 * only; TVA stays on India time. Personal tokens and the Claude connector go
 * through the shared routes a CRM-only seller may use (contracts §1).
 */
export default function CrmAccountPage() {
  const { data, error, setError, reload } = useAsync(() => getCrmPreferences(), []);
  const [saved, setSaved] = useState(false);

  async function save(changes: Partial<MePreferences>) {
    setSaved(false);
    try {
      await updateCrmPreferences(changes);
      setSaved(true);
      reload();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-3xl items-center gap-3 px-4 py-3">
          <Link
            href="/crm"
            className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-4" />
            Back to the CRM
          </Link>
          <div className="ml-auto">
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6">
        <h1 className="font-heading text-lg font-semibold">Account</h1>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Display</CardTitle>
            <CardDescription>How the CRM shows dates and amounts to you.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {error && (
              <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                {error}
              </div>
            )}
            {data && (
              <div className="grid max-w-md grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="pref-timezone">Time zone</Label>
                  <select
                    id="pref-timezone"
                    className="h-8 w-full rounded-md border border-input bg-transparent px-2 text-sm"
                    value={data.timezone}
                    onChange={(e) =>
                      save({ timezone: e.target.value as MePreferences["timezone"] })
                    }
                  >
                    <option value="Asia/Kolkata">India (Kolkata)</option>
                    <option value="Australia/Sydney">Australia (Sydney)</option>
                  </select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="pref-amounts">Amounts in INR</Label>
                  <select
                    id="pref-amounts"
                    className="h-8 w-full rounded-md border border-input bg-transparent px-2 text-sm"
                    value={data.number_format}
                    onChange={(e) =>
                      save({ number_format: e.target.value as MePreferences["number_format"] })
                    }
                  >
                    <option value="indian_short">Short (12 L, 1.2 Cr)</option>
                    <option value="full">Full (12,34,567)</option>
                  </select>
                </div>
              </div>
            )}
            {saved && <p className="text-sm text-muted-foreground">Saved.</p>}
          </CardContent>
        </Card>

        <TokenManager />

        <Card>
          <CardContent className="p-4 text-sm text-muted-foreground">
            To use the CRM from claude.ai, add the portal as a connector there. Claude sends you
            back here to approve it, with the same access you have in the CRM and nothing more.
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
