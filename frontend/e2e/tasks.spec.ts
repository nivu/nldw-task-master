import fs from "node:fs";
import path from "node:path";

import { type APIRequestContext, expect, type Page, test } from "@playwright/test";

import { PASSWORD, PEOPLE, signIn, submitSignIn } from "./helpers";

/**
 * My Tasks and quick add — spec 008 Journeys 4.2 and 4.5, FR-TASK-ADD-01 to
 * -04, -06, FR-TASK-LIST-01, -08, NFR-TASK-06; tasks S1c (T064). Local only.
 *
 * Needs the stack the other specs need, after `npx supabase db reset`.
 * Phase 1 is off for everyone after a reset (FR-TASK-ROLL-01), so the
 * admin (Vinita) switches it on for the people used here, through the same
 * route the Admin People page uses.
 *
 * The clock: the preview is computed by the backend, whose clock a browser
 * test cannot move. Journey 4.2 runs at the spec's fixed clock (Tuesday
 * 20 October 2026, IST) by fixing the browser's clock and answering the
 * preview request with what the backend answers at that clock (the parser
 * itself is proven at that clock in backend/tests/test_task_quick_add.py);
 * the save goes to the real backend.
 */

const DEVANSH_ID = "a0000000-0000-4000-8000-000000000002";

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

/** Phase 1 (or `level`) on for these people (FR-TASK-ROLL-02), as the admin. */
async function switchOn(request: APIRequestContext, emails: string[], level = 1) {
  const token = await tokenFor(request, PEOPLE.admin);
  const auth = { Authorization: `Bearer ${token}` };
  const users = await (await request.get("/api/proxy/api/v1/admin/users", { headers: auth })).json();
  for (const email of emails) {
    const user = users.find((u: { email: string }) => u.email === email);
    expect(user, email).toBeTruthy();
    const r = await request.patch(`/api/proxy/api/v1/admin/users/${user.id}`, {
      headers: auth,
      data: { tasks_level: level },
    });
    expect(r.ok(), await r.text()).toBeTruthy();
  }
}

/** The focused element shows it: an outline or a ring (box-shadow). */
async function focusIsVisible(page: Page) {
  const style = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el) return null;
    const css = getComputedStyle(el);
    return { outline: css.outlineStyle !== "none" && css.outlineWidth !== "0px", ring: css.boxShadow !== "none" };
  });
  expect(style, "nothing has the focus").not.toBeNull();
  expect(style!.outline || style!.ring, "the focused element shows no focus").toBe(true);
}

const quickAdd = (page: Page) => page.locator('[data-testid="quick-add"]:visible input[aria-label^="Quick add"]');

test.beforeAll(async ({ request }) => {
  await switchOn(request, [PEOPLE.user, PEOPLE.lead, PEOPLE.crmOnly]);
});

test("journey 4.2: one line, previewed, saved with Enter, in the assignee's My Tasks", async ({ page }) => {
  const title = `Call Deepak re RDX pilot ${Date.now().toString(36)}`;
  await page.clock.setFixedTime(new Date("2026-10-20T12:00:00+05:30"));
  await page.route("**/api/proxy/api/v1/tasks/quick-add/preview", async (route) => {
    const text: string = route.request().postDataJSON().text;
    const done = text.includes("@Devansh") && text.includes("#sales") && text.includes(" fri");
    await route.fulfill({
      json: {
        draft: {
          title: done ? title : text,
          due_on: done ? "2026-10-23" : null,
          due_weekday: done ? "Friday" : null,
          bucket: done ? "this_week" : "later",
          tags: done ? ["sales"] : [],
          assignee: done ? { id: DEVANSH_ID, name: "Devansh" } : { id: "me", name: "Deepika" },
          links: [],
        },
        visible_to: done ? "Visible to: you and Devansh" : "Visible to: only you",
        questions: [],
      },
    });
  });

  await signIn(page, PEOPLE.user);
  await page.goto("/tasks");
  await quickAdd(page).fill(`${title} fri #sales @Devansh`);
  const preview = page.locator('[data-testid="quick-add-preview"]:visible');
  await expect(preview).toContainText(title);
  await expect(preview).toContainText("Friday 23 Oct");
  await expect(preview).toContainText("This week");
  await expect(preview).toContainText("#sales");
  await expect(preview).toContainText("Devansh");
  await expect(preview).toContainText("Visible to: you and Devansh");
  await quickAdd(page).press("Enter");
  await expect(quickAdd(page)).toHaveValue("");

  // Devansh sees it on his next load of My Tasks (FR-TASK-ADD-06).
  await page.unrouteAll();
  await page.context().clearCookies();
  await signIn(page, PEOPLE.lead);
  await page.goto("/tasks");
  await expect(page.getByRole("button", { name: title })).toBeVisible();
});

