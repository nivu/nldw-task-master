import fs from "node:fs";
import path from "node:path";

import { type APIRequestContext, expect, type Locator, type Page, test } from "@playwright/test";
import { NextRequest } from "next/server";

import * as proxyRoute from "../app/api/proxy/[...path]/route";
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

  test("a task opens the record it is linked to, from the Tasks table and with Enter", async ({ page, request }) => {
    const token = await tokenFor(request, PEOPLE.user);
    const tag = `E2E task ${Date.now().toString(36)}`;
    const company = await crmApi(request, token, "POST", "/companies?workspace=labs", { name: `${tag} Co` });
    const task = await crmApi(request, token, "POST", "/tasks?workspace=labs", {
      title: `${tag} call back`,
      links: [{ record_type: "company", record_id: company.id }],
    });

    await signIn(page, PEOPLE.user);
    await page.goto("/crm/labs/tasks");
    await page.getByLabel("Search tasks").fill(tag);
    const row = page.locator(`[data-row-id="${task.id}"]`);
    await expect(row).toBeVisible();
    await expect(row.getByRole("link", { name: `${tag} Co` })).toBeVisible();
    await row.getByRole("link", { name: `${tag} call back` }).click();
    await page.waitForURL(`**/crm/labs/companies/${company.id}`);

    await page.goto("/crm/labs/tasks");
    await page.getByLabel("Search tasks").fill(tag);
    await page.locator(`[data-row-id="${task.id}"]`).focus();
    await page.keyboard.press("Enter");
    await page.waitForURL(`**/crm/labs/companies/${company.id}`);
  });

  test("the seeded views open in both workspaces with their filters (FR-CRM-VIEW-11)", async ({
    page,
    request,
  }) => {
    // [tab, conditions in its filter]
    const seeded: [string, [string, number][]][] = [
      [
        "companies",
        [
          ["All companies", 0],
          ["Lead funnel", 1],
          ["Partners and channels", 1],
          ["My accounts", 1],
        ],
      ],
      [
        "opportunities",
        [
          ["Pipeline", 1],
          ["Closing this month", 2],
          ["Won and lost this quarter", 3],
        ],
      ],
      [
        "tasks",
        [
          ["Open tasks by owner", 1],
          ["My week", 1],
        ],
      ],
    ];
    await signIn(page, PEOPLE.manager);
    for (const ws of ["labs", "academy"]) {
      for (const [list, views] of seeded) {
        await page.goto(`/crm/${ws}/${list}`);
        for (const [name, conditions] of views) {
          await page.getByRole("tab", { name }).click();
          await expect(page.getByRole("tab", { name })).toHaveAttribute("aria-selected", "true");
          await expect(page.getByText(/^[\d,]+\+? records?$/).first()).toBeVisible();
          await expect(page.getByTestId("view-error")).toHaveCount(0);
          await expect(
            page.getByRole("button", { name: conditions ? `Filter · ${conditions}` : "Filter", exact: true })
          ).toBeVisible();
        }
      }
    }

    // Open tasks by owner groups by assignee; My week is a board by bucket.
    await page.goto("/crm/labs/tasks");
    await page.getByRole("tab", { name: "Open tasks by owner" }).click();
    await page.getByRole("button", { name: /^Sort/ }).click();
    await expect(page.locator("#view-group-by")).toHaveValue("assignee_id");
    await page.keyboard.press("Escape");
    await page.getByRole("tab", { name: "My week" }).click();
    await expect(column(page, "today")).toBeVisible();

    // My accounts leaves out a company someone else owns.
    const token = await tokenFor(request, PEOPLE.user);
    const tag = `E2E mine ${Date.now().toString(36)}`;
    await crmApi(request, token, "POST", "/companies?workspace=labs", { name: `${tag} Theirs` });
    await page.goto("/crm/labs/companies");
    await page.getByRole("tab", { name: "All companies" }).click();
    await page.getByLabel("Search companies").fill(tag);
    await expect(page.getByTestId("table-count")).toHaveText("1 record");
    await page.getByRole("tab", { name: "My accounts" }).click();
    await expect(page.getByTestId("table-count")).toHaveText("0 records");
  });
});

