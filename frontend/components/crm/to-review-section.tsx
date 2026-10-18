"use client";

import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ToReviewSection } from "@/lib/api/crm-types";
import { type CrmTimezone, formatDate } from "@/lib/crm/format";

/**
 * Today's To review — spec 009 FR-CRMAI-REV-09, FR-CRMAI-CHG-03.
 *
 * The sixth section, after the five of 007: how many suggestions wait for
 * the person and the three oldest, each opening the Review Queue. The server
 * sends it only while the queue is shown (FR-CRMAI-FLAG-04), never to a
 * Viewer.
 */
export function ToReviewSectionCard({
  workspace,
  section,
  timeZone,
}: {
  workspace: string;
  section: ToReviewSection;
  timeZone: CrmTimezone;
}) {
  const queue = `/crm/${workspace}/review`;
  return (
    <Card data-testid="today-to_review">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          To review
          {section.count > 0 && <Badge variant="secondary">{section.count}</Badge>}
        </CardTitle>
      </CardHeader>
      <CardContent className="text-sm">
        {section.items.length === 0 ? (
          <p className="text-muted-foreground">Nothing to review.</p>
        ) : (
          <>
            <ul className="divide-y">
              {section.items.map((item) => (
                <li key={item.record_id}>
                  <Link href={queue} className="flex items-start justify-between gap-3 py-2 hover:bg-muted/50">
                    <span className="min-w-0">
                      <span className="block truncate font-medium">{item.title}</span>
                      {item.detail && (
                        <span className="block truncate text-xs text-muted-foreground">{item.detail}</span>
                      )}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {formatDate(item.created_at, timeZone)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
            {section.count > section.items.length && (
              <Link href={queue} className="mt-2 inline-block text-xs underline">
                All {section.count} in Review
              </Link>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
