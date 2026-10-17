"use client";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { AiProvenance, AiSourceKind } from "@/lib/api/crm-types";
import { type CrmTimezone, formatDate } from "@/lib/crm/format";
import { cn } from "@/lib/utils";

/**
 * The AI marker — spec 009 P-2, FR-CRMAI-LLM-04; plan §5.9.
 *
 * A small "AI" badge beside every generated value. Hovering it, or reaching
 * it with the keyboard, shows where the value came from, the model and when
 * it was made. A deterministic rule has no model, so only its source shows.
 * Everything is a text node: generated text is never rendered as HTML.
 */

const SOURCE_TEXT: Record<AiSourceKind, string> = {
  email_thread: "From an email thread",
  meeting: "From a meeting",
  enrichment: "From the company's website",
  ai_field: "An AI field",
  rule: "From a follow-up rule",
};

export function AiMarker({
  ai,
  timeZone,
  label,
  className,
}: {
  ai: AiProvenance;
  timeZone: CrmTimezone;
  /** The source by name, such as the thread's subject. */
  label?: string;
  className?: string;
}) {
  const lines = [label ? `${SOURCE_TEXT[ai.source_kind]}: ${label}` : SOURCE_TEXT[ai.source_kind]];
  if (ai.model) lines.push(`Model: ${ai.model}`);
  if (ai.generated_at) lines.push(`Made ${formatDate(ai.generated_at, timeZone)}`);
  return (
    <Tooltip>
      <TooltipTrigger
        aria-label={`AI. ${lines.join(". ")}`}
        className={cn(
          "inline-flex shrink-0 items-center rounded border px-1 text-[10px] leading-4 font-semibold text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
          className
        )}
      >
        AI
      </TooltipTrigger>
      <TooltipContent className="flex-col items-start gap-0.5">
        {lines.map((line) => (
          <span key={line}>{line}</span>
        ))}
      </TooltipContent>
    </Tooltip>
  );
}
