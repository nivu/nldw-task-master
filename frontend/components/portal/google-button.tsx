"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { createClient } from "@/lib/supabase/client";

/**
 * Sign in with Google — FR-AUTH-08.
 *
 * Google authenticates; it does not authorise (FR-AUTH-09). A successful
 * Google sign-in only proves who somebody is. Whether they may use the portal
 * is decided by `app/api/deps.py::current_user`, which requires a `profiles`
 * row an admin created. Somebody with a valid Google account and no portal
 * account gets a clear refusal, not access.
 *
 * `redirectTo` points at this application's own callback route, which
 * exchanges the code for a session. Google's OAuth client is configured with
 * *Supabase's* callback, which is a different URL — Supabase sits in the
 * middle and hands off to us afterwards.
 */
export function GoogleButton({ next = "/calendar" }: { next?: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function signIn() {
    setBusy(true);
    setError(null);

    const { error: oauthError } = await createClient().auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`,
        queryParams: {
          // Always show the account chooser. Without this, a shared or
          // previously-used browser signs the last person straight back in —
          // which is the sharing problem this replaced passwords to avoid.
          prompt: "select_account",
        },
      },
    });

    if (oauthError) {
      setError(oauthError.message);
      setBusy(false);
    }
    // On success the browser leaves for Google, so there is nothing to reset.
  }

  return (
    <div className="space-y-3">
      {error && (
        <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}
      <Button onClick={signIn} disabled={busy} className="w-full" variant="outline">
        <GoogleMark />
        {busy ? "Taking you to Google…" : "Sign in with Google"}
      </Button>
    </div>
  );
}

/** Google's mark, inline so the page pulls nothing from a third-party host. */
function GoogleMark() {
  return (
    <svg viewBox="0 0 24 24" className="size-4" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1Z"
      />
      <path
        fill="#34A853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23Z"
      />
      <path
        fill="#FBBC05"
        d="M5.84 14.1a6.6 6.6 0 0 1 0-4.2V7.06H2.18a11 11 0 0 0 0 9.88l3.66-2.84Z"
      />
      <path
        fill="#EA4335"
        d="M12 4.75c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 1.46 14.97.5 12 .5A11 11 0 0 0 2.18 7.06l3.66 2.84C6.71 7.3 9.14 4.75 12 4.75Z"
      />
    </svg>
  );
}
