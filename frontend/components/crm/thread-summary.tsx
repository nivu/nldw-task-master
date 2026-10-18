"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

import { AiMarker } from "@/components/crm/ai-marker";
import type { CrmThreadSummaryBody, ThreadSentiment } from "@/lib/api/crm-types";
import { type CrmTimezone, formatDate } from "@/lib/crm/format";
import { cn } from "@/lib/utils";

/**
 * An email thread's summary — spec 009 FR-CRMAI-SUM-04, -08, FR-CRMAI-LLM-04.
 *
 * Collapsed to one line with the AI marker; expanded it shows the summary,
 * the other side's asks, Nunnari's commitments (owner and date when stated)
 * and the sentiment. The email itself is not stored, so this is all there
 * is. Every generated string is rendered as a text node, never as HTML.
 */

const SENTIMENT_TEXT: Record<ThreadSentiment, string> = {
  positive: "Positive",
  neutral: "Neutral",
  concern: "Concern",
};

function commitmentLine(
  commitment: CrmThreadSummaryBody["commitments"][number],
  timeZone: CrmTimezone
): string {
  const extra = [
    commitment.owner_name,
    commitment.due_on ? `by ${formatDate(commitment.due_on, timeZone)}` : null,
  ].filter(Boolean);
  return extra.length ? `${commitment.text} (${extra.join(", ")})` : commitment.text;
}

export function ThreadSummary({
  summary,
  subject,
  timeZone,
}: {
  summary: CrmThreadSummaryBody;
  /** The thread's subject, named in the marker's provenance. */
  subject?: string | null;
  timeZone: CrmTimezone;
}) {
  const [open, setOpen] = useState(false);
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          <Chevron className="size-3.5" aria-hidden />
          {open ? "Hide summary" : "Summary"}
        </button>
        <AiMarker ai={summary.ai} timeZone={timeZone} label={subject || undefined} />
        <span
          className={cn(
            "text-xs",
            summary.sentiment === "concern" ? "text-destructive" : "text-muted-foreground"
          )}
        >
          Sentiment: {SENTIMENT_TEXT[summary.sentiment]}
        </span>
      </div>
      {open && (
        <div className="space-y-2 rounded-md bg-muted/40 p-2 text-sm">
          <p className="whitespace-pre-line break-words">{summary.summary}</p>
          {summary.asks.length > 0 && (
            <div>
              <p className="text-xs font-medium text-muted-foreground">Their asks</p>
              <ul className="list-disc space-y-0.5 pl-5">
                {summary.asks.map((ask, i) => (
                  <li key={i} className="break-words">
                    {ask}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {summary.commitments.length > 0 && (
            <div>
              <p className="text-xs font-medium text-muted-foreground">Our commitments</p>
              <ul className="list-disc space-y-0.5 pl-5">
                {summary.commitments.map((commitment, i) => (
                  <li key={i} className="break-words">
                    {commitmentLine(commitment, timeZone)}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
