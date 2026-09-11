"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { claimCompoff, errorMessage } from "@/lib/api/portal";
import type { MyCompoff } from "@/lib/api/types";

/**
 * Comp-off — spec 006 FR-COMP.
 *
 * Claim a weekend or holiday you worked; your lead approves; then book the
 * day from the calendar under the comp-off category. Credits lapse after a
 * fixed number of days, and the card says when.
 */
export function CompoffCard({ data, onChanged }: { data: MyCompoff | null; onChanged: () => void }) {
  const [workedOn, setWorkedOn] = useState("");
  const [days, setDays] = useState("1");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          Comp-off
          {data && (
            <span className="ml-2 font-normal text-muted-foreground">
              {data.available} day{data.available === "1.0" || data.available === "1" ? "" : "s"} available
            </span>
          )}
        </CardTitle>
        <CardDescription>
          Worked a weekend or a holiday? Claim the day back. Once your lead approves it, book it
          from the calendar as comp-off within {data?.valid_days ?? 90} days.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{error}</div>}
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            setError(null);
            try {
              await claimCompoff({ worked_on: workedOn, days, note: note.trim() || null });
              setWorkedOn("");
              setNote("");
              onChanged();
            } catch (err) {
              setError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="space-y-1.5">
            <Label className="text-xs" htmlFor="compoff-date">Day worked</Label>
            <Input id="compoff-date" type="date" value={workedOn} onChange={(e) => setWorkedOn(e.target.value)} required />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">How much</Label>
            <select
              className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
              value={days}
              onChange={(e) => setDays(e.target.value)}
            >
              <option value="1">Full day</option>
              <option value="0.5">Half day</option>
            </select>
          </div>
          <div className="min-w-40 flex-1 space-y-1.5">
            <Label className="text-xs" htmlFor="compoff-note">What for</Label>
            <Input id="compoff-note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Release weekend" maxLength={300} />
          </div>
          <Button type="submit" size="sm" disabled={busy || !workedOn}>
            {busy ? "Claiming…" : "Claim"}
          </Button>
        </form>
        {data && data.claims.length > 0 && (
          <div className="space-y-1 text-xs text-muted-foreground">
            {data.claims.slice(-6).reverse().map((c) => (
              <div key={c.id} className="flex flex-wrap items-center gap-2">
                <span className="tabular-nums">{c.worked_on}</span>
                <span>{c.days} day</span>
                <Badge variant={c.status === "approved" ? "secondary" : "outline"}>{c.status}</Badge>
                {c.status === "approved" && c.expires_on && <span>valid until {c.expires_on}</span>}
                {c.decision_note && <span className="italic">&ldquo;{c.decision_note}&rdquo;</span>}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
