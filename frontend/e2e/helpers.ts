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

export async function signIn(page: Page, email: string) {
  await page.goto("/auth/login");
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
