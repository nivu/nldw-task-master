import fs from "node:fs";
import path from "node:path";

import { type APIRequestContext, expect, type Page, test } from "@playwright/test";

import { PASSWORD, PEOPLE, signIn } from "./helpers";

/**
 * Day-log suggestions — spec 008 Journey 4.9, FR-TASK-LOG-04 to -06; the
 * phase-3 acceptance lines of §12; tasks S3a (T101). Local only.
 *
 * Needs the stack the other specs need, after `npx supabase db reset`. The
 * admin (Vinita) makes a confirmed project, allocates Deepika to it and
 * switches phase 3 on for her; Deepika then finishes two tasks on that
 * project and one with no project. A second confirmed project, Atlas, she
 * was allocated to only before today, so she can see it but it is not one of
 * the day's choices; she finishes one task on it too. Accept, Ignore and the
 * merge are screen state only (plan §5.9): these tests prove the draft, and
 * that Save day is the only write.
 */

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

interface Entry {
  project_id: string | null;
  activity: string | null;
  hours_office: string;
  note: string | null;
}

const stamp = Date.now().toString(36);
const titles = {
  first: `Fix login ${stamp}`,
  second: `Ship report ${stamp}`,
  loose: `Book travel ${stamp}`,
  away: `Draft deck ${stamp}`,
};
let projectId = "";
let projectName = "";
let awayId = "";
let awayName = "";
let userToken = "";

async function api(request: APIRequestContext, token: string, method: string, url: string, data?: unknown) {
  const r = await request.fetch(`/api/proxy/api/v1${url}`, {
    method,
    headers: { Authorization: `Bearer ${token}` },
    data,
  });
  expect(r.ok(), await r.text()).toBeTruthy();
  return r.json();
}

async function todaysEntries(request: APIRequestContext): Promise<Entry[]> {
  return (await api(request, userToken, "GET", "/timesheet/day")).entries;
}

const suggestion = (page: Page, title: string) => page.getByTestId("suggestion").filter({ hasText: title });
const lineFor = (page: Page, id: string) => page.locator(`[id="note-new-${id}"]`);
const cardOf = (page: Page, id: string) =>
  lineFor(page, id).locator("xpath=ancestor::div[contains(@class,'p-3')][1]");