test("a question keeps Save disabled until it is answered", async ({ page }) => {
  await signIn(page, PEOPLE.user);
  await page.goto("/tasks");
  await quickAdd(page).fill(`Pay the vendor 5/11 ${Date.now().toString(36)}`);
  const preview = page.locator('[data-testid="quick-add-preview"]:visible');
  await expect(preview).toContainText("5 Nov or 11 May?");
  await expect(preview.getByRole("button", { name: "Save" })).toBeDisabled();
  await preview.getByRole("button", { name: /5 Nov/ }).click();
  await expect(preview.getByRole("button", { name: "Save" })).toBeEnabled();
});

test("journey 4.5: a CRM-only seller opens My Tasks from the CRM and adds a private reminder", async ({
  page,
}) => {
  const title = `Renew my visa ${Date.now().toString(36)}`;
  await submitSignIn(page, PEOPLE.crmOnly, "**/crm/labs");
  if ((page.viewportSize()?.width ?? 1280) < 768) {
    await page.getByRole("button", { name: "More", exact: true }).click();
  }
  await page.getByRole("link", { name: "My Tasks" }).first().click();
  await page.waitForURL("**/crm/labs/my-tasks");
  await expect(page.getByRole("heading", { name: "My Tasks" })).toBeVisible();
  await quickAdd(page).fill(title);
  const preview = page.locator('[data-testid="quick-add-preview"]:visible');
  await expect(preview).toContainText("Visible to: only you");
  await preview.getByRole("button", { name: "Save" }).click();
  // On the open page at once, without a reload (FR-TASK-ADD-06).
  await expect(page.getByRole("button", { name: title })).toBeVisible();
});

test("keyboard only: q, type, Enter, each control with a visible focus ring", async ({ page }) => {
  const title = `Read the contract ${Date.now().toString(36)}`;
  await signIn(page, PEOPLE.user);
  await page.goto("/tasks");
  await page.locator("body").focus();
  await page.keyboard.press("q");
  await expect(quickAdd(page)).toBeFocused();
  await focusIsVisible(page);
  await page.keyboard.type(title);
  await expect(page.locator('[data-testid="quick-add-preview"]:visible')).toContainText(title);
  await page.keyboard.press("Tab");
  await focusIsVisible(page);
  await quickAdd(page).focus();
  await page.keyboard.press("Enter");
  const row = page.getByRole("button", { name: title });
  await expect(row).toBeVisible();
  await row.focus();
  await focusIsVisible(page);
  await page.getByRole("combobox", { name: `Section for ${title}` }).focus();
  await focusIsVisible(page);
});

test("overdue and priority are said in words, not colour alone", async ({ page, request }) => {
  const title = `Send the GST filing ${Date.now().toString(36)}`;
  const token = await tokenFor(request, PEOPLE.user);
  const r = await request.post("/api/proxy/api/v1/tasks", {
    headers: { Authorization: `Bearer ${token}` },
    data: { title, due_on: "2020-01-01", priority: "urgent" },
  });
  expect(r.ok(), await r.text()).toBeTruthy();
  await signIn(page, PEOPLE.user);
  await page.goto("/tasks");
  const row = page.getByTestId("section-today").getByTestId("task-row").filter({ hasText: title });
  await expect(row).toContainText(/\d+ days overdue/);
  await expect(row).toContainText("Urgent priority");
});

