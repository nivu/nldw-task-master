"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage, getMyReview, saveMyReview } from "@/lib/api/portal";
import type { Review } from "@/lib/api/types";
import { useAsync } from "@/lib/use-async";

/**
 * My quarter — spec 006 FR-REV.
 *
 * The person's own hours and every note they wrote, so the summary starts
 * from what was actually done rather than from memory. Their words; the lead
 * reads them and closes the quarter. No ratings anywhere.
 */
function currentQuarter(): string {
  const now = new Date();
  return `${now.getFullYear()}-Q${Math.floor(now.getMonth() / 3) + 1}`;
}

function shiftQuarter(q: string, delta: number): string {
  const [y, n] = q.split("-Q").map(Number);
  const idx = y * 4 + (n - 1) + delta;
  return `${Math.floor(idx / 4)}-Q${(idx % 4) + 1}`;
}

export default function ReviewPage() {
  const [quarter, setQuarter] = useState(currentQuarter());
  const { data, error, setError, reload } = useAsync<Review>(() => getMyReview(quarter), [quarter]);

  if (error && !data) {
    return <div role="alert" className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">{error}</div>;
  }
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex-1">
          <h1 className="font-heading text-lg font-semibold">Your quarter</h1>
          <p className="text-sm text-muted-foreground">
            {data.quarter} · {data.first} → {data.last} · {data.total_hours} hours logged
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => setQuarter(shiftQuarter(quarter, -1))}>
          Earlier
        </Button>
        <Button variant="outline" size="sm" onClick={() => setQuarter(shiftQuarter(quarter, 1))}>
          Later
        </Button>
      </div>

      {error && <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{error}</div>}

      <Editor key={data.quarter} data={data} onSaved={reload} onError={setError} />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Where the hours went</CardTitle>
        </CardHeader>
        <CardContent className="divide-y p-0">
          {data.hours.length === 0 && <p className="p-4 text-sm text-muted-foreground">Nothing logged this quarter.</p>}
          {data.hours.map((row) => (
            <div key={row.name} className="flex items-center gap-3 p-3 text-sm">
              <span className="flex-1">{row.name}</span>
              <span className="tabular-nums">{row.hours}h</span>
            </div>
          ))}
        </CardContent>
      </Card>

      {data.notes.map((group) => (
        <Card key={group.name}>
          <CardHeader>
            <CardTitle className="text-base">{group.name}</CardTitle>
            <CardDescription>What you wrote at the time.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {group.months.map((month) => (
              <div key={month.period} className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">
                  {new Date(`${month.period}-01T00:00:00`).toLocaleDateString("en-GB", { month: "long", year: "numeric" })}
                </p>
                <ul className="space-y-1 text-sm">
                  {month.entries.map((entry, i) => (
                    <li key={i} className="flex gap-2">
                      <span className="w-20 shrink-0 tabular-nums text-muted-foreground">{entry.date.slice(5)}</span>
                      <span>{entry.note}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function Editor({ data, onSaved, onError }: { data: Review; onSaved: () => void; onError: (m: string) => void }) {
  const [summary, setSummary] = useState(data.summary);
  const [busy, setBusy] = useState(false);
  const closed = Boolean(data.closed_at);

  async function save(submit: boolean) {
    setBusy(true);
    try {
      await saveMyReview({ quarter: data.quarter, summary, submit });
      onSaved();
    } catch (err) {
      onError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <CardTitle className="text-base">In your words</CardTitle>
          {data.submitted_at && <Badge variant="secondary">submitted</Badge>}
          {closed && <Badge variant="outline">closed by your lead</Badge>}
        </div>
        <CardDescription>
          What went well, what you learned, what you would do differently. Your lead reads this
          alongside the figures below. Nobody scores it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Textarea
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
          rows={8}
          maxLength={8000}
          disabled={closed}
          placeholder="Start from the notes below…"
        />
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => save(false)} disabled={busy || closed}>
            Save draft
          </Button>
          <Button onClick={() => save(true)} disabled={busy || closed || !summary.trim()}>
            {data.submitted_at ? "Update submission" : "Submit"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