test.describe.serial("Log a day suggestions", () => {
  test.beforeAll(async ({ request }) => {
    const admin = await tokenFor(request, PEOPLE.admin);
    const users = await api(request, admin, "GET", "/admin/users");
    const deepika = users.find((u: { email: string }) => u.email === PEOPLE.user);
    expect(deepika).toBeTruthy();
    projectName = `Lumen ${stamp}`;
    const project = await api(request, admin, "POST", "/admin/projects", { name: projectName, status: "confirmed" });
    projectId = project.id;
    const today = new Date();
    const later = new Date(today.getTime() + 30 * 86_400_000);
    const before = new Date(today.getTime() - 7 * 86_400_000);
    await api(request, admin, "POST", "/admin/allocations", {
      project_id: projectId,
      user_id: deepika.id,
      starts_on: before.toISOString().slice(0, 10),
      ends_on: later.toISOString().slice(0, 10),
      percent: 50,
    });
    awayName = `Atlas ${stamp}`;
    awayId = (await api(request, admin, "POST", "/admin/projects", { name: awayName, status: "confirmed" })).id;
    await api(request, admin, "POST", "/admin/allocations", {
      project_id: awayId,
      user_id: deepika.id,
      starts_on: new Date(today.getTime() - 30 * 86_400_000).toISOString().slice(0, 10),
      ends_on: new Date(today.getTime() - 2 * 86_400_000).toISOString().slice(0, 10),
      percent: 50,
    });
    await api(request, admin, "PATCH", `/admin/users/${deepika.id}`, { tasks_level: 3 });

    userToken = await tokenFor(request, PEOPLE.user);
    const link = [{ record_type: "tva_project", record_id: projectId }];
    for (const [title, links] of [
      [titles.first, link],
      [titles.second, link],
      [titles.loose, []],
      [titles.away, [{ record_type: "tva_project", record_id: awayId }]],
    ] as const) {
      const task = await api(request, userToken, "POST", "/tasks", { title, links });
      await api(request, userToken, "POST", `/tasks/${task.id}/complete`);
    }
  });

  test("accepting two tasks on one project gives one line at zero hours", async ({ page }) => {
    await signIn(page, PEOPLE.user);
    await page.goto("/timesheet");
    await expect(suggestion(page, titles.first)).toContainText(projectName);
    await suggestion(page, titles.first).getByRole("button", { name: "Add" }).click();
    await suggestion(page, titles.second).getByRole("button", { name: "Add" }).click();

    await expect(lineFor(page, projectId)).toHaveCount(1);
    await expect(lineFor(page, projectId)).toHaveValue(`${titles.first}; ${titles.second}`);
    await expect(suggestion(page, titles.first)).toContainText("Added");
    await expect(suggestion(page, titles.second)).toContainText("Added");
    const card = cardOf(page, projectId);
    await expect(card.getByRole("spinbutton")).toHaveCount(2);
    for (const hours of await card.getByRole("spinbutton").all()) await expect(hours).toHaveValue("0");
  });

  test("nothing is written without Save", async ({ page, request }) => {
    await signIn(page, PEOPLE.user);
    await page.goto("/timesheet");
    await suggestion(page, titles.first).getByRole("button", { name: "Add" }).click();
    await suggestion(page, titles.loose).getByRole("button", { name: "Ignore" }).click();
    await expect(suggestion(page, titles.loose)).toHaveCount(0);

    expect((await todaysEntries(request)).filter((e) => e.project_id === projectId)).toEqual([]);
    await page.reload();
    await expect(lineFor(page, projectId)).toHaveCount(0);
    await expect(suggestion(page, titles.first).getByRole("button", { name: "Add" })).toBeVisible();
    await expect(suggestion(page, titles.loose)).toBeVisible();
  });

  test("removing the line, or the title from its note, offers the task again", async ({ page }) => {
    await signIn(page, PEOPLE.user);
    await page.goto("/timesheet");
    await suggestion(page, titles.first).getByRole("button", { name: "Add" }).click();
    await expect(suggestion(page, titles.first)).toContainText("Added");
    await cardOf(page, projectId).getByRole("button", { name: "Remove line" }).click();
    await expect(lineFor(page, projectId)).toHaveCount(0);
    await expect(suggestion(page, titles.first)).not.toContainText("Added");
    await suggestion(page, titles.first).getByRole("button", { name: "Add" }).click();
    await expect(lineFor(page, projectId)).toHaveValue(titles.first);

    await suggestion(page, titles.second).getByRole("button", { name: "Add" }).click();
    await expect(suggestion(page, titles.second)).toContainText("Added");
    await lineFor(page, projectId).fill(titles.first);
    await expect(suggestion(page, titles.first)).toContainText("Added");
    await expect(suggestion(page, titles.second).getByRole("button", { name: "Add" })).toBeVisible();
  });

  test("ignoring a task while choosing where it goes closes the choice", async ({ page }) => {
    await signIn(page, PEOPLE.user);
    await page.goto("/timesheet");
    await suggestion(page, titles.loose).getByRole("button", { name: "Add" }).click();
    await expect(page.getByTestId("suggestion-target")).toBeVisible();
    await suggestion(page, titles.loose).getByRole("button", { name: "Ignore" }).click();
    await expect(suggestion(page, titles.loose)).toHaveCount(0);
    await expect(page.getByTestId("suggestion-target")).toHaveCount(0);
  });

  test("a task on a project not allocated that day adds the project to the choices", async ({ page, request }) => {
    await signIn(page, PEOPLE.user);
    await page.goto("/timesheet");
    await expect(suggestion(page, titles.away)).toContainText(awayName);
    await suggestion(page, titles.away).getByRole("button", { name: "Add" }).click();

    await expect(lineFor(page, awayId)).toHaveValue(titles.away);
    const card = cardOf(page, awayId);
    await expect(card).toContainText(awayName);
    await expect(card).toContainText("not allocated");
    for (const hours of await card.getByRole("spinbutton").all()) await expect(hours).toHaveValue("0");
    await card.getByRole("spinbutton").first().fill("1");
    await page.getByRole("button", { name: "Save day" }).click();
    await expect(page.getByText(/^Saved —/)).toBeVisible();

    const away = (await todaysEntries(request)).filter((e) => e.project_id === awayId);
    expect(away).toHaveLength(1);
    expect(away[0].hours_office).toBe("1.00");
    expect(away[0].note).toBe(titles.away);
  });

  test("saving with hours writes one normal entry through Save day", async ({ page, request }) => {
    await signIn(page, PEOPLE.user);
    await page.goto("/timesheet");
    await suggestion(page, titles.first).getByRole("button", { name: "Add" }).click();
    await suggestion(page, titles.second).getByRole("button", { name: "Add" }).click();
    const card = cardOf(page, projectId);
    await card.getByRole("spinbutton").first().fill("3.5");
    await page.getByRole("button", { name: "Save day" }).click();
    await expect(page.getByText(/^Saved —/)).toBeVisible();

    const mine = (await todaysEntries(request)).filter((e) => e.project_id === projectId);
    expect(mine).toHaveLength(1);
    expect(mine[0].hours_office).toBe("3.50");
    expect(mine[0].note).toBe(`${titles.first}; ${titles.second}`);
    await page.reload();
    await expect(suggestion(page, titles.first)).toContainText("Added");
    await expect(suggestion(page, titles.second)).toContainText("Added");
  });

  test("a task with no project asks where it goes, and a zero-hour line is not saved", async ({ page, request }) => {
    await signIn(page, PEOPLE.user);
    await page.goto("/timesheet");
    await suggestion(page, titles.loose).getByRole("button", { name: "Add" }).click();
    const target = page.getByTestId("suggestion-target");
    await expect(target).toContainText(`“${titles.loose}” has no project.`);
    await target.getByRole("button", { name: "Admin" }).click();
    await expect(page.locator('[id="note-new-activity:admin"]')).toHaveValue(titles.loose);
    await page.getByRole("button", { name: "Save day" }).click();
    await expect(page.getByText(/^Saved —/)).toBeVisible();

    const entries = await todaysEntries(request);
    expect(entries.filter((e) => (e.note ?? "").includes(titles.loose))).toEqual([]);
    expect(entries.filter((e) => e.project_id === projectId)).toHaveLength(1);
  });

  test("Log a day warns that a note is visible to the lead, managers and admins", async ({ page }) => {
    await signIn(page, PEOPLE.user);
    await page.goto("/timesheet");
    await expect(page.getByTestId("note-visibility")).toHaveText(
      "Your notes are visible to your lead, managers and admins."
    );
  });
});