test("a collapsed sidebar keeps quick add: q expands it and focuses the box", async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 1280) < 768, "the sidebar is a wide-screen control");
  await page.addInitScript(() => {
    window.localStorage.setItem("portal.sidebar.collapsed", "1");
    window.localStorage.setItem("crm.sidebar.collapsed", "1");
  });

  await signIn(page, PEOPLE.user);
  await page.goto("/tasks");
  await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();
  await page.locator("body").focus();
  await page.keyboard.press("q");
  await expect(quickAdd(page)).toBeFocused();

  await page.goto("/crm/labs/my-tasks");
  await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();
  await page.getByRole("button", { name: "Quick add a task (shortcut q)" }).click();
  await expect(quickAdd(page)).toBeFocused();
});

test("a filtered My Tasks page shows a new task only when the filter matches it", async ({ page }) => {
  const stamp = Date.now().toString(36);
  const tag = `billing${stamp}`;
  await signIn(page, PEOPLE.user);
  await page.goto("/tasks");
  await page.getByRole("textbox", { name: "Tag" }).fill(`#${tag}`);
  // The filter settles before a task is added.
  await expect(page.getByTestId("task-row")).toHaveCount(0);

  const other = `Book flights ${stamp}`;
  await quickAdd(page).fill(`${other} #travel`);
  await expect(page.locator('[data-testid="quick-add-preview"]:visible')).toContainText("#travel");
  await quickAdd(page).press("Enter");
  await expect(quickAdd(page)).toHaveValue("");

  const matching = `Send the invoice ${stamp}`;
  await quickAdd(page).fill(`${matching} #${tag}`);
  await expect(page.locator('[data-testid="quick-add-preview"]:visible')).toContainText(`#${tag}`);
  await quickAdd(page).press("Enter");
  await expect(page.getByRole("button", { name: matching })).toBeVisible();
  await expect(page.getByRole("button", { name: other })).toHaveCount(0);
});

test("below phase 1 the My Tasks pages show nothing of tasks", async ({ page }) => {
  // Sriram is the owner, an admin of both workspaces, and left at level 0.
  await submitSignIn(page, PEOPLE.manager, "**/home");
  await page.goto("/tasks");
  await page.waitForURL("**/home");
  await page.goto("/crm/labs/my-tasks");
  await page.waitForURL(/\/crm\/labs$/);
  await expect(page.getByRole("heading", { name: "My Tasks" })).toHaveCount(0);
  await expect(page.locator('[data-testid="quick-add"]')).toHaveCount(0);
});

test("the Brief shows a workspace's To review as its own card, opening the Review Queue", async ({
  page,
  request,
}) => {
  // 009 FR-CRMAI-REV-09: since 009 the brief's CRM sections carry To review
  // after the five of 007. Answered here so the queue need not be filled.
  await switchOn(request, [PEOPLE.lead], 2);
  await page.route("**/api/proxy/api/v1/tasks/brief", async (route) => {
    const five = ["replies_owed", "next_steps", "tasks", "silent_clients", "loose_ends"].map((key) => ({
      key,
      items: [],
    }));
    await route.fulfill({
      json: {
        as_of: "2026-10-20",
        today: [],
        overdue: [],
        nudge: [],
        workspaces: [
          {
            workspace: { key: "labs", name: "Nunnari Labs" },
            sections: [
              ...five,
              {
                key: "to_review",
                count: 4,
                items: [
                  {
                    record_type: "review_item",
                    record_id: "c0000000-0000-4000-8000-000000000001",
                    title: "Change next step",
                    detail: "Acme pilot",
                    created_at: "2026-10-19T05:00:00+00:00",
                  },
                ],
              },
            ],
          },
        ],
      },
    });
  });
  await signIn(page, PEOPLE.lead);
  await page.goto("/tasks?view=brief");
  const card = page.getByTestId("today-to_review");
  await expect(card).toBeVisible();
  await expect(card).toContainText("To review");
  await expect(card).toContainText("4");
  await expect(card.getByRole("link", { name: /Change next step/ })).toHaveAttribute("href", "/crm/labs/review");
  await expect(card.getByRole("link", { name: "All 4 in Review" })).toHaveAttribute("href", "/crm/labs/review");
  // No card without a title, and no link to an object page that does not exist.
  await expect(page.locator('a[href*="undefined"]')).toHaveCount(0);
});