test.describe("Today, search and merge (S7)", () => {
  test("Cmd+K finds a seeded company and opens it (FR-CRM-SRCH-01, -02)", async ({ page, request }) => {
    const token = await tokenFor(request, PEOPLE.user);
    const tag = `E2E find ${Date.now().toString(36)}`;
    const company = await crmApi(request, token, "POST", "/companies?workspace=labs", { name: `${tag} Co` });

    await signIn(page, PEOPLE.user);
    await page.goto("/crm/labs");
    await expect(page.getByRole("heading", { name: "Today" })).toBeVisible();
    await page.keyboard.press("ControlOrMeta+k");
    const box = page.getByRole("combobox", { name: "Search the CRM" });
    await expect(box).toBeVisible();
    await box.fill(tag);
    await page.getByRole("option", { name: new RegExp(`${tag} Co`) }).click();
    await page.waitForURL(`**/crm/labs/companies/${company.id}`);
  });

  test("Cmd+K on a person makes a task linked to them, and offers the record's actions (FR-CRM-SRCH-02)", async ({
    page,
    request,
  }) => {
    const token = await tokenFor(request, PEOPLE.user);
    const tag = `E2E task ${Date.now().toString(36)}`;
    const person = await crmApi(request, token, "POST", "/people?workspace=labs", { first_name: `${tag} Pat` });

    await signIn(page, PEOPLE.user);
    // Nowhere to link a task: the menu does not offer one.
    await page.goto("/crm/labs");
    await expect(page.getByRole("heading", { name: "Today" })).toBeVisible();
    await page.keyboard.press("ControlOrMeta+k");
    await page.getByRole("combobox", { name: "Search the CRM" }).fill(tag);
    await expect(page.getByRole("option", { name: /^New company/ })).toBeVisible();
    await expect(page.getByRole("option", { name: /^New task/ })).toHaveCount(0);
    await page.keyboard.press("Escape");

    await page.goto(`/crm/labs/people/${person.id}`);
    await expect(page.getByRole("heading", { name: `${tag} Pat` })).toBeVisible();
    await page.keyboard.press("ControlOrMeta+k");
    await expect(page.getByRole("option", { name: "Copy link" })).toBeVisible();
    await expect(page.getByRole("option", { name: `Delete ${tag} Pat…` })).toBeVisible();
    await page.getByRole("combobox", { name: "Search the CRM" }).fill(`${tag} call`);
    await page.getByRole("option", { name: new RegExp(`^New task “${tag} call”`) }).click();
    await page.waitForURL("**/crm/labs/tasks");
    const after = await crmApi(request, token, "GET", `/people/${person.id}?workspace=labs`);
    expect(after.tasks.map((t: { title: string }) => t.title)).toContain(`${tag} call`);
  });

  test("Today shows its sections, the email ones saying sync is off (FR-CRM-TODAY-01, -02)", async ({ page }) => {
    await signIn(page, PEOPLE.user);
    await page.goto("/crm/labs");
    await expect(page.getByTestId("today-replies_owed")).toContainText("Email sync is off");
    await expect(page.getByTestId("today-silent_clients")).toContainText("Email sync is off");
    await expect(page.getByTestId("today-loose_ends")).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// The importer and the widened proxy — spec 007 FR-CRM-CHG-08, FR-CRM-MIG,
// 006 FR-STMT-02; plan §5.12; tasks S8. The size checks confirm the 4 MB
// figure chosen under Netlify's ~6 MB function body limit (Q-05).
// ---------------------------------------------------------------------------

const MB = 1024 * 1024;

/** A companies CSV of about `bytes` bytes, every row a unique Twenty id. */
function companiesCsv(bytes: number, tag: string): Buffer {
  const lines = ["id,name,description"];
  let size = lines[0].length + 1;
  for (let i = 0; ; i++) {
    const line = `${tag}-${i},${tag} company ${i},${"x".repeat(900)}`;
    if (size + line.length + 1 > bytes) break;
    lines.push(line);
    size += line.length + 1;
  }
  return Buffer.from(`${lines.join("\n")}\n`);
}

async function upload(request: APIRequestContext, token: string, body: Buffer, name = "companies.csv") {
  return request.post("/api/proxy/api/v1/crm/imports/dry-run?workspace=academy", {
    headers: { Authorization: `Bearer ${token}` },
    multipart: { file: { name, mimeType: "text/csv", buffer: body } },
  });
}

test.describe("the importer and the proxy (S8)", () => {
  test("a 3.9 MB CSV uploads through the proxy; a 4.1 MB one gets 413", async ({ request }) => {
    const token = await tokenFor(request, PEOPLE.manager);
    const tag = `e2e${Date.now().toString(36)}`;
    const ok = await upload(request, token, companiesCsv(3.9 * MB, tag));
    expect(ok.status(), await ok.text()).toBe(200);
    expect((await ok.json()).counts.companies.create).toBeGreaterThan(1000);

    const over = await upload(request, token, companiesCsv(4.1 * MB, `${tag}b`));
    expect(over.status()).toBe(413);
    expect((await over.json()).detail).toBe("The upload is larger than 4 MB.");
  });

  test("an oversize chunked upload with no Content-Length gets 413", async ({ request, baseURL }) => {
    const token = await tokenFor(request, PEOPLE.manager);
    let sent = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (sent >= 5 * MB) return controller.close();
        sent += MB;
        controller.enqueue(new Uint8Array(MB).fill(120));
      },
    });
    const r = await fetch(`${baseURL}/api/proxy/api/v1/crm/imports/dry-run?workspace=academy`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: stream,
      duplex: "half",
    } as RequestInit & { duplex: "half" });
    expect(r.status).toBe(413);
  });

  test("a disallowed request Content-Type gets 415", async ({ request }) => {
    const token = await tokenFor(request, PEOPLE.manager);
    const r = await request.post("/api/proxy/api/v1/crm/imports/dry-run?workspace=academy", {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "text/plain" },
      data: "hello",
    });
    expect(r.status()).toBe(415);
  });

  /**
   * The proxy's handler called directly, with `fetch` standing in for a
   * backend that sends what the real one never does: a redirect, a cookie,
   * an HTML page.
   */
  async function viaProxy(
    backend: Response,
    init: { method?: string; contentType?: string; body?: Uint8Array } = {}
  ): Promise<{ res: Response; sent: RequestInit | null }> {
    const real = globalThis.fetch;
    let sent: RequestInit | null = null;
    globalThis.fetch = (async (_url: unknown, req?: RequestInit) => {
      sent = req ?? {};
      return backend;
    }) as typeof fetch;
    try {
      const method = init.method ?? "GET";
      const request = new NextRequest("http://portal.test/api/proxy/api/v1/x", {
        method,
        headers: init.contentType ? { "Content-Type": init.contentType } : {},
        body: init.body as BodyInit | undefined,
      });
      const handler = method === "POST" ? proxyRoute.POST : proxyRoute.GET;
      const res = await handler(request, { params: Promise.resolve({ path: ["api", "v1", "x"] }) });
      return { res, sent };
    } finally {
      globalThis.fetch = real;
    }
  }

  test("a backend redirect and cookie never reach the browser", async () => {
    const { res, sent } = await viaProxy(
      new Response("<a href=/elsewhere>moved</a>", {
        status: 307,
        headers: { Location: "https://evil.test/", "Set-Cookie": "s=1", "Content-Type": "text/html" },
      })
    );
    expect(sent?.redirect).toBe("manual");
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBeNull();
    expect(res.headers.get("set-cookie")).toBeNull();

    const json = await viaProxy(
      Response.json({ ok: true }, { headers: { "Set-Cookie": "s=1" } })
    );
    expect(json.res.status).toBe(200);
    expect(json.res.headers.get("set-cookie")).toBeNull();
    expect(await json.res.json()).toEqual({ ok: true });
  });

  test("a download comes back as a nosniff attachment; an HTML page gets 502", async () => {
    const csv = await viaProxy(
      new Response("a,b\n1,2\n", { headers: { "Content-Type": "text/csv", "Set-Cookie": "s=1" } })
    );
    expect(csv.res.status).toBe(200);
    expect(csv.res.headers.get("content-disposition")).toBe("attachment");
    expect(csv.res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(csv.res.headers.get("set-cookie")).toBeNull();
    expect(await csv.res.text()).toBe("a,b\n1,2\n");

    const html = await viaProxy(
      new Response("<script>alert(1)</script>", { headers: { "Content-Type": "text/html" } })
    );
    expect(html.res.status).toBe(502);
    expect(html.res.headers.get("content-type")).toBe("application/problem+json");
    expect(await html.res.text()).not.toContain("<script>");
  });

  test("a multipart upload gets the backend's 64 KB of envelope room; JSON does not", async () => {
    const limit = 4 * MB + 64 * 1024;
    const post = (size: number, contentType: string) =>
      viaProxy(Response.json({}), { method: "POST", contentType, body: new Uint8Array(size) });

    const multipart = "multipart/form-data; boundary=x";
    const fits = await post(limit, multipart);
    expect(fits.res.status).toBe(200);
    expect(fits.sent).not.toBeNull();
    const over = await post(limit + 1, multipart);
    expect(over.res.status).toBe(413);
    expect(over.sent).toBeNull();

    expect((await post(4 * MB, "application/json")).res.status).toBe(200);
    expect((await post(4 * MB + 1, "application/json")).res.status).toBe(413);
  });

  test("the effort statement CSV downloads with its name and real content (006 FR-STMT-02)", async ({
    page,
    request,
  }) => {
    const token = await tokenFor(request, PEOPLE.manager);
    const projects = await (
      await request.get("/api/proxy/api/v1/admin/projects", { headers: { Authorization: `Bearer ${token}` } })
    ).json();
    const project = projects[0];
    const period = new Date().toISOString().slice(0, 7);

    await signIn(page, PEOPLE.manager);
    await page.goto(`/analytics/statement?project=${project.id}&period=${period}`);
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "Download CSV" }).click(),
    ]);
    expect(download.suggestedFilename()).toBe(`${project.name}-${period}.csv`);
    const text = fs.readFileSync(await download.path(), "utf8");
    expect(text.split("\n")[0]).toContain("effort statement");
    expect(text).not.toContain('"detail"');
  });

  test("journey 4.6: dry run, run, and a second run that changes nothing", async ({ page }) => {
    const tag = `e2e${Date.now().toString(36)}`;
    const exportJson = JSON.stringify({
      companies: [{ id: `${tag}-c`, name: `${tag} College` }],
      people: [{ id: `${tag}-p`, firstName: "Ravi", email: `ravi.${tag}@example.com`, companyId: `${tag}-c` }],
      notes: [{ id: `${tag}-n`, body: "First call went well.", targets: [{ type: "company", id: `${tag}-c` }] }],
    });
    const file = { name: "academy.json", mimeType: "application/json", buffer: Buffer.from(exportJson) };

    await signIn(page, PEOPLE.manager);
    await page.goto("/crm/academy/settings/import");
    for (const creates of ["1", "0"]) {
      await page.locator("#twenty-file").setInputFiles(file);
      await page.getByRole("button", { name: "Dry run" }).click();
      await expect(page.getByTestId("import-report")).toBeVisible();
      await page.getByRole("button", { name: "Run the import" }).click();
      const done = page.getByTestId("import-done");
      await expect(done).toBeVisible();
      await expect(done.getByRole("row", { name: /^Companies/ }).getByRole("cell").nth(2)).toHaveText(creates);
    }
  });
});

