"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { approveOauth, errorMessage, getMe, getOauthTransaction } from "@/lib/api/portal";
import { createClient } from "@/lib/supabase/client";

/**
 * OAuth consent — spec 006 FR-OAUTH-02.
 *
 * Claude (or any MCP client) sent the person here with a transaction id.
 * If they are not signed in, they sign in with Google first and come back.
 * They see who is asking and choose; the backend mints the code only on
 * "Allow", bound to the signed-in person. The client's redirect address was
 * registered by the client and is never taken from this page.
 */
export default function ConnectPage() {
  return (
    <Suspense fallback={<main className="p-6 text-sm text-muted-foreground">Loading…</main>}>
      <Connect />
    </Suspense>
  );
}

function Connect() {
  const params = useSearchParams();
  const router = useRouter();
  const txn = params.get("txn") ?? "";
  const [who, setWho] = useState<{ client_name: string | null } | null>(null);
  const [me, setMe] = useState<{ display_name: string; email: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    async function load() {
      if (!txn) {
        setError("This link is missing its connection request. Start again from Claude.");
        return;
      }
      const { data } = await createClient().auth.getSession();
      if (!data.session) {
        router.replace(`/auth/login?next=${encodeURIComponent(`/auth/connect?txn=${txn}`)}`);
        return;
      }
      const [m, t] = await Promise.all([getMe(), getOauthTransaction(txn)]);
      setMe(m);
      setWho(t);
    }
    load().catch((err: unknown) => setError(errorMessage(err)));
  }, [txn, router]);

  async function decide(approved: boolean) {
    setBusy(true);
    try {
      const { redirect } = await approveOauth(txn, approved);
      window.location.href = redirect;
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Connect to the Nunnari portal</CardTitle>
          <CardDescription>
            {who?.client_name ?? "An app"} is asking to use the portal as you.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          {error && (
            <div role="alert" className="rounded-md bg-destructive/10 p-3 text-destructive">
              {error}
            </div>
          )}
          {me && who && !error && (
            <>
              <p>
                It will act as <span className="font-medium">{me.display_name}</span> ({me.email})
                with exactly your access — nothing more. You can end this at any time from
                Account → Connect Claude.
              </p>
              <p className="text-muted-foreground">
                Leave reasons are never shared through this connection.
              </p>
              <div className="flex gap-2">
                <Button onClick={() => decide(true)} disabled={busy}>
                  Allow
                </Button>
                <Button variant="outline" onClick={() => decide(false)} disabled={busy}>
                  Deny
                </Button>
              </div>
            </>
          )}
          {!me && !error && <p className="text-muted-foreground">Checking who you are…</p>}
        </CardContent>
      </Card>
    </main>
  );
}
