import fs from "node:fs";
import path from "node:path";

import { type APIRequestContext, expect, test } from "@playwright/test";

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
