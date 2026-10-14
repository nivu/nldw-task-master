import fs from "node:fs";
import path from "node:path";

import { type APIRequestContext, expect, type Locator, type Page, test } from "@playwright/test";

import { PASSWORD, PEOPLE, signIn, submitSignIn } from "./helpers";

/**
 * The CRM's shell and who reaches it — spec 007 FR-CRM-UI-03 to -05, D9;
 * tasks S1c.
 *
 * Needs the stack quickstart §1 describes, with `npx supabase db reset` run so
 * seed.sql's CRM people and memberships exist: Tamil (CRM-only, a Labs
 * Member), Deepika (staff, a Labs Member) and Sriram (the local owner, Admin
 * of both workspaces).
 */

const TVA_API = /\/api\/proxy\/api\/v1\/(?!me$|crm\/)/;

test.describe("a CRM-only seller", () => {
  test("lands on /crm/labs after sign-in (FR-CRM-UI-04)", async ({ page }) => {
    await submitSignIn(page, PEOPLE.crmOnly, "**/crm/labs");
    await expect(page.getByTestId("crm-shell")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Today" })).toBeVisible();
    // No TVA link anywhere in the shell, the TVA guide at /help included.
    await expect(page.getByRole("link", { name: /Back to TVA/i })).toHaveCount(0);
    await expect(page.locator('a[href="/help"]')).toHaveCount(0);
  });

  test("a TVA page sends them to /crm before any TVA request fires", async ({ page }) => {
    await submitSignIn(page, PEOPLE.crmOnly, "**/crm/labs");
    const tvaCalls: string[] = [];
    page.on("request", (request) => {
      if (TVA_API.test(new URL(request.url()).pathname)) tvaCalls.push(request.url());
    });
    await page.goto("/calendar");
    await page.waitForURL("**/crm/labs");
    expect(tvaCalls).toEqual([]);
  });
});

test.describe("a staff member", () => {
  test("with a membership sees the CRM link, and Back to TVA inside (FR-CRM-UI-03)", async ({
    page,
  }) => {
    await signIn(page, PEOPLE.user);
    const link = page.getByRole("link", { name: "CRM" }).first();
    await expect(link).toBeVisible();
    await link.click();
    await page.waitForURL("**/crm/labs");
    await expect(page.getByRole("link", { name: /Back to TVA/i }).first()).toBeVisible();
    await expect(page.locator('a[href="/help"]').first()).toBeVisible();
  });

  test("without one sees no CRM link", async ({ page }) => {
    await signIn(page, PEOPLE.admin);
    await expect(page.getByRole("link", { name: "CRM", exact: true })).toHaveCount(0);
  });

  test("the owner administers both workspaces after db reset", async ({ page }) => {
    await signIn(page, PEOPLE.manager);
    for (const ws of ["labs", "academy"]) {
      await page.goto(`/crm/${ws}/settings`);
      await expect(page.getByRole("tab", { name: "Members" })).toBeVisible();
    }
  });
});

test.describe("an anonymous visitor", () => {
  test("is sent to sign in from anywhere under /crm (FR-CRM-UI-05)", async ({ page }) => {
    for (const route of ["/crm", "/crm/labs", "/crm/labs/settings", "/crm/account"]) {
      await page.goto(route);
      await expect(page).toHaveURL(/\/auth\/login/);
    }
  });
});

// ---------------------------------------------------------------------------
// Journey 4.2 in Nunnari Labs — spec 007 FR-CRM-TVA-04, -07 to -09; tasks S5.
//
// The deal is set up through the API (the proxy, with the person's own
// session token from the local GoTrue), then the TVA side is checked in the
// browser: the project shows in TVA Projects as tentative and deal-managed,
// and the deal is won through the handoff dialog. Run once as a TVA user
// (Deepika: no revenue) and once as a TVA manager (Sriram).
// ---------------------------------------------------------------------------

function localEnv(name: string): string {
  if (process.env[name]) return process.env[name] as string;
  const file = path.join(__dirname, "..", ".env");
  const line = fs
    .readFileSync(file, "utf8")
    .split("\n")
    .find((l) => l.startsWith(`${name}=`));
  return (line ?? "").slice(name.length + 1).trim();
}

async function tokenFor(request: APIRequestContext, email: string): Promise<string> {
  const r = await request.post(
    `${localEnv("NEXT_PUBLIC_SUPABASE_URL")}/auth/v1/token?grant_type=password`,
    { headers: { apikey: localEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY") }, data: { email, password: PASSWORD } }
  );
  expect(r.ok(), await r.text()).toBeTruthy();
  return (await r.json()).access_token;
}

async function crmApi(
  request: APIRequestContext,
  token: string,
  method: string,
  route: string,
  data?: unknown
) {
  const r = await request.fetch(`/api/proxy/api/v1/crm${route}`, {
    method,
    headers: { Authorization: `Bearer ${token}` },
    data,
  });
  expect(r.ok(), `${method} ${route}: ${await r.text()}`).toBeTruthy();
  return r.json();
}

for (const seller of [
  { email: PEOPLE.user, name: "a TVA user", manager: false },
  { email: PEOPLE.manager, name: "a TVA manager", manager: true },
]) {
  test(`journey 4.2 in Labs as ${seller.name}: Proposal makes a tentative project, the handoff wins it`, async ({
    page,
    request,
  }) => {
    const token = await tokenFor(request, seller.email);
    const tag = `E2E ${Date.now().toString(36)}`;
    const company = await crmApi(request, token, "POST", "/companies?workspace=labs", { name: `${tag} Co` });
    await crmApi(request, token, "PATCH", `/companies/${company.id}?workspace=labs`, { lead_stage: "qualified" });
    const person = await crmApi(request, token, "POST", "/people?workspace=labs", { first_name: "Priya" });
    const deal = await crmApi(request, token, "POST", "/opportunities?workspace=labs", {
      name: tag,
      company_id: company.id,
      point_of_contact_id: person.id,
      next_step: "Send the proposal",
      next_step_on: "2026-12-01",
    });
    const moved = await crmApi(request, token, "POST", `/opportunities/${deal.id}/stage?workspace=labs`, {
      stage: "proposal",
      changes: { amount: "500000", currency: "INR", expected_close_on: "2026-12-15" },
    });
    expect(moved.project.status).toBe("tentative");

    // The project is in TVA Projects as tentative, managed by the deal.
    await signIn(page, PEOPLE.manager);
    await page.goto("/projects");
    const card = page.locator("div", { hasText: tag }).filter({ hasText: "Managed by CRM deal" }).last();
    await expect(card).toBeVisible();
    await expect(card.getByText(/Tentative/).first()).toBeVisible();

    // Won through the handoff, in the browser, as the seller.
    await page.context().clearCookies();
    await signIn(page, seller.email);
    await page.goto(`/crm/labs/opportunities/${deal.id}`);
    await page.getByRole("combobox", { name: "Move stage" }).click();
    await page.getByRole("option", { name: "Closed Won" }).click();
    await expect(page.getByRole("heading", { name: `Hand off ${tag}` })).toBeVisible();
    await page.selectOption("#handoff-lead", { label: "Sriram" });
    await page.fill("#handoff-delivery-start", "2026-12-16");
    await page.fill("#handoff-delivery-end", "2027-03-31");
    if (!seller.manager) {
      await expect(page.getByText(/Only a TVA manager or admin sets revenue/)).toBeVisible();
    }
    await page.getByRole("button", { name: "Confirm handoff and mark won" }).click();
    await expect(page.getByText("Closed Won").first()).toBeVisible();
    await expect(page.getByText(`${tag} · confirmed`)).toBeVisible();
  });
}

// ---------------------------------------------------------------------------
// Views — spec 007 FR-CRM-VIEW-01, -06, -09, FR-CRM-ACC-11; tasks S6b,
// quickstart §3 S6.
//
// Records are made through the API with a tag in their names; the view's
// search narrows each list to them, so rows other runs left behind do not
// matter. Kanban moves are made all three ways a seller makes them: the
// keyboard (focus, Space, an arrow, Space) on both projects, and on
// Pipeline the mouse on the desktop project and a touch drag on the phone.
// ---------------------------------------------------------------------------

/** Each step waits for its announcement, which is also what a screen
 *  reader hears. The sensor starts listening for arrows a moment after the
 *  pick-up, so an arrow pressed in that moment is pressed again. */
async function keyboardMove(page: Page, card: Locator, key: "ArrowRight" | "ArrowLeft", to: string) {
  const live = page.locator('[role="status"][aria-live="assertive"]');
  await card.focus();
  await page.keyboard.press("Space");
  await expect(live).toContainText(/Picked up|is over/);
  await expect(async () => {
    await page.keyboard.press(key);
    await expect(live).toContainText(`is over ${to}`, { timeout: 1000 });
  }).toPass();
  await page.keyboard.press("Space");
}

async function mouseMove(page: Page, card: Locator, column: Locator) {
  const from = await card.boundingBox();
  const to = await column.boundingBox();
  if (!from || !to) throw new Error("The card or the column is not on screen.");
  await page.mouse.move(from.x + from.width / 2, from.y + 12);
  await page.mouse.down();
  await page.mouse.move(from.x + from.width / 2 + 12, from.y + 20, { steps: 6 });
  await page.mouse.move(to.x + to.width / 2, to.y + 80, { steps: 12 });
  await page.mouse.up();
}

/** Press and hold, then slide: a phone's drag (the sensor waits 200 ms so a
 *  quick swipe still scrolls the board). Playwright has no touch-move, so
 *  the touches go through the DevTools protocol. */
async function touchMove(page: Page, card: Locator, column: Locator) {
  const from = await card.boundingBox();
  const to = await column.boundingBox();
  if (!from || !to) throw new Error("The card or the column is not on screen.");
  const viewport = page.viewportSize()!;
  const cdp = await page.context().newCDPSession(page);
  const touch = (type: "touchStart" | "touchMove" | "touchEnd", x: number, y: number) =>
    cdp.send("Input.dispatchTouchEvent", {
      type,
      touchPoints: type === "touchEnd" ? [] : [{ x: Math.round(x), y: Math.round(y) }],
    });
  const start = { x: from.x + from.width / 2, y: from.y + 12 };
  const end = { x: Math.min(to.x + 40, viewport.width - 8), y: to.y + 80 };
  await touch("touchStart", start.x, start.y);
  await page.waitForTimeout(350);
  for (let i = 1; i <= 12; i++) {
    await touch("touchMove", start.x + ((end.x - start.x) * i) / 12, start.y + ((end.y - start.y) * i) / 12);
  }
  await touch("touchEnd", end.x, end.y);
}

const column = (page: Page, key: string) => page.locator(`[data-testid="kanban-column"][data-column="${key}"]`);
const card = (page: Page, id: string) => page.locator(`[data-card-id="${id}"]`);

test.describe("views (S6)", () => {
  test("a table view filters, sorts, groups and saves (FR-CRM-VIEW-02, -03, -05)", async ({ page, request }) => {
    const token = await tokenFor(request, PEOPLE.user);
    const tag = `E2E view ${Date.now().toString(36)}`;
    for (const name of ["Alpha", "Beta"]) {
      await crmApi(request, token, "POST", "/companies?workspace=labs", { name: `${tag} ${name}` });
    }

    await signIn(page, PEOPLE.user);
    await page.goto("/crm/labs/companies");
    await expect(page.getByRole("tab", { name: "All companies" })).toHaveAttribute("aria-selected", "true");

    await page.getByRole("button", { name: /^Filter/ }).click();
    await page.getByRole("button", { name: "Condition", exact: true }).click();
    await page.getByLabel("Condition 1 field").selectOption({ label: "Name" });
    await page.getByLabel("Condition 1 operator").selectOption("contains");
    await page.getByLabel("Condition 1 value").fill(tag);
    await page.getByRole("button", { name: "Apply" }).click();
    await expect(page.getByRole("button", { name: "Filter · 1" })).toBeVisible();

    await page.getByRole("button", { name: /^Sort/ }).click();
    await page.getByLabel("Sort 1 field").selectOption({ label: "Name" });
    await page.getByLabel("Sort 1 direction").selectOption("desc");
    await page.locator("#view-group-by").selectOption({ label: "Lead stage" });
    await page.keyboard.press("Escape");

    const rows = page.locator("[data-row-id]");
    await expect(rows).toHaveCount(2);
    await expect(rows.first()).toContainText(`${tag} Beta`);
    await expect(page.getByRole("button", { name: /^New\s*2/ })).toBeVisible();

    const viewName = `Mine ${Date.now().toString(36)}`;
    await page.getByRole("button", { name: "View options" }).click();
    await page.getByRole("menuitem", { name: "Save as new view…" }).click();
    await page.fill("#view-name", viewName);
    await page.getByRole("dialog").getByRole("button", { name: "Save view" }).click();
    await expect(page.getByRole("tab", { name: viewName })).toHaveAttribute("aria-selected", "true");
    await expect(page).toHaveURL(/\?view=/);

    await page.reload();
    await expect(page.getByRole("tab", { name: viewName })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("button", { name: "Filter · 1" })).toBeVisible();
    await expect(page.locator("[data-row-id]").first()).toContainText(`${tag} Beta`);
  });

  test("a kanban drag moves a card, and a refused drag rolls back (FR-CRM-VIEW-01, -06)", async ({
    page,
    request,
  }) => {
    const token = await tokenFor(request, PEOPLE.user);
    const tag = `E2E funnel ${Date.now().toString(36)}`;
    const fresh = await crmApi(request, token, "POST", "/companies?workspace=labs", { name: `${tag} New` });
    const busy = await crmApi(request, token, "POST", "/companies?workspace=labs", { name: `${tag} Busy` });
    await crmApi(request, token, "PATCH", `/companies/${busy.id}?workspace=labs`, { lead_stage: "qualified" });
    await crmApi(request, token, "POST", "/opportunities?workspace=labs", {
      name: `${tag} deal`,
      company_id: busy.id,
      next_step: "Call",
      next_step_on: "2026-12-01",
    });

    await signIn(page, PEOPLE.user);
    await page.goto("/crm/labs/companies");
    await page.getByRole("tab", { name: "Lead funnel" }).click();
    await page.getByLabel("Search companies").fill(tag);
    await expect(page.getByText("2 records", { exact: true })).toBeVisible();
    await expect(column(page, "new").locator(card(page, fresh.id))).toBeVisible();
    await expect(column(page, "deal_open").locator(card(page, busy.id))).toBeVisible();

    // New → Contacted: saved, and still there after a reload.
    await keyboardMove(page, card(page, fresh.id), "ArrowRight", "Contacted");
    await expect(column(page, "contacted").locator(card(page, fresh.id))).toBeVisible();
    await page.reload();
    await page.getByLabel("Search companies").fill(tag);
    await expect(page.getByText("2 records", { exact: true })).toBeVisible();
    await expect(column(page, "contacted").locator(card(page, fresh.id))).toBeVisible();

    // Deal open → Qualified: refused (its open deal sets its lead stage), so it goes back.
    await keyboardMove(page, card(page, busy.id), "ArrowLeft", "Qualified");
    await expect(page.getByTestId("kanban-error")).toContainText("open deals");
    await expect(column(page, "deal_open").locator(card(page, busy.id))).toBeVisible();
  });

  test("Pipeline: a stage drag that needs more opens the stage form; cancelling puts it back", async ({
    page,
    request,
  }, testInfo) => {
    const token = await tokenFor(request, PEOPLE.user);
    const tag = `E2E pipe ${Date.now().toString(36)}`;
    const company = await crmApi(request, token, "POST", "/companies?workspace=labs", { name: `${tag} Co` });
    await crmApi(request, token, "PATCH", `/companies/${company.id}?workspace=labs`, { lead_stage: "qualified" });
    const deal = await crmApi(request, token, "POST", "/opportunities?workspace=labs", {
      name: tag,
      company_id: company.id,
      next_step: "Call",
      next_step_on: "2026-12-01",
    });

    await signIn(page, PEOPLE.user);
    await page.goto("/crm/labs/pipeline");
    await page.getByLabel("Search pipeline").fill(tag);
    // The search settles before anything is measured: the board re-lays out.
    await expect(page.getByText("1 record", { exact: true })).toBeVisible();
    await expect(column(page, "discovery").locator(card(page, deal.id))).toBeVisible();

    if (testInfo.project.name === "desktop") {
      await mouseMove(page, card(page, deal.id), column(page, "qualification"));
    } else {
      await touchMove(page, card(page, deal.id), column(page, "qualification"));
    }
    await expect(page.getByRole("heading", { name: "Move to Qualification" })).toBeVisible();
    await expect(column(page, "qualification").locator(card(page, deal.id))).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
    await expect(column(page, "discovery").locator(card(page, deal.id))).toBeVisible();
  });

  test("a Viewer opens Pipeline without amounts, with the notice, and cannot filter by amount (FR-CRM-ACC-11)", async ({
    page,
  }) => {
    await signIn(page, PEOPLE.lead);
    await page.goto("/crm/labs/pipeline");
    await expect(page.getByTestId("view-notice")).toContainText("A condition on an amount field was removed");
    await expect(page.getByTestId("crm-kanban")).not.toContainText("weighted");

    await page.getByRole("button", { name: /^Filter/ }).click();
    await page.getByRole("button", { name: "Condition", exact: true }).click();
    await page.getByLabel("Condition 1 field").selectOption({ label: "Amount" });
    await page.getByLabel("Condition 1 operator").selectOption("gt");
    await page.getByLabel("Condition 1 value").fill("1");
    await page.getByRole("button", { name: "Apply" }).click();
    await expect(page.getByTestId("view-error")).toHaveText("Viewers cannot filter, sort or total by amount.");
  });

  test("the seeded views open in both workspaces (FR-CRM-VIEW-11)", async ({ page }) => {
    const seeded: [string, string[]][] = [
      ["companies", ["All companies", "Lead funnel", "Partners and channels", "My accounts"]],
      ["opportunities", ["Pipeline", "Closing this month", "Won and lost this quarter"]],
      ["tasks", ["Open tasks by owner", "My week"]],
    ];
    await signIn(page, PEOPLE.manager);
    for (const ws of ["labs", "academy"]) {
      for (const [list, names] of seeded) {
        await page.goto(`/crm/${ws}/${list}`);
        for (const name of names) {
          await page.getByRole("tab", { name }).click();
          await expect(page.getByRole("tab", { name })).toHaveAttribute("aria-selected", "true");
          await expect(page.getByText(/^[\d,]+\+? records?$/).first()).toBeVisible();
          await expect(page.getByTestId("view-error")).toHaveCount(0);
        }
      }
    }
  });
});
