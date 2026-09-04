import { expect, test } from "@playwright/test";

import { openDayNumbered, openLastBookableDay, PEOPLE, signIn } from "./helpers";

/**
 * The scenarios from spec §4, driven through the real UI.
 *
 * These cover the things a unit test structurally cannot: that a guard placed
 * in `proxy.ts` is actually wired up, that the roster genuinely omits a reason
 * rather than merely intending to, and that a day the server called
 * non-bookable renders as one you cannot press.
 */

test.describe("signing in", () => {
  test("every guarded route redirects an anonymous visitor (FR-AUTH-01)", async ({ page }) => {
    for (const route of ["/", "/calendar", "/team", "/admin", "/approvals", "/account"]) {
      await page.goto(route);
      await expect(page).toHaveURL(/\/auth\/login/);
    }
  });

  test("the sign-in page offers no way to create an account (FR-AUTH-02)", async ({ page }) => {
    await page.goto("/auth/login");
    // Not a styling check — a sign-up link here would be a route into the
    // system that bypasses the admin, which FR-AUTH-02 forbids outright.
    await expect(page.getByRole("link", { name: /sign up|register|create account/i })).toHaveCount(0);
    await expect(page.getByText(/Accounts are created by an admin/i)).toBeVisible();
  });

  test("Google is offered as the way in (FR-AUTH-08)", async ({ page }) => {
    await page.goto("/auth/login");
    await expect(page.getByRole("button", { name: /Sign in with Google/i })).toBeVisible();
  });

  test("a bad password is refused", async ({ page }) => {
    // The password form is a local-development affordance only
    // (NEXT_PUBLIC_ENABLE_PASSWORD_LOGIN); production is Google-only per
    // FR-AUTH-08, where this test has nothing to assert against.
    await page.goto("/auth/login");
    if ((await page.locator("#password").count()) === 0) {
      test.skip(true, "password fallback is off — Google-only, as in production");
    }
    await page.fill("#email", PEOPLE.user);
    await page.fill("#password", "wrong-password");
    await page.click('button[type="submit"]');
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page).toHaveURL(/\/auth\/login/);
  });
});

test.describe("the calendar", () => {
  test.beforeEach(async ({ page }) => signIn(page, PEOPLE.user));

  test("shows a balance per category (FR-BAL-06)", async ({ page }) => {
    for (const label of ["Work from home", "Casual leave", "Sick leave"]) {
      await expect(page.getByText(label).first()).toBeVisible();
    }
  });

  test("every Saturday and Sunday is non-bookable (FR-CAL-05)", async ({ page }) => {
    // Checked by grid POSITION, not by a CSS class: a restyle must not be able
    // to turn this assertion into a no-op that still reports green.
    const weekend = await page.evaluate(() => {
      const grid = [...document.querySelectorAll(".grid.grid-cols-7")].pop();
      const cells = [...(grid?.children ?? [])];
      let disabled = 0;
      let total = 0;
      cells.forEach((cell, index) => {
        if (index % 7 < 5) return; // Monday-first, so 5 = Sat, 6 = Sun
        const button = cell.tagName === "BUTTON" ? cell : cell.querySelector("button");
        if (!button) return;
        total += 1;
        if ((button as HTMLButtonElement).disabled) disabled += 1;
      });
      return { disabled, total };
    });

    expect(weekend.total).toBeGreaterThan(0);
    expect(weekend.disabled).toBe(weekend.total);
  });

  test("books a half day in three interactions (§4.1, NFR-02)", async ({ page }) => {
    const day = await openLastBookableDay(page);
    test.skip(!day, "no bookable day left this month");

    const dialog = page.locator('[data-slot="dialog-content"]');
    await expect(dialog).toBeVisible();

    // FR-BOOK-12 — the remaining allowance is shown BEFORE confirming, so
    // nobody has to do the arithmetic themselves (§1.1).
    await expect(dialog.getByText(/You have/)).toBeVisible();

    await dialog.getByRole("button", { name: "Casual leave" }).scrollIntoViewIfNeeded();
    await dialog.getByRole("button", { name: "Casual leave" }).click();
    await dialog.getByRole("button", { name: "Half day" }).click();
    await dialog.locator("#reason").fill("Dentist appointment");
    await dialog.getByRole("button", { name: "Confirm" }).scrollIntoViewIfNeeded();
    await dialog.getByRole("button", { name: "Confirm" }).click();

    await expect(dialog).toBeHidden();

    // Assert on the cell we actually booked, not on any PENDING anywhere:
    // Deepika has a SEEDED pending booking, so `getByText("PENDING").first()`
    // passed whether or not this booking rendered — and the cleanup below then
    // re-opened a cell the calendar had not yet refreshed.
    // Case-INSENSITIVE: the status renders as "PENDING" but only because CSS
    // uppercases it. textContent — which is what Playwright matches — is
    // "pending", so /PENDING/ silently never matches.
    const cell = page
      .locator(".grid.grid-cols-7 button")
      .filter({ hasText: new RegExp(`^${day}`) })
      .filter({ hasText: /pending/i });
    await expect(cell).toBeVisible();

    // Put the allowance back. These tests write real rows, and a booking that
    // survives the run consumes half a day of casual leave every time — after
    // enough runs the balance hits zero and this test starts failing for a
    // reason that has nothing to do with the code under test.
    //
    // The cell must be found again by number: saving reloaded the calendar, so
    // the marker openLastBookableDay left on it no longer exists.
    await openDayNumbered(page, day!);
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Clear day" }).click();
    await expect(dialog).toBeHidden();
  });

  test("refuses casual leave for today, in words the person can act on (§6.1, A-19)", async ({
    page,
  }) => {
    // Today's cell is the one the server marked as today. It is only
    // clickable on a working day: on a weekend or a declared holiday the
    // server marks it non-bookable and the cell is disabled (FR-CAL-05), so
    // there is no dialog to assert against and nothing to test. Checking
    // *before* clicking matters — this ran green on weekdays and timed out
    // every Saturday and Sunday, which reads like a broken app rather than a
    // test that assumed the day of the week.
    const today = page.locator("button.ring-primary, button.border-primary").first();
    if (await today.isDisabled()) {
      test.skip(true, "today is a weekend or holiday — nothing bookable to refuse");
    }
    await today.click();

    const dialog = page.locator('[data-slot="dialog-content"]');
    await expect(dialog).toBeVisible();

    await dialog.getByRole("button", { name: "Casual leave" }).scrollIntoViewIfNeeded();
    await dialog.getByRole("button", { name: "Casual leave" }).click();
    await dialog.locator("#reason").fill("Trying to take today off");
    const confirm = dialog.getByRole("button", { name: /Confirm|Update/ });
    await confirm.scrollIntoViewIfNeeded();
    await confirm.click();

    // The message must name the alternative, not just say no.
    await expect(dialog.getByRole("alert")).toContainText(/before the day itself/i);
    await expect(dialog.getByRole("alert")).toContainText(/sick leave/i);
  });
});

