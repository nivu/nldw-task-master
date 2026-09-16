/**
 * Local-date formatting.
 *
 * `Date.toISOString()` converts to UTC first, so a local midnight in
 * Asia/Kolkata becomes 18:30 the previous day and the date slips by one.
 * Every "which day is this" question in the browser goes through these.
 */
export function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function isoMonth(d: Date): string {
  return isoDate(d).slice(0, 7);
}
