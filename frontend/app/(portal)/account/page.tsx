"use client";

import { useEffect, useState } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { errorMessage, getHistory, getMe } from "@/lib/api/portal";
import type { Category, Me, YearHistory } from "@/lib/api/types";
import { CATEGORY_LABEL } from "@/lib/api/types";

const CATEGORIES: Category[] = ["wfh", "casual", "sick"];

/**
 * The person's own account — FR-BAL-08.
 *
 * Read-only. Self-service profile editing is an explicit non-goal for V1
 * (spec §2.2), so name, role and lead are shown but not editable, and since
 * sign-in moved to Google only (FR-AUTH-08) there is no password to change
 * either — FR-AUTH-05 was withdrawn with it. What remains is this person's own
 * consumption history for the year.
 */
export default function AccountPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [history, setHistory] = useState<YearHistory | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getMe().then(setMe).catch((err) => setError(errorMessage(err)));
    getHistory().then(setHistory).catch(() => undefined);
  }, []);

  return (
    <div className="space-y-6">
      <h1 className="font-heading text-lg font-semibold">Account</h1>

      {error && (
        <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

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
        <CardContent className="p-4 text-sm text-muted-foreground">
          {/* FR-AUTH-05 was withdrawn when sign-in moved to Google only
              (FR-AUTH-08). There is no password here to change, and saying so
              is better than leaving people hunting for the setting. */}
          You sign in with Google, so there is no portal password to change.
          Manage that account in your Google settings.
        </CardContent>
      </Card>
    </div>
  );
}
