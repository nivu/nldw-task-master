"use client";

import { useState, useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";

/**
 * A year frame shared by every month-based view — spec 006 FR-YEAR.
 *
 * Calendar year (January to December) or financial year (April to March),
 * with a year to step through. The choice is remembered on this device so
 * switching to the financial year on one screen carries to the next.
 */
export type FrameMode = "calendar" | "financial";

export interface YearFrame {
  mode: FrameMode;
  /** The year the frame starts in: 2026 means Jan–Dec 2026, or Apr 2026–Mar 2027. */
  year: number;
  start: string; // YYYY-MM
  end: string; // YYYY-MM
  months: string[]; // twelve YYYY-MM
  label: string;
  setMode: (mode: FrameMode) => void;
  shift: (delta: number) => void;
}

const KEY = "portal.year-frame";
const listeners = new Set<() => void>();

function readMode(): FrameMode {
  try {
    const saved = window.localStorage.getItem(KEY);
    return saved === "financial" ? "financial" : "calendar";
  } catch {
    return "calendar";
  }
}

function writeMode(mode: FrameMode) {
  try {
    window.localStorage.setItem(KEY, mode);
  } catch {
    // Storage may be unavailable; the choice just will not persist.
  }
  listeners.forEach((fn) => fn());
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  window.addEventListener("storage", fn);
  return () => {
    listeners.delete(fn);
    window.removeEventListener("storage", fn);
  };
}

function frameFor(mode: FrameMode, year: number) {
  const startMonth = mode === "financial" ? 4 : 1;
  const months: string[] = [];
  for (let i = 0; i < 12; i += 1) {
    const m = startMonth + i;
    const y = year + Math.floor((m - 1) / 12);
    months.push(`${y}-${String(((m - 1) % 12) + 1).padStart(2, "0")}`);
  }
  return {
    start: months[0],
    end: months[11],
    months,
    label: mode === "financial" ? `FY ${year}–${String(year + 1).slice(2)}` : `${year}`,
  };
}

function defaultYear(mode: FrameMode): number {
  const now = new Date();
  return mode === "financial" && now.getMonth() < 3 ? now.getFullYear() - 1 : now.getFullYear();
}

export function useYearFrame(): YearFrame {
  // The mode lives in localStorage and is read through an external store, so
  // the server render and the first client render agree ("calendar") and the
  // saved choice applies without an effect that sets state.
  const mode = useSyncExternalStore(subscribe, readMode, () => "calendar" as FrameMode);
  // Steps away from the current year, so a saved financial-year mode still
  // lands on the right year without recomputing state when it loads.
  const [offset, setOffset] = useState(0);
  const year = defaultYear(mode) + offset;

  return {
    mode,
    year,
    ...frameFor(mode, year),
    setMode: (next) => {
      writeMode(next);
      setOffset(0);
    },
    shift: (delta) => setOffset((o) => o + delta),
  };
}

export function YearFrameControl({ frame }: { frame: YearFrame }) {
  return (
    <div className="flex flex-wrap items-center gap-1">
      <Button variant={frame.mode === "calendar" ? "secondary" : "outline"} size="sm" onClick={() => frame.setMode("calendar")}>
        Calendar year
      </Button>
      <Button variant={frame.mode === "financial" ? "secondary" : "outline"} size="sm" onClick={() => frame.setMode("financial")}>
        Financial year
      </Button>
      <Button variant="outline" size="sm" onClick={() => frame.shift(-1)} aria-label="Previous year">
        ‹
      </Button>
      <span className="min-w-20 text-center text-sm font-medium tabular-nums">{frame.label}</span>
      <Button variant="outline" size="sm" onClick={() => frame.shift(1)} aria-label="Next year">
        ›
      </Button>
    </div>
  );
}
