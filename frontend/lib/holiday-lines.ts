/**
 * Reading a pasted list of holidays — spec 001 FR-HOL-08.
 *
 * One holiday per line, date first: "2026-10-20 Diwali" or "20/10/2026,
 * Diwali". This only shows the admin how each line was read before anything is
 * sent; the backend checks every entry again and decides what is skipped.
 */

export const MAX_HOLIDAY_LINES = 100;
const MAX_NAME = 120;

export interface HolidayLine {
  /** 1-based, as the admin counts lines in the box. */
  line: number;
  text: string;
  date?: string;
  name?: string;
  /** Unreadable — nothing is sent until it is fixed. */
  error?: string;
  /** Readable, but the backend will skip it. */
  skip?: string;
}

const ISO = /^(\d{4})-(\d{1,2})-(\d{1,2})$/;
const DMY = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/;

/** "2026-10-20" or "20/10/2026" as YYYY-MM-DD; null if it is not a real day. */
export function readDate(token: string): string | null {
  let y: number, m: number, d: number;
  const iso = ISO.exec(token);
  const dmy = DMY.exec(token);
  if (iso) [y, m, d] = [Number(iso[1]), Number(iso[2]), Number(iso[3])];
  else if (dmy) [d, m, y] = [Number(dmy[1]), Number(dmy[2]), Number(dmy[3])];
  else return null;
  // 31/02 rolls over to March; a real day survives the round trip.
  const probe = new Date(Date.UTC(y, m - 1, d));
  if (probe.getUTCFullYear() !== y || probe.getUTCMonth() !== m - 1 || probe.getUTCDate() !== d) {
    return null;
  }
  return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/**
 * Every non-blank line, read. `existing` and `locationId` mark the lines the
 * backend will skip, by the same rule it uses: a holiday everywhere covers
 * every location, and one for a location covers only that location.
 */
export function parseHolidayLines(
  text: string,
  existing: { date: string; name: string; location_id?: string | null }[] = [],
  locationId: string | null = null
): HolidayLine[] {
  const lines: HolidayLine[] = [];
  const firstOn = new Map<string, number>();
  text.split(/\r?\n/).forEach((raw, i) => {
    const trimmed = raw.trim();
    if (!trimmed) return;
    const line: HolidayLine = { line: i + 1, text: trimmed };
    lines.push(line);

    const match = /^([^\s,;]+)[\s,;]+(.+)$/.exec(trimmed);
    if (!match) {
      line.error = "Needs a date and then a name.";
      return;
    }
    const date = readDate(match[1]);
    const name = match[2].trim();
    if (!date) {
      line.error = `"${match[1]}" is not a date. Write 2026-10-20 or 20/10/2026.`;
      return;
    }
    if (name.length > MAX_NAME) {
      line.error = `The name is longer than ${MAX_NAME} characters.`;
      return;
    }
    line.date = date;
    line.name = name;

    const taken = existing.find(
      (h) => h.date === date && (locationId === null || !h.location_id || h.location_id === locationId)
    );
    if (taken) line.skip = `Already a holiday (${taken.name}).`;
    else if (firstOn.has(date)) line.skip = `Same date as line ${firstOn.get(date)}.`;
    else firstOn.set(date, line.line);
  });
  return lines;
}
