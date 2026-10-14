/**
 * CRM display formatting — spec 007 FR-CRM-FX-06, FR-CRM-ACC-15,
 * FR-CRM-UI-07; plan §5.9.
 *
 * Dates read "14 Oct 2026" in the viewer's own CRM time zone. INR follows the
 * viewer's amount format: Indian short ("₹12 L", "₹1.2 Cr") or full Indian
 * grouping ("₹12,34,567"); AUD and USD use Western grouping. Amounts arrive
 * as decimal strings and stay strings until this last step, which is display
 * only: nothing formatted here is ever added up.
 */

import type { MePreferences } from "@/lib/api/types";

export type CrmTimezone = MePreferences["timezone"];
export type NumberFormat = MePreferences["number_format"];
export type Currency = "INR" | "AUD" | "USD";

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const LAKH = 100_000;
const CRORE = 10_000_000;
const SYMBOL: Record<Currency, string> = { INR: "₹", AUD: "A$", USD: "US$" };

/**
 * "14 Oct 2026". A timestamp is shown on the day it falls in the viewer's
 * zone; a date-only value ("2026-10-14") is a calendar day already and is
 * never shifted by a zone.
 */
export function formatDate(value: string | Date, timeZone: CrmTimezone): string {
  const dateOnly = typeof value === "string" && DATE_ONLY.test(value);
  const when =
    typeof value === "string" ? new Date(dateOnly ? `${value}T00:00:00Z` : value) : value;
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: dateOnly ? "UTC" : timeZone,
  }).format(when);
}

/** Up to two decimals, trailing zeros dropped: 12 → "12", 1.25 → "1.25". */
function short(value: number): string {
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value);
}

/**
 * An amount in its own currency. `numberFormat` applies to INR only; AUD and
 * USD always use Western grouping (FR-CRM-FX-06).
 */
export function formatAmount(
  value: string,
  currency: Currency,
  numberFormat: NumberFormat = "indian_short"
): string {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return value;
  const sign = amount < 0 ? "-" : "";
  const abs = Math.abs(amount);
  if (currency !== "INR") {
    const western = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(abs);
    return `${sign}${SYMBOL[currency]}${western}`;
  }
  if (numberFormat === "indian_short" && abs >= CRORE) {
    return `${sign}₹${short(abs / CRORE)} Cr`;
  }
  if (numberFormat === "indian_short" && abs >= LAKH) {
    return `${sign}₹${short(abs / LAKH)} L`;
  }
  const full = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(abs);
  return `${sign}₹${full}`;
}
