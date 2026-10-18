"use client";

import type { ContactRecencyLevel, CrmContactRecency } from "@/lib/api/crm-types";

/**
 * Contact recency — spec 009 FR-CRMAI-REL-01 to -03, Q-21; plan §5.10.
 *
 * A header chip on company and person pages: Recent, Fading or Lapsed,
 * worked out by the flags job from stored emails and meeting notes. It is a
 * fact about recent contact, not the person-set Relationship status, so it
 * never says Warm or Cold. With Gmail sync off it says "Meetings only".
 * Shown only while phase 4 is on (the record read sends it then) and once a
 * level has been computed.
 */

const RECENCY_TEXT: Record<ContactRecencyLevel, string> = {
  recent: "Recent",
  fading: "Fading",
  lapsed: "Lapsed",
};

const RECENCY_DETAIL: Record<ContactRecencyLevel, string> = {
  recent: "Emails both ways, or a meeting note, in the last 30 days",
  fading: "Some contact in the last 90 days",
  lapsed: "No contact in the last 90 days",
};

export function ContactRecencyChip({ recency }: { recency: CrmContactRecency | null | undefined }) {
  if (!recency?.level) return null;
  const detail = recency.meetings_only
    ? `${RECENCY_DETAIL[recency.level]}. Counted from meeting notes only: Gmail sync is off.`
    : RECENCY_DETAIL[recency.level];
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs"
      title={detail}
      data-contact-recency={recency.level}
    >
      <span className="text-muted-foreground">Contact recency</span>
      <span className="font-medium">{RECENCY_TEXT[recency.level]}</span>
      {recency.meetings_only && <span className="text-muted-foreground">· Meetings only</span>}
      <span className="sr-only">{`. ${detail}`}</span>
    </span>
  );
}
