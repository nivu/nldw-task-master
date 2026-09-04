"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { GoogleButton } from "@/components/portal/google-button";
import { PasswordFallback } from "@/components/portal/password-fallback";

/**
 * A development-only affordance, never set in production.
 *
 * Google OAuth cannot be driven by a headless browser, so the 32 tests in
 * `e2e/` sign in as the seeded accounts with a password. This renders the form
 * they need.
 *
 * It is NOT what keeps production safe. That is Supabase's email provider
 * being disabled server-side (`[auth.email] enable_signup = false`), which
 * makes a password sign-in fail no matter what this page renders. Turning this
 * flag on against production would produce a form that cannot work, not a way
 * in.
 */
const PASSWORD_FALLBACK = process.env.NEXT_PUBLIC_ENABLE_PASSWORD_LOGIN === "true";

/**
 * Sign in — FR-AUTH-08.
 *
 * Google, and nothing else. There is no password field because there are no
 * passwords: in a small company they get shared ("just use mine, I'll approve
 * it later"), and a leave record somebody else can create is not a record.
 * Removing the credential removes the thing that can be passed around.
 *
 * There is also no way to create an account here (FR-AUTH-02). Google proves
 * who somebody is; whether they may use the portal is decided by a `profiles`
 * row an admin created (FR-AUTH-09).
 */
export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center">
          <p className="text-sm text-muted-foreground">Loading…</p>
        </div>
      }
    >
      <SignIn />
    </Suspense>
  );
}

function SignIn() {
  const searchParams = useSearchParams();
  // Written by /auth/callback, and already a sentence. Only the query string
  // is read: this app uses the PKCE code flow, where Supabase returns errors
  // as query parameters to the callback route.
  const [reason] = useState<string | null>(searchParams.get("reason"));

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Nunnari Employee Portal</CardTitle>
          <CardDescription>Sign in to mark leave and see your team.</CardDescription>
        </CardHeader>
        <CardContent>
          {reason && (
            <div
              role="alert"
              className="mb-4 rounded-md bg-destructive/10 p-3 text-sm text-destructive"
            >
              {reason}
            </div>
          )}

          <GoogleButton />

          {PASSWORD_FALLBACK && <PasswordFallback />}

          <p className="mt-6 text-xs text-muted-foreground">
            Accounts are created by an admin. Signing in with Google proves who
            you are — it does not create an account. If Google works but the
            portal still turns you away, ask an admin to add the address you
            signed in with.
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
