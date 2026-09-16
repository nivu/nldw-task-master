"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  closeReview,
  confirmWeek,
  decideCompoff,
  errorMessage,
  getTeamCompoff,
  getTeamReviews,
  getTeamWeeks,
  reopenWeek,
} from "@/lib/api/portal";
import type { CompoffClaim, TeamReviews, TeamWeek } from "@/lib/api/types";
import { useAsync } from "@/lib/use-async";
import { cn } from "@/lib/utils";
import { isoDate } from "@/lib/dates";

/** Spec 006 FR-SIGN — a lead confirms each report's week. */
export function WeekSignoff({ onError }: { onError: (m: string) => void }) {
  const [offset, setOffset] = useState(0);
  const monday = (() => {
    const now = new Date();
    const d = new Date(now);
    d.setDate(now.getDate() - ((now.getDay() + 6) % 7) + offset * 7);
    return isoDate(d);
  })();
  const { data, reload } = useAsync<TeamWeek>(() => getTeamWeeks(monday), [monday]);

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex-1">
            <CardTitle className="text-base">Weekly sign-off</CardTitle>
            <CardDescription>
              Week of {new Date(`${monday}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "long" })}.
              Confirm each report&apos;s week once it looks right; anything unconfirmed is confirmed
              automatically when the edit window closes.
            </CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={() => setOffset(offset - 1)}>Earlier</Button>
          <Button variant="outline" size="sm" onClick={() => setOffset(offset + 1)} disabled={offset >= 0}>Later</Button>
        </div>
      </CardHeader>
      <CardContent className="divide-y p-0">
        {!data && <p className="p-4 text-sm text-muted-foreground">Loading…</p>}
        {data?.people.map((p) => (
          <div key={p.user_id} className="flex flex-wrap items-center gap-3 p-3 text-sm">
            <span className="font-medium">{p.display_name}</span>
            <span className="tabular-nums text-muted-foreground">{p.total}h</span>
            {p.missing_days.length > 0 && (
              <Badge variant="outline">{p.missing_days.length} day{p.missing_days.length === 1 ? "" : "s"} missing</Badge>
            )}
            {p.confirmation ? (
              <Badge variant="secondary">{p.confirmation.status === "auto" ? "auto-confirmed" : "confirmed"}</Badge>
            ) : (
              <Badge variant="outline">open</Badge>
            )}
            <div className="ml-auto flex gap-1">
              {p.confirmation ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={async () => {
                    try {
                      await reopenWeek(p.user_id, monday);
                      reload();
                    } catch (err) {
                      onError(errorMessage(err));
                    }
                  }}
                >
                  Reopen
                </Button>
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={async () => {
                    try {
                      await confirmWeek(p.user_id, monday);
                      reload();
                    } catch (err) {
                      onError(errorMessage(err));
                    }
                  }}
                >
                  Confirm
                </Button>
              )}
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

/** Spec 006 FR-COMP — claims waiting for a decision, then the rest. */
export function CompoffQueue({ onError }: { onError: (m: string) => void }) {
  const { data, reload } = useAsync<CompoffClaim[]>(() => getTeamCompoff(), []);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const pending = (data ?? []).filter((c) => c.status === "pending");
  const rest = (data ?? []).filter((c) => c.status !== "pending").slice(0, 10);

  async function decide(claim: CompoffClaim, approve: boolean) {
    try {
      await decideCompoff(claim.id, approve, notes[claim.id] ?? null);
      reload();
    } catch (err) {
      onError(errorMessage(err));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Comp-off claims</CardTitle>
        <CardDescription>
          Weekend or holiday work your reports want a day back for. An approved day is valid for a
          limited time and is booked like leave, under the comp-off category.
        </CardDescription>
      </CardHeader>
      <CardContent className="divide-y p-0">
        {pending.length === 0 && <p className="p-4 text-sm text-muted-foreground">Nothing waiting.</p>}
        {pending.map((c) => (
          <div key={c.id} className="space-y-2 p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{c.display_name}</span>
              <span className="text-muted-foreground">worked {c.worked_on} · {c.days} day</span>
              {c.note && <span className="italic text-muted-foreground">&ldquo;{c.note}&rdquo;</span>}
            </div>
            <Textarea
              rows={1}
              placeholder="Note (required to reject)"
              value={notes[c.id] ?? ""}
              onChange={(e) => setNotes({ ...notes, [c.id]: e.target.value })}
            />
            <div className="flex gap-2">
              <Button size="sm" onClick={() => decide(c, true)}>Approve</Button>
              <Button size="sm" variant="outline" disabled={!(notes[c.id] ?? "").trim()} onClick={() => decide(c, false)}>
                Reject
              </Button>
            </div>
          </div>
        ))}
        {rest.map((c) => (
          <div key={c.id} className="flex flex-wrap items-center gap-2 p-3 text-xs text-muted-foreground">
            <span>{c.display_name}</span>
            <span>{c.worked_on} · {c.days} day</span>
            <Badge variant="outline">{c.status}</Badge>
            {c.expires_on && c.status === "approved" && <span>valid until {c.expires_on}</span>}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

/** Spec 006 FR-REV — reports' quarterly summaries, in their own words. */
export function QuarterReviews({ onError }: { onError: (m: string) => void }) {
  const [quarter, setQuarter] = useState<string | undefined>(undefined);
  const { data, reload } = useAsync<TeamReviews>(() => getTeamReviews(quarter), [quarter]);
  const shift = (q: string, delta: number) => {
    const [y, n] = q.split("-Q").map(Number);
    const idx = y * 4 + (n - 1) + delta;
    return `${Math.floor(idx / 4)}-Q${(idx % 4) + 1}`;
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex-1">
            <CardTitle className="text-base">Quarter in review{data ? ` · ${data.quarter}` : ""}</CardTitle>
            <CardDescription>
              Each person&apos;s own summary of their quarter, with the hours behind it. Read it, talk
              about it, then close it. There is nothing to score.
            </CardDescription>
          </div>
          {data && (
            <>
              <Button variant="outline" size="sm" onClick={() => setQuarter(shift(data.quarter, -1))}>Earlier</Button>
              <Button variant="outline" size="sm" onClick={() => setQuarter(shift(data.quarter, 1))}>Later</Button>
            </>
          )}
        </div>
      </CardHeader>
      <CardContent className="divide-y p-0">
        {data?.people.map((p) => (
          <div key={p.user_id} className="space-y-2 p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{p.display_name}</span>
              <span className="tabular-nums text-muted-foreground">{p.total_hours}h</span>
              {p.submitted_at ? <Badge variant="secondary">submitted</Badge> : <Badge variant="outline">not yet</Badge>}
              {p.closed_at && <Badge variant="outline">closed</Badge>}
              {p.submitted_at && !p.closed_at && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="ml-auto"
                  onClick={async () => {
                    try {
                      await closeReview(p.user_id, data.quarter);
                      reload();
                    } catch (err) {
                      onError(errorMessage(err));
                    }
                  }}
                >
                  Close
                </Button>
              )}
            </div>
            {p.summary && <p className={cn("whitespace-pre-wrap rounded-md bg-muted/50 p-2")}>{p.summary}</p>}
            {p.hours.length > 0 && (
              <p className="text-xs text-muted-foreground">
                {p.hours.map((h) => `${h.name} ${h.hours}h`).join(" · ")}
              </p>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