// ---------------------------------------------------------------------------
// Journey 4.9 — a campaign (spec 007 FR-CRM-CAMP-02 to -05; tasks S11a,
// quickstart §3 S11). Sanjeev's Saturday webinars, in Academy: a college
// is dragged through the board and converted to a deal, and the deal carries
// the campaign as its source. Made through the API with a tag, so rows other
// runs left behind do not matter.
// ---------------------------------------------------------------------------

test.describe("campaigns (S11a)", () => {
  test("journey 4.9: drag a member along, convert it, and the deal shows its source campaign", async ({
    page,
    request,
  }) => {
    const token = await tokenFor(request, PEOPLE.manager);
    const tag = `E2E webinar ${Date.now().toString(36)}`;
    const campaign = await crmApi(request, token, "POST", "/campaigns?workspace=academy", {
      name: `${tag} series`,
    });
    const college = await crmApi(request, token, "POST", "/companies?workspace=academy", {
      name: `${tag} College`,
    });
    await crmApi(request, token, "PATCH", `/companies/${college.id}?workspace=academy`, { lead_stage: "qualified" });
    const added = await crmApi(request, token, "POST", `/campaigns/${campaign.id}/members?workspace=academy`, {
      company_ids: [college.id],
    });
    expect(added.added).toBe(1);
    const board = await crmApi(request, token, "GET", `/campaigns/${campaign.id}/members?workspace=academy`);
    const member = board.rows[0];

    await signIn(page, PEOPLE.manager);
    await page.goto(`/crm/academy/campaigns/${campaign.id}`);
    await expect(page.getByRole("heading", { name: `${tag} series` })).toBeVisible();
    await expect(column(page, "targeted").locator(card(page, member.id))).toBeVisible();

    // Targeted → Invited, through the keyboard, and saved.
    await keyboardMove(page, card(page, member.id), "ArrowRight", "Invited");
    await expect(column(page, "invited").locator(card(page, member.id))).toBeVisible();

    // Convert it to a new deal.
    await card(page, member.id).getByRole("button", { name: "Convert" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Deal name").fill(`${tag} AI course`);
    await dialog.getByLabel("Next step", { exact: true }).fill("Send the proposal");
    await dialog.getByLabel("Next step date").fill("2026-12-01");
    await dialog.getByRole("button", { name: "Convert" }).click();
    await expect(column(page, "converted").locator(card(page, member.id))).toBeVisible();
    await expect(page.getByTestId("campaign-figures")).toContainText("100.0%");

    // The deal carries the campaign as its source.
    await expect(card(page, member.id)).toContainText(`${tag} AI course`);
    const after = await crmApi(request, token, "GET", `/campaigns/${campaign.id}/members?workspace=academy`);
    await page.goto(`/crm/academy/opportunities/${after.rows[0].converted_opportunity_id}`);
    await expect(page.getByRole("heading", { name: `${tag} AI course` })).toBeVisible();
    await expect(page.getByText("Source campaign")).toBeVisible();
    await expect(page.getByRole("link", { name: `${tag} series` })).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Dashboard and export (spec 007 FR-CRM-IO-04, -05, FR-CRM-DASH-01; tasks
// S11b, quickstart §3 S11). A company made with a tag, so the export can be
// narrowed to it whatever other runs left behind.
// ---------------------------------------------------------------------------

test.describe("dashboard and export (S11b)", () => {
  test("a view exported to CSV downloads with its name and the view's rows", async ({ page, request }) => {
    const token = await tokenFor(request, PEOPLE.manager);
    const tag = `E2E export ${Date.now().toString(36)}`;
    await crmApi(request, token, "POST", "/companies?workspace=academy", { name: `${tag} College` });

    await signIn(page, PEOPLE.manager);
    await page.goto("/crm/academy/companies");
    await page.getByRole("searchbox").fill(tag);
    await expect(page.getByRole("link", { name: `${tag} College` })).toBeVisible();
    await page.getByRole("button", { name: "Export" }).click();
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByTestId("export-view-csv").click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/^companies-\d{4}-\d{2}-\d{2}\.csv$/);
    const text = fs.readFileSync(await download.path(), "utf8").replace(/^﻿/, "");
    const lines = text.trim().split(/\r?\n/);
    expect(lines[0].startsWith("ID,Name")).toBe(true);
    expect(lines).toHaveLength(2);
    expect(lines[1]).toContain(`${tag} College`);
  });

  test("the dashboard loads its panels for an admin", async ({ page }) => {
    await signIn(page, PEOPLE.manager);
    await page.goto("/crm/labs/dashboard");
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    await expect(page.getByTestId("panel-pipeline")).toBeVisible();
    await expect(page.getByTestId("panel-bookings")).toBeVisible();
    await expect(page.getByTestId("panel-activity")).toBeVisible();
  });
});
