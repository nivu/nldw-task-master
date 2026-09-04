"use client";

import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { BookingDialog } from "@/components/portal/booking-dialog";
import { getCalendar } from "@/lib/api/portal";
import { useAsync } from "@/lib/use-async";
import type { CalendarMonth, Category, DayCell } from "@/lib/api/types";
import { CATEGORY_LABEL, CATEGORY_SHORT } from "@/lib/api/types";
import { cn } from "@/lib/utils";

/**
 * The calendar — FR-CAL-01 through FR-CAL-08.
 *
 * Every judgement about a day arrives from the server already made: whether it
 * is bookable, whether it is locked, what is on it. That is deliberate. NFR-04
 * puts the lock decision server-side, and the surest way to keep it there is
 * to give the browser the answer rather than the ingredients — there is no
 * date arithmetic anywhere in this file.
 */

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** FR-CAL-02 — the three categories must be told apart at a glance. */
const CATEGORY_STYLE: Record<Category, string> = {
  wfh: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200",
  casual: "bg-violet-100 text-violet-900 dark:bg-violet-950 dark:text-violet-200",
  sick: "bg-rose-100 text-rose-900 dark:bg-rose-950 dark:text-rose-200",
};

export default function CalendarPage() {
  const [period, setPeriod] = useState<string>("");
  const [selected, setSelected] = useState<DayCell | null>(null);

  const { data, error, reload } = useAsync<CalendarMonth>(
    () => getCalendar(period || undefined),
    [period]
  );

  function shiftMonth(delta: number) {
    if (!data) return;
    const [year, month] = data.period.split("-").map(Number);
    const shifted = new Date(year, month - 1 + delta, 1);
    setPeriod(
      `${shifted.getFullYear()}-${String(shifted.getMonth() + 1).padStart(2, "0")}`
    );
  }

  if (error) {
    return (
      <div role="alert" className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">
        {error}
      </div>
    );
  }

  if (!data) {
    return <p className="text-sm text-muted-foreground">Loading your calendar…</p>;
  }

  const monthLabel = new Date(`${data.period}-01T00:00:00`).toLocaleDateString("en-GB", {
    month: "long",
    year: "numeric",
  });

  return (
    <div className="space-y-6">
      {/* FR-BAL-06 — allowance, used and remaining, per category. Kept above
          the grid so the numbers are visible before a day is chosen. */}
      <div className="grid grid-cols-3 gap-3">
        {data.balances.map((balance) => (
          <Card key={balance.category}>
            <CardContent className="p-3">
              <p className="text-xs text-muted-foreground">
                {CATEGORY_LABEL[balance.category]}
              </p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">{balance.remaining}</p>
              <p className="text-xs text-muted-foreground">
                {balance.used} used of {balance.allowance}
                {balance.opening !== "0.0" && ` · ${balance.opening} carried in`}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="flex items-center justify-between">
        <h1 className="font-heading text-lg font-semibold">{monthLabel}</h1>
        {/* FR-CAL-07 */}
        <div className="flex gap-1">
          <Button variant="outline" size="icon" onClick={() => shiftMonth(-1)} aria-label="Previous month">
            <ChevronLeft className="size-4" />
          </Button>
          <Button variant="outline" size="icon" onClick={() => shiftMonth(1)} aria-label="Next month">
            <ChevronRight className="size-4" />
          </Button>
        </div>
      </div>

      <div>
        <div className="grid grid-cols-7 gap-1 pb-1">
          {WEEKDAYS.map((day) => (
            <div key={day} className="text-center text-[11px] font-medium text-muted-foreground">
              {day}
            </div>
          ))}
        </div>

        <div className="grid grid-cols-7 gap-1">
          {data.weeks.flat().map((cell, index) =>
            cell === null ? (
              <div key={`pad-${index}`} />
            ) : (
              <DayButton key={cell.date} cell={cell} onSelect={() => setSelected(cell)} />
            )
          )}
        </div>
      </div>

      <Legend />

      <BookingDialog
        day={selected}
        balances={data.balances}
        onClose={() => setSelected(null)}
        onSaved={reload}
      />
    </div>
  );
}

function DayButton({ cell, onSelect }: { cell: DayCell; onSelect: () => void }) {
  const booking = cell.booking;
  const dayNumber = Number(cell.date.slice(-2));

  // A day can be opened if it is bookable, or if it already holds a booking
  // the person may still edit (FR-BOOK-07). A locked day with a booking stays
  // readable but inert (FR-CAL-08).
  const interactive = cell.bookable || Boolean(booking?.can_edit);

  return (
    <button
      type="button"
      onClick={interactive ? onSelect : undefined}
      disabled={!interactive}
      title={cell.holiday ?? undefined}
      className={cn(
        "flex min-h-16 flex-col items-start gap-0.5 rounded-md border p-1.5 text-left transition-colors",
        "disabled:cursor-default",
        // Was bg-muted/40, which rendered almost identically to a working day.
        cell.is_weekend && "bg-muted",
        cell.holiday && "bg-amber-50 dark:bg-amber-950/40",
        // FR-CAL-08 — a locked day must look different from an editable one.
        cell.locked && "opacity-55",
        interactive && "hover:border-primary/60 hover:bg-muted/60",
        cell.is_today ? "border-primary ring-1 ring-primary" : "border-border"
      )}
    >
      <span
        className={cn(
          "text-xs tabular-nums",
          cell.is_today ? "font-bold text-primary" : "text-muted-foreground"
        )}
      >
        {dayNumber}
      </span>

      {/* FR-CAL-04 — holidays are labelled with their name. */}
      {cell.holiday && (
        <span className="line-clamp-2 text-[10px] leading-tight font-medium text-amber-800 dark:text-amber-300">
          {cell.holiday}
        </span>
      )}

      {booking?.category && (
        <span
          title={`${CATEGORY_LABEL[booking.category]}${
            booking.duration === "0.5" ? ", half day" : ""
          }`}
          className={cn(
            // A phone column is about 40px wide inside its padding, which is
            // not enough for "Casual ½" — earlier attempts truncated it to
            // "Casu…" and then to "C…", each less useful than the last.
            //
            // So the category is carried by COLOUR on a phone (FR-CAL-02, with
            // the legend below doing the mapping) and the word appears only
            // where there is room for it. The half-day marker (FR-CAL-03) is a
            // single glyph that fits at every width.
            "w-full truncate rounded px-0.5 py-0.5 text-[9px] leading-tight font-medium",
            CATEGORY_STYLE[booking.category]
          )}
        >
          <span className="hidden sm:inline">{CATEGORY_SHORT[booking.category]} </span>
          {booking.duration === "0.5" ? "½" : <span className="sm:hidden">&nbsp;</span>}
        </span>
      )}

      {booking && !booking.category && (
        <span className="w-full truncate rounded bg-neutral-200 px-0.5 py-0.5 text-[9px] leading-tight font-medium dark:bg-neutral-800">
          Absent
        </span>
      )}

      {/* FR-CAL-06 — the booking's status on the day itself. */}
      {booking && booking.status !== "approved" && (
        <span className="truncate text-[8px] uppercase tracking-wide text-muted-foreground">
          {booking.status}
        </span>
      )}

      {/* Spec A-21. Somebody finding leave on their own calendar that they do
          not remember booking deserves to see where it came from, rather than
          being left to wonder. */}
      {booking?.backfilled && (
        <span className="truncate text-[8px] uppercase tracking-wide text-amber-700 dark:text-amber-400">
          by admin
        </span>
      )}
    </button>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
      {(Object.keys(CATEGORY_LABEL) as Category[]).map((category) => (
        <span key={category} className="flex items-center gap-1.5">
          <span className={cn("size-2.5 rounded-sm", CATEGORY_STYLE[category])} />
          {CATEGORY_LABEL[category]}
        </span>
      ))}
      <span className="flex items-center gap-1.5">
        <span className="size-2.5 rounded-sm bg-amber-100 dark:bg-amber-900" />
        Holiday
      </span>
      <span className="flex items-center gap-1.5">
        <span className="size-2.5 rounded-sm border bg-muted/40" />
        Weekend
      </span>
      <span>½ marks a half day.</span>
      <span>&ldquo;by admin&rdquo; was entered on your behalf.</span>
      <span>Faded days are locked — the date has passed.</span>
    </div>
  );
}
