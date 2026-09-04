"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { changePassword, errorMessage, getHistory, getMe } from "@/lib/api/portal";
import type { Category, Me, YearHistory } from "@/lib/api/types";
import { CATEGORY_LABEL } from "@/lib/api/types";

const CATEGORIES: Category[] = ["wfh", "casual", "sick"];

/**
 * The person's own account — FR-AUTH-05 and FR-BAL-08.
 *
 * Self-service profile editing is an explicit non-goal for V1 (spec §2.2), so
 * name, role and lead are shown but not editable. Only the password can be
 * changed here, and that goes to Supabase Auth rather than to any table this
 * product owns (FR-AUTH-07).
 */
export default function AccountPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [history, setHistory] = useState<YearHistory | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getMe().then(setMe).catch((err) => setError(errorMessage(err)));
    getHistory().then(setHistory).catch(() => undefined);
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setNotice(null);

    if (password !== confirm) {
      setError("Those two passwords do not match.");
      return;
    }

    setBusy(true);
    try {
      await changePassword(password);
      setNotice("Password changed.");
      setPassword("");
      setConfirm("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="font-heading text-lg font-semibold">Account</h1>

      {me && (
        <Card>
          <CardContent className="space-y-1 p-4 text-sm">
            <p className="font-medium">{me.display_name}</p>
            <p className="text-muted-foreground">{me.email}</p>
            <p className="text-muted-foreground capitalize">Role: {me.role}</p>
          </CardContent>
        </Card>
      )}

      {/* FR-BAL-08 — consumption history for the current year. */}
      {history && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Your {history.year}</CardTitle>
            <CardDescription>Days taken, month by month.</CardDescription>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="py-2 font-medium">Month</th>
                  {CATEGORIES.map((category) => (
                    <th key={category} className="py-2 text-right font-medium">
                      {CATEGORY_LABEL[category]}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y">
                {Object.entries(history.months)
                  // A year of empty rows tells nobody anything; show the months
                  // where something actually happened.
                  .filter(([, days]) => CATEGORIES.some((c) => Number(days[c]) > 0))
                  .map(([period, days]) => (
                    <tr key={period}>
                      <td className="py-2">
                        {new Date(`${period}-01T00:00:00`).toLocaleDateString("en-GB", {
                          month: "long",
                        })}
                      </td>
                      {CATEGORIES.map((category) => (
                        <td key={category} className="py-2 text-right tabular-nums">
                          {days[category]}
                        </td>
                      ))}
                    </tr>
                  ))}
              </tbody>
            </table>
            {Object.values(history.months).every((days) =>
              CATEGORIES.every((c) => Number(days[c]) === 0)
            ) && (
              <p className="py-3 text-sm text-muted-foreground">
                You have not taken any leave this year.
              </p>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Change password</CardTitle>
          <CardDescription>At least 8 characters.</CardDescription>
        </CardHeader>
        <CardContent>
          {error && (
            <div role="alert" className="mb-4 rounded-md bg-destructive/10 p-3 text-sm text-destructive">
              {error}
            </div>
          )}
          {notice && <div className="mb-4 rounded-md bg-muted p-3 text-sm">{notice}</div>}

          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="password">New password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                minLength={8}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirm">Confirm</Label>
              <Input
                id="confirm"
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                minLength={8}
                required
              />
            </div>
            <Button type="submit" disabled={busy || password.length < 8}>
              {busy ? "Saving…" : "Change password"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
