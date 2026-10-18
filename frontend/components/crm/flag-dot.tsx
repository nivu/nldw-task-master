"use client";

import type { CrmFlag, FlagLevel } from "@/lib/api/crm-types";
import { formatDate, type CrmTimezone } from "@/lib/crm/format";
import { cn } from "@/lib/utils";

/**
 * A follow-up or deal-health flag — spec 007 FR-CRM-FLAG-02, -05,
 * FR-CRM-REC-01.
 *
 * A dot with its level written beside it, never colour alone; hovering (or
 * a screen reader) gives each flag's label and detail and when the flags were
 * last computed. The copy says "follow-up" for companies, people and tasks
 * and "deal health" for deals, never bare "health", which is TVA's project
 * health. Labels come from the server, so 009's "Stuck in stage" (FR-CRMAI-
 * RULE-03, only while R-2 and phase 3 are on) shows like any other flag.
 */

const LEVEL_TEXT: Record<FlagLevel, string> = { none: "No flag", amber: "Amber", red: "Red" };

const DOT: Record<FlagLevel, string> = {
  none: "border border-muted-foreground/40 bg-transparent",
  amber: "bg-amber-500",
  red: "bg-red-600",
};

export function flagNoun(recordType: string): string {
  return recordType === "opportunity" ? "Deal health" : "Follow-up";
}

export function FlagDot({
  level,
  flags = [],
  computedAt,
  recordType = "company",
  timeZone,
  showNone = false,
  className,
}: {
  level: FlagLevel | null | undefined;
  flags?: CrmFlag[];
  computedAt?: string | null;
  recordType?: string;
  timeZone: CrmTimezone;
  /** Tables show "No flag"; a record header shows nothing. */
  showNone?: boolean;
  className?: string;
}) {
  const shown: FlagLevel = level ?? "none";
  if (shown === "none" && !showNone) return null;
  const lines = flags.map((f) => `${f.label} (${f.level}): ${f.detail}`);
  if (computedAt) lines.push(`Computed ${formatDate(computedAt, timeZone)}`);
  const summary = `${flagNoun(recordType)}: ${LEVEL_TEXT[shown]}`;
  return (
    <span
      className={cn("inline-flex items-center gap-1.5 text-xs", className)}
      title={[summary, ...lines].join("\n")}
      data-flag-level={shown}
    >
      <span aria-hidden className={cn("inline-block size-2.5 shrink-0 rounded-full", DOT[shown])} />
      <span className={cn(shown === "none" && "text-muted-foreground")}>{LEVEL_TEXT[shown]}</span>
      {lines.length > 0 && <span className="sr-only">{`${summary}. ${lines.join(". ")}`}</span>}
    </span>
  );
}

/** The header chip of a record page (FR-CRM-REC-01): level and the flags. */
export function FlagChip({
  level,
  flags,
  computedAt,
  recordType,
  timeZone,
}: {
  level: FlagLevel | undefined;
  flags: CrmFlag[] | undefined;
  computedAt: string | null | undefined;
  recordType: string;
  timeZone: CrmTimezone;
}) {
  if (!level || level === "none") return null;
  return (
    <span className="inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs">
      <span className="text-muted-foreground">{flagNoun(recordType)}</span>
      <FlagDot
        level={level}
        flags={flags ?? []}
        computedAt={computedAt}
        recordType={recordType}
        timeZone={timeZone}
        className="font-medium"
      />
      {flags && flags.length > 0 && (
        <span className="text-muted-foreground">· {flags.map((f) => f.label).join(", ")}</span>
      )}
    </span>
  );
}
