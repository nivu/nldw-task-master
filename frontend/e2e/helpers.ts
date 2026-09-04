import { expect, type Page } from "@playwright/test";

/**
 * The people seeded by `supabase/seed.sql`. Every password is `portal123`,
 * which is why that file says never to do this anywhere real.
 */
export const PEOPLE = {
  admin: "vinita@nunnari.example",
  lead: "devansh.nl@gmail.com",
  user: "deepika.nl@gmail.com",
  otherUser: "tarun.nl@gmail.com",
} as const;

export const PASSWORD = "portal123";

/**
 * Sign in as one of the seeded accounts.
 *
 * Uses the password form, which is a local-development affordance only —
 * production is Google-only (FR-AUTH-08) and OAuth cannot be driven by a
 * headless browser. Two things must be true for this to work locally:
 *
 *   frontend/.env       NEXT_PUBLIC_ENABLE_PASSWORD_LOGIN=true
 *   supabase/config.toml  password sign-in enabled, via
 *                         backend/scripts/ops/local_password_auth.sh on
 *
 * The check below turns "every test fails on a missing selector" into one
 * sentence naming the actual cause.
 */
export async function signIn(page: Page, email: string) {
  await page.goto("/auth/login");

  if ((await page.locator("#password").count()) === 0) {
    throw new Error(
      "No password form on the sign-in page. These tests need the local " +
        "development fallback: set NEXT_PUBLIC_ENABLE_PASSWORD_LOGIN=true in " +
        "frontend/.env, run backend/scripts/ops/local_password_auth.sh on, and " +
        "restart Supabase (the auth flag is read at startup)."
    );
  }

  await page.fill("#email", email);
  await page.fill("#password", PASSWORD);
  await page.click('button[type="submit"]');
  // FR-CAL-01 — the calendar is the primary view after sign-in.
  await page.waitForURL("**/calendar");
  await expect(page.getByText(/Work from home/).first()).toBeVisible();
}

/**
 * A day cell that can actually be booked, chosen from the END of the month.
 *
 * Deliberately not "the first open day": the tests write real rows, so the
 * earliest days fill up as the suite runs and re-runs. Working backwards keeps
 * a fresh target available without needing a database reset between runs.
 */
export async function openLastBookableDay(page: Page) {
  const marked = await page.evaluate(() => {
    const grid = [...document.querySelectorAll(".grid.grid-cols-7")].pop();
    if (!grid) return null;
    const open = [...grid.querySelectorAll("button:not([disabled])")].filter((cell) =>
      /^\d+$/.test((cell.textContent || "").trim())
    );
    const cell = open[open.length - 1];
    if (!cell) return null;
    cell.setAttribute("data-e2e", "target-day");
    return cell.textContent?.trim() ?? null;
  });

  if (marked) await page.click('[data-e2e="target-day"]');
  return marked;
}

/**
 * Re-open a day cell by its number.
 *
 * `openLastBookableDay` tags its cell with a data attribute, but saving a
 * booking reloads the calendar and React re-renders the grid — so that
 * attribute is gone and any later click on it silently misses. Anything that
 * needs the same cell again has to find it afresh.
 */
export async function openDayNumbered(page: Page, dayNumber: string) {
  const marked = await page.evaluate((wanted) => {
    const grid = [...document.querySelectorAll(".grid.grid-cols-7")].pop();
    if (!grid) return false;
    const cell = [...grid.querySelectorAll("button")].find(
      (b) => (b.textContent || "").trim().startsWith(wanted) && !(b as HTMLButtonElement).disabled
    );
    if (!cell) return false;
    cell.setAttribute("data-e2e", "reopen");
    return true;
  }, dayNumber);

  if (marked) await page.click('[data-e2e="reopen"]');
  return marked;
}
