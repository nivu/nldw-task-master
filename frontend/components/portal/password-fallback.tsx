"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createClient } from "@/lib/supabase/client";

/**
 * Password sign-in, for local development only.
 *
 * Rendered only when NEXT_PUBLIC_ENABLE_PASSWORD_LOGIN is "true", which is set
 * in `frontend/.env` for local work and nowhere else. It exists because Google
 * OAuth cannot be driven by a headless browser and the browser tests need a
 * way in as the seeded accounts.
 *
 * This component is not a security boundary and must never be treated as one.
 * FR-AUTH-08 is enforced by Supabase's email provider being disabled, which
 * makes every request this form sends fail — the form's absence in production
 * is tidiness, its inability to work is the actual control.
 */
export function PasswordFallback() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);

    const { error: signInError } = await createClient().auth.signInWithPassword({
      email: email.trim(),
      password,
    });

    if (signInError) {
      setError(signInError.message);
      setLoading(false);
      return;
    }
    // A full page load so the proxy sees the new session cookie immediately.
    window.location.href = "/calendar";
  }

  return (
    <div className="mt-5">
      <div className="mb-4 flex items-center gap-3">
        <span className="h-px flex-1 bg-border" />
        <span className="text-xs text-muted-foreground">local development only</span>
        <span className="h-px flex-1 bg-border" />
      </div>

      {error && (
        <div
          role="alert"
          className="mb-4 rounded-md bg-destructive/10 p-3 text-sm text-destructive"
        >
          {error}
        </div>
      )}

      <form onSubmit={submit} className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
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
        <Button type="submit" variant="outline" className="w-full" disabled={loading}>
          {loading ? "Signing in…" : "Sign in with a password"}
        </Button>
      </form>
    </div>
  );
}
