import { expect, test } from "@playwright/test";

import { PEOPLE, signIn, submitSignIn } from "./helpers";

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
    // No TVA link anywhere in the shell.
    await expect(page.getByRole("link", { name: /Back to TVA/i })).toHaveCount(0);
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