test.describe("the lead", () => {
  test.beforeEach(async ({ page }) => signIn(page, PEOPLE.lead));

  test("the roster defaults to today with no filtering (FR-LEAD-05)", async ({ page }) => {
    await page.goto("/team");
    await expect(page.getByRole("heading", { name: "Today" })).toBeVisible();
    // .first() because each person also appears in the consumption table below;
    // this assertion is about the roster existing, not about how many times a
    // name is rendered.
    await expect(page.getByText("Deepika").first()).toBeVisible();
  });

  test("the roster carries NO reason text (Q-06, NFR-05)", async ({ page }) => {
    await page.goto("/team");
    await expect(page.getByText("Deepika").first()).toBeVisible();
    // A sick-leave reason is health information about a named colleague. It
    // must not appear on a screen whose whole purpose is to be glanced at.
    await expect(page.locator("body")).not.toContainText(/Dentist|Fever|Anniversary/);
  });

  test("the approval queue DOES carry the reason (Q-06)", async ({ page }) => {
    await page.goto("/approvals");
    const cards = page.locator("body");
    await expect(cards).toContainText(/Approvals/);
    // A lead cannot reasonably approve a request whose reason they may not read.
    await expect(cards).toContainText(/Dentist appointment/);
  });

  test("rejecting is unavailable until a note is written (FR-APPR-03)", async ({ page }) => {
    await page.goto("/approvals");
    const reject = page.getByRole("button", { name: "Reject" }).first();
    await expect(reject).toBeDisabled();

    await page.locator("textarea").first().fill("Two people are already out that day.");
    await expect(reject).toBeEnabled();
  });
});

test.describe("the admin", () => {
  test("can manage people, holidays and policy (FR-ADMIN, FR-HOL)", async ({ page }) => {
    await signIn(page, PEOPLE.admin);
    await page.goto("/admin");

    await expect(page.getByText("Add someone")).toBeVisible();
    // Scoped to the table, not the page. A bare getByText("Devansh") also
    // matches the hidden <option> inside the "Approved by" select, which is
    // correctly invisible and would fail for the wrong reason.
    await expect(page.getByRole("cell", { name: "Devansh", exact: false }).first()).toBeVisible();

    await page.getByRole("tab", { name: "Holidays" }).click();
    await expect(page.getByText("Declare a holiday")).toBeVisible();
    await expect(page.getByText("Diwali").first()).toBeVisible();

    // §11 — the spec's open questions are visible in the product as settings,
    // rather than being buried in a document nobody opens.
    await page.getByRole("tab", { name: "Policy" }).click();
    await expect(page.getByText("carry_forward_policy").first()).toBeVisible();
    await expect(page.getByText("sandwich_rule").first()).toBeVisible();
  });
});

test.describe("the admin backfill (spec A-21)", () => {
  test("is labelled as the exception it is, and lists what was entered", async ({ page }) => {
    await signIn(page, PEOPLE.admin);
    await page.goto("/admin");
    await page.getByRole("tab", { name: "Backfill" }).click();

    // The warning is not decoration. Distinct labelling is the condition on
    // which this override of §6.3 was added at all, so its absence is a
    // regression worth failing a build over.
    await expect(
      page.getByText(/the one place a locked day can be changed/i)
    ).toBeVisible();
    await expect(page.getByText(/Record leave already taken/i)).toBeVisible();
    await expect(page.getByText(/Everything entered by hand/i)).toBeVisible();
  });

  test("is not reachable by a lead", async ({ page }) => {
    await signIn(page, PEOPLE.lead);
    await page.goto("/admin");
    await expect(page.getByText(/Only an admin can do that/i)).toBeVisible();
  });
});

test.describe("authorisation", () => {
  test("a plain user gets no lead or admin navigation", async ({ page }) => {
    await signIn(page, PEOPLE.otherUser);
    const header = page.locator("header");
    await expect(header.getByRole("link", { name: "Team" })).toHaveCount(0);
    await expect(header.getByRole("link", { name: "Admin" })).toHaveCount(0);
  });

  test("and is refused when visiting /admin directly (FR-ADMIN)", async ({ page }) => {
    await signIn(page, PEOPLE.otherUser);
    await page.goto("/admin");
    // Hiding the link is tidiness; this is the check that matters.
    await expect(page.getByText(/Only an admin can do that/i)).toBeVisible();
  });
});
