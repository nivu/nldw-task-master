import type { TodaySection, ToReviewSection } from "@/lib/api/crm-types";

/**
 * Today's sections split in two — spec 009 FR-CRMAI-REV-09, plan §5.4.
 *
 * `GET /crm/today` returns To review after the five sections of 007. The
 * five go to `TodayList` unchanged and To review to its own card, so
 * `today-list.tsx` never sees a key it has no title for. The server leaves
 * the section out for a Viewer and while the queue is hidden
 * (FR-CRMAI-FLAG-04), and then `toReview` is null.
 */
export function splitToReview(sections: (TodaySection | ToReviewSection)[]): {
  sections: TodaySection[];
  toReview: ToReviewSection | null;
} {
  const five: TodaySection[] = [];
  let toReview: ToReviewSection | null = null;
  for (const section of sections) {
    if (section.key === "to_review") toReview = section;
    else five.push(section);
  }
  return { sections: five, toReview };
}
