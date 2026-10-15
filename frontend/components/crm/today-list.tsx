"use client";

import Link from "next/link";

import { FlagDot } from "@/components/crm/flag-dot";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { CrmToday, TodayItem, TodaySectionKey } from "@/lib/api/crm-types";
import { type CrmTimezone, formatDate } from "@/lib/crm/format";
import { OBJECT_PATH } from "@/lib/crm/view-fields";

/**
 * Today's five sections — spec 007 FR-CRM-TODAY-01, -02.
 *
 * One line per item, each opening its record, in the server's order. A task
 * has no page of its own, so its line opens the record it is linked to (or
 * the Tasks list). The two email sections say "Email sync is off" while it
 * is, instead of looking empty.
 */

const TITLES: Record<TodaySectionKey, string> = {
  replies_owed: "Replies I owe",
  next_steps: "Deal next steps",
  tasks: "Tasks for today",
  silent_clients: "Clients gone silent",
  loose_ends: "Loose ends",
};

const EMPTY: Record<TodaySectionKey, string> = {
  replies_owed: "No replies owed.",
  next_steps: "No next step is due.",
  tasks: "Nothing due today.",
  silent_clients: "Nobody has gone silent.",
  loose_ends: "No loose ends.",
};

function href(ws: string, item: TodayItem): string {
  if (item.record_type === "task") {
    return item.opens
      ? `/crm/${ws}/${OBJECT_PATH[item.opens.record_type]}/${item.opens.record_id}`
      : `/crm/${ws}/tasks`;
  }
  return `/crm/${ws}/${OBJECT_PATH[item.record_type]}/${item.record_id}`;
}

export function TodayList({
  workspace,
  today,
  timeZone,
}: {
  workspace: string;
  today: CrmToday;
  timeZone: CrmTimezone;
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {today.sections.map((section) => (
        <Card key={section.key} data-testid={`today-${section.key}`}>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              {TITLES[section.key]}
              {section.items.length > 0 && <Badge variant="secondary">{section.items.length}</Badge>}
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            {section.off_reason ? (
              <p className="text-muted-foreground">{section.off_reason}</p>
            ) : section.items.length === 0 ? (
              <p className="text-muted-foreground">{EMPTY[section.key]}</p>
            ) : (
              <ul className="divide-y">
                {section.items.map((item) => (
                  <li key={`${item.record_type}-${item.record_id}`}>
                    <Link
                      href={href(workspace, item)}
                      className="flex items-start justify-between gap-3 py-2 hover:bg-muted/50"
                    >
                      <span className="min-w-0">
                        <span className="block truncate font-medium">{item.title}</span>
                        {item.detail && (
                          <span className="block truncate text-xs text-muted-foreground">{item.detail}</span>
                        )}
                      </span>
                      {(item.level === "red" || item.level === "amber") && (
                        // Sections 1 and 4 (1b): the flag, red first, in words too.
                        <FlagDot level={item.level} timeZone={timeZone} className="shrink-0" />
                      )}
                      {item.due_on && (
                        <span
                          className={
                            item.level === "overdue"
                              ? "shrink-0 text-xs font-medium text-destructive"
                              : "shrink-0 text-xs text-muted-foreground"
                          }
                        >
                          {item.level === "overdue" ? "Overdue · " : ""}
                          {formatDate(item.due_on, timeZone)}
                        </span>
                      )}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
