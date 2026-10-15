"use client";

import { useEffect, useState } from "react";

import { useYearFrame, YearFrameControl } from "@/components/shared/year-frame";
import { StackedByMonth } from "@/components/shared/charts";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import Link from "next/link";

import { TokenManager } from "@/components/shared/token-manager";
import { errorMessage, getHistory, getMe, getMyFeed, rotateMyFeed } from "@/lib/api/portal";
import type { Category, Me, YearHistory } from "@/lib/api/types";
import { useAsync } from "@/lib/use-async";
import { CATEGORY_LABEL } from "@/lib/api/types";

const CATEGORIES: Category[] = ["wfh", "casual", "sick"];
const HISTORY_COLOUR: Record<Category, string> = {
  wfh: "#0ea5e9",
  casual: "#8b5cf6",
  sick: "#f43f5e",
  compoff: "#10b981",
};

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
  // Spec 006 §Y — calendar year or financial year, remembered on this device.
  const frame = useYearFrame();

  useEffect(() => {
    getMe().then(setMe).catch((err) => setError(errorMessage(err)));
  }, []);

  useEffect(() => {
    getHistory({ start: frame.start, end: frame.end }).then(setHistory).catch(() => undefined);
  }, [frame.start, frame.end]);

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
            {me.account_type === "staff" && (
              <p className="text-muted-foreground capitalize">Role: {me.role}</p>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Your quarter</CardTitle>
          <CardDescription>
            Your hours and notes for the quarter, and a place to write it up in your own words for
            your lead.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Link href="/review" className="text-sm underline underline-offset-2">
            Open the quarter in review →
          </Link>
        </CardContent>
      </Card>

      <FeedPanel />

      <TokenManager />

      {/* FR-BAL-08 — consumption history for the current year. */}
      {history && (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex-1">
                <CardTitle className="text-base">Your {frame.label}</CardTitle>
                <CardDescription>Days taken, month by month.</CardDescription>
              </div>
              <YearFrameControl frame={frame} />
            </div>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <StackedByMonth
              months={Object.keys(history.months)}
              series={CATEGORIES.map((c) => ({
                name: CATEGORY_LABEL[c],
                values: Object.values(history.months).map((days) => Number(days[c] ?? 0)),
                colour: HISTORY_COLOUR[c],
              }))}
            />
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
                          year: frame.mode === "financial" ? "2-digit" : undefined,
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

/**
 * Calendar feed — spec 006 FR-FEED.
 *
 * A private address for Google Calendar or any calendar app. Category only,
 * never reasons. Rotating it invalidates the old address at once.
 */
function FeedPanel() {
  const { data, error, setError, reload } = useAsync<{ url: string }>(() => getMyFeed(), []);
  const [copied, setCopied] = useState(false);

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // The address is on screen regardless.
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Leave in your calendar</CardTitle>
        <CardDescription>
          Subscribe to this address in Google Calendar (Other calendars → From URL) to see approved
          leave and holidays for the people you can see in the portal. Categories only — never
          reasons. Treat the address like a password; rotate it if it leaks.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {error && <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{error}</div>}
        {data && (
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 overflow-x-auto rounded bg-muted px-2 py-1 font-mono text-xs">{data.url}</code>
            <Button size="sm" variant="outline" onClick={() => copy(data.url)}>{copied ? "Copied" : "Copy"}</Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={async () => {
                try {
                  await rotateMyFeed();
                  reload();
                } catch (err) {
                  setError(errorMessage(err));
                }
              }}
            >
              Rotate
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
