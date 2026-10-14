"use client";

import type { CrmCapacityWeek } from "@/lib/api/crm-types";
import type { MePreferences } from "@/lib/api/types";
import { formatDate } from "@/lib/crm/format";
import { cn } from "@/lib/utils";

/** Up to one decimal, trailing zeros dropped: "84.00" → "84". Display only. */
function hours(value: string): string {
  return `${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 }).format(Number(value))}h`;
}

/**
 * The capacity check — spec 007 FR-CRM-CAP-01 to -03.
 *
 * Per week: the hours tentative projects would need against the hours nobody
 * is yet promised to. Totals only — no person appears anywhere — and a week
 * with fewer than 3 people supplying it says so instead of a figure, so one
 * person's leave cannot be read off it (Q-39). The figures are TVA's forecast's
 * own (FR-CRM-CAP-03).
 */
export function CapacityPanel({
  weeks,
  timeZone,
}: {
  weeks: CrmCapacityWeek[];
  timeZone: MePreferences["timezone"];
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs text-muted-foreground">
            <th className="p-2 font-medium">Week of</th>
            <th className="p-2 text-right font-medium">Tentative work</th>
            <th className="p-2 text-right font-medium">Free capacity</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {weeks.map((week) => (
            <tr key={week.week_start}>
              <td className="whitespace-nowrap p-2">{formatDate(week.week_start, timeZone)}</td>
              {"hidden" in week ? (
                <td colSpan={2} className="p-2 text-right text-muted-foreground">
                  Hidden: {week.hidden}
                </td>
              ) : (
                <>
                  <td className="p-2 text-right tabular-nums">{hours(week.tentative_hours)}</td>
                  <td
                    className={cn(
                      "p-2 text-right tabular-nums",
                      Number(week.free_hours) < Number(week.tentative_hours) &&
                        "font-medium text-amber-700 dark:text-amber-400"
                    )}
                  >
                    {hours(week.free_hours)}
                  </td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
