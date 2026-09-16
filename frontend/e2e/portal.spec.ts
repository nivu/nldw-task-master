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
    for (const route of ["/", "/calendar", "/team", "/admin", "/approvals", "/account", "/projects", "/timesheet"]) {
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
    // The form is client-rendered: wait for it before deciding whether the
    // password fallback exists, or a slow first paint reads as "off".
    await page.waitForSelector("#email");
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

test.describe("the management layer (spec 003)", () => {
  test("a manager runs projects and sees money", async ({ page }) => {
    await signIn(page, PEOPLE.manager);
    // Header nav on desktop; on a phone the link lives under the More menu.
    const more = page.getByRole("button", { name: "More" });
    if (await more.isVisible()) await more.click();
    await expect(page.getByRole("link", { name: "Projects" }).first()).toBeVisible();
    // FR-ROLE-03 — but not the admin panel.
    await expect(page.getByRole("link", { name: "Admin" })).toHaveCount(0);

    await page.goto("/projects");
    await expect(page.getByText("Add a project")).toBeVisible();
    await expect(page.getByLabel(/Revenue/)).toBeVisible();

    await page.goto("/analytics");
    await expect(page.getByRole("tab", { name: "Money" })).toBeVisible();
    await page.getByRole("tab", { name: "Money" }).click();
    await expect(page.getByText(/Per person, across projects/)).toBeVisible();
  });

  test("a lead sees effort but never money (FR-FIN-07)", async ({ page }) => {
    await signIn(page, PEOPLE.lead);
    await page.goto("/analytics");
    await expect(page.getByRole("tab", { name: "Projects" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Money" })).toHaveCount(0);
    await expect(page.getByRole("tab", { name: "Resources" })).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText(/COGS|margin|cost rate/i);

    // Hiding the tab is tidiness; the refusal is the check that matters.
    await page.goto("/projects");
    await expect(page.getByText(/Only a manager or admin can do that/i)).toBeVisible();
  });

  test("a plain user gets no Projects navigation and is refused directly", async ({ page }) => {
    await signIn(page, PEOPLE.otherUser);
    await expect(page.locator("header").getByRole("link", { name: "Projects" })).toHaveCount(0);
    await page.goto("/projects");
    await expect(page.getByText(/Only a manager or admin can do that/i)).toBeVisible();
  });

  test("the timesheet offers activities and asks what was done (FR-TIME-11/12)", async ({ page }) => {
    await signIn(page, PEOPLE.user);
    await page.goto("/timesheet");
    await expect(page.getByText("Not for a project")).toBeVisible();
    await page.getByRole("button", { name: /Learning/ }).click();
    await expect(page.getByLabel("What did you do?").first()).toBeVisible();
    await expect(page.getByText("activity").first()).toBeVisible();
  });

  test("the admin records CTC as dated periods, never salary (spec 005)", async ({ page }) => {
    await signIn(page, PEOPLE.admin);
    await page.goto("/admin");
    await expect(page.getByText(/CTC \(/).first()).toBeVisible();
    await expect(page.locator("body")).not.toContainText(/salary|rate of pay/i);
    await expect(page.getByLabel("Role for Sriram")).toHaveValue("manager");
    await page.getByLabel("CTC for Tarun").click();
    await expect(page.getByText(/Annual CTC/)).toBeVisible();
    await expect(page.getByText(/until further notice/i).first()).toBeVisible();
  });

  test("a manager sees the monthly profit table and the timeline (spec 005)", async ({ page }) => {
    await signIn(page, PEOPLE.manager);
    await page.goto("/analytics");
    await page.getByRole("tab", { name: "Money" }).click();
    await expect(page.getByText("By month")).toBeVisible();
    await expect(page.getByRole("columnheader", { name: /planned/ }).first()).toBeVisible();
    await page.getByRole("tab", { name: "Resources" }).click();
    await expect(page.getByText("Who is on what")).toBeVisible();
    await page.getByRole("button", { name: "Weeks" }).click();
    await expect(page.getByRole("button", { name: "Months" })).toBeVisible();
  });
});

test.describe("the help guides", () => {
  test("are readable without signing in and describe every role", async ({ page }) => {
    await page.goto("/help");
    await expect(page).toHaveURL(/\/help$/);
    await expect(page.getByRole("heading", { name: /How to use the portal/ })).toBeVisible();

    await page.getByRole("link", { name: "For managers" }).first().click();
    await expect(page).toHaveURL(/\/help\/for-managers/);
    await expect(page.getByRole("heading", { name: "For managers", level: 1 })).toBeVisible();
    // Public, so it must describe screens and never data: no seeded name here.
    await expect(page.locator("body")).not.toContainText(/Deepika|Devansh|Sriram|Vinita|Tarun/);
  });

  test("are reachable from the sign-in page", async ({ page }) => {
    await page.goto("/auth/login");
    await page.getByRole("link", { name: /How to use the portal/ }).click();
    await expect(page).toHaveURL(/\/help/);
  });
});

test.describe("connecting Claude (spec 004)", () => {
  test("a token is issued from the Account page, shown once, and can be revoked", async ({ page }) => {
    await signIn(page, PEOPLE.user);
    await page.goto("/account");
    await expect(page.getByText("Connect Claude")).toBeVisible();

    // Unique per run: a failed earlier run leaves its token behind, and the
    // assertions below must not be confused by it.
    const tokenName = `e2e token ${Date.now()}`;
    await page.fill("#token-name", tokenName);
    await page.getByRole("button", { name: "Create token" }).click();
    // FR-TOK-02 — the plaintext appears exactly once, with the warning.
    await expect(page.getByText(/will not be shown again/i)).toBeVisible();
    await expect(page.locator("code", { hasText: /^nunp_/ }).first()).toBeVisible();
    await expect(page.getByText(/claude mcp add/)).toBeVisible();

    await page.getByRole("button", { name: "I have saved it" }).click();
    // The plaintext is gone; only the short display prefix remains in the list.
    await expect(page.getByText(/will not be shown again/i)).toHaveCount(0);
    await expect(page.getByText(/claude mcp add/)).toHaveCount(0);

    // Revoke so the seeded account does not accumulate live tokens across runs.
    const row = page.getByTestId("token-row").filter({ hasText: tokenName });
    await expect(row).toHaveCount(1);
    await row.getByRole("button", { name: "Revoke" }).click();
    await expect(row).toHaveCount(0);
    await expect(page.getByText(/revoked or expired token/)).toBeVisible();
  });
});

test.describe("org operations (spec 006)", () => {
  test("a person can claim comp-off, see their calendar feed and write up their quarter", async ({ page }) => {
    await signIn(page, PEOPLE.user);
    await expect(page.getByText(/Worked a weekend or a holiday/)).toBeVisible();
    await page.goto("/account");
    await expect(page.getByText("Leave in your calendar")).toBeVisible();
    await expect(page.locator("code", { hasText: /\/api\/v1\/feed\// })).toBeVisible();
    await page.goto("/review");
    await expect(page.getByText("In your words")).toBeVisible();
    // FR-REV-03 — nothing on this page rates anybody.
    await expect(page.locator("body")).not.toContainText(/\brating\b|\/ *5|\bgrade\b/i);
  });

  test("a lead signs off weeks and sees comp-off claims and reviews", async ({ page }) => {
    await signIn(page, PEOPLE.lead);
    await page.goto("/team");
    await page.getByRole("tab", { name: "Weeks" }).click();
    await expect(page.getByText("Weekly sign-off")).toBeVisible();
    await page.getByRole("tab", { name: "Comp-off" }).click();
    await expect(page.getByText("Comp-off claims")).toBeVisible();
    await page.getByRole("tab", { name: "Reviews" }).click();
    await expect(page.getByText(/Quarter in review/)).toBeVisible();
  });

  test("a manager sees utilisation, bench, hiring and project health", async ({ page }) => {
    await signIn(page, PEOPLE.manager);
    await page.goto("/analytics");
    await page.getByRole("tab", { name: "Utilisation" }).click();
    await expect(page.getByText(/Target \d+%/)).toBeVisible();
    await page.getByRole("tab", { name: "Bench" }).click();
    await expect(page.getByText("Hiring signal")).toBeVisible();
  });

  test("a lead is refused bench and hiring (manager tier)", async ({ page }) => {
    await signIn(page, PEOPLE.lead);
    await page.goto("/analytics");
    await expect(page.getByRole("tab", { name: "Utilisation" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Bench" })).toHaveCount(0);
  });

  test("the admin has locations, checklists and notification controls", async ({ page }) => {
    await signIn(page, PEOPLE.admin);
    await page.goto("/admin");
    await page.getByRole("tab", { name: "Holidays" }).click();
    await expect(page.getByText("Locations", { exact: true }).first()).toBeVisible();
    await expect(page.getByLabel("Applies to")).toBeVisible();
    await page.getByRole("tab", { name: "Checklists" }).click();
    await expect(page.getByText("Start a checklist")).toBeVisible();
    await page.getByRole("tab", { name: "Notifications" }).click();
    await expect(page.getByRole("button", { name: "Send me a test" })).toBeVisible();
  });

  test("the OAuth consent page needs a request and a signed-in person", async ({ page }) => {
    await page.goto("/auth/connect?txn=abc");
    // Not signed in: sent to sign in, and brought back afterwards.
    await expect(page).toHaveURL(/\/auth\/login\?next=/);
  });
});

test.describe("year frames (spec 006 FR-YEAR)", () => {
  test("the financial year runs April to March and is remembered across views", async ({ page }) => {
    await signIn(page, PEOPLE.manager);
    await page.goto("/analytics");
    await page.getByRole("tab", { name: "Utilisation" }).click();
    await page.getByRole("button", { name: "Financial year" }).first().click();
    await expect(page.getByText(/Utilisation · FY \d{4}–\d{2}/)).toBeVisible();
    await expect(page.getByRole("columnheader", { name: /^Apr/ }).first()).toBeVisible();
    // The choice carries to another month-based view on the same device.
    await page.goto("/account");
    await expect(page.getByText(/Your FY \d{4}–\d{2}/)).toBeVisible();
    await page.getByRole("button", { name: "Calendar year" }).click();
    await expect(page.getByText(/Your \d{4}$/)).toBeVisible();
  });
});

test.describe("home and navigation", () => {
  test("Home answers what needs me today, per role", async ({ page }) => {
    await signIn(page, PEOPLE.user);
    await page.goto("/home");
    await expect(page.getByText(/Good (morning|afternoon|evening)/)).toBeVisible();
    await expect(page.getByText("This week")).toBeVisible();
    await expect(page.getByText("Your balances")).toBeVisible();
    await expect(page.getByText("Your team")).toHaveCount(0);

    await signIn(page, PEOPLE.lead);
    await page.goto("/home");
    await expect(page.getByText("Your team")).toBeVisible();
    await expect(page.getByText(/waiting for you|Nothing waiting/).first()).toBeVisible();

    await signIn(page, PEOPLE.admin);
    await page.goto("/home");
    await expect(page.getByText("Health")).toBeVisible();
    await expect(page.getByText(/Slack is (not )?connected/)).toBeVisible();
  });

  test("the root lands on Home", async ({ page }) => {
    await signIn(page, PEOPLE.user);
    await page.goto("/");
    await expect(page).toHaveURL(/\/home$/);
  });

  test("the timesheet shows the week around the day", async ({ page }) => {
    await signIn(page, PEOPLE.user);
    await page.goto("/timesheet");
    await expect(page.getByRole("button", { name: /^(Mo|Tu|We|Th|Fr|Sa|Su) \d\d/ }).first()).toBeVisible();
  });

  test("the team page is tabbed", async ({ page }) => {
    await signIn(page, PEOPLE.lead);
    await page.goto("/team");
    for (const tab of ["Today", "Weeks", "Comp-off", "Reviews"]) {
      await expect(page.getByRole("tab", { name: tab })).toBeVisible();
    }
    await page.getByRole("tab", { name: "Weeks" }).click();
    await expect(page.getByText("Weekly sign-off")).toBeVisible();
  });
});
