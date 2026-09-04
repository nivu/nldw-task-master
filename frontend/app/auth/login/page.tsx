"use client";

import { Suspense, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useSearchParams } from "next/navigation";

/**
 * FR-CAL-01 — the calendar is the primary view after sign-in.
 */
const AFTER_LOGIN_PATH = "/calendar";

/**
 * Sign in.
 *
 * Email and password only. The starter's one-time-code mode has been removed
 * because it called `signInWithOtp({ shouldCreateUser: true })`, which creates
 * an account for any address that asks — exactly what FR-AUTH-02 forbids.
 * Accounts are created by an admin, and there is deliberately no way to make
 * one from this page.
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
      <LoginForm />
    </Suspense>
  );
}

function LoginForm() {
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(
    searchParams.get("error") === "auth_failed"
      ? "That sign-in link did not work. Try your password."
      : null
  );

  const supabase = createClient();

  async function handleLogin(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);

    const { error: signInError } = await supabase.auth.signInWithPassword({
      email: email.trim(),
      password,
    });

    if (signInError) {
      // Supabase says "Invalid login credentials" for a wrong password AND for
      // an address that has no account. That is the correct behaviour — saying
      // which would confirm who works here to anyone who asks — so the message
      // is passed through rather than made more specific.
      setError(signInError.message);
      setLoading(false);
      return;
    }

    // A full page load, not a router push, so the middleware sees the new
    // session cookie on the very next request.
    window.location.href = AFTER_LOGIN_PATH;
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Nunnari Employee Portal</CardTitle>
          <CardDescription>Sign in to mark leave and see your team.</CardDescription>
        </CardHeader>
        <CardContent>
          {error && (
            <div
              role="alert"
              className="mb-4 rounded-md bg-destructive/10 p-3 text-sm text-destructive"
            >
              {error}
            </div>
          )}

          <form onSubmit={handleLogin} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@nunnari.example"
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "Signing in…" : "Sign in"}
            </Button>
          </form>

          <p className="mt-6 text-xs text-muted-foreground">
            Accounts are created by an admin. If you cannot sign in, ask Vinita
            rather than signing up — there is no sign-up.
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
