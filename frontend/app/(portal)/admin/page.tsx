"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import Link from "next/link";

import { BackfillPanel } from "@/components/portal/backfill-panel";
import {
  createUser,
  declareHoliday,
  deleteHoliday,
  errorMessage,
  listAllowances,
  listBackfills,
  listHolidays,
  listSettings,
  listUsers,
  setAllowance,
  updateUser,
} from "@/lib/api/portal";
import { useAsync } from "@/lib/use-async";
import type {
  Allowance,
  AppSetting,
  BackfillEntry,
  Category,
  Holiday,
  PortalUser,
  Role,
} from "@/lib/api/types";
import { CATEGORY_LABEL } from "@/lib/api/types";

const CATEGORIES: Category[] = ["wfh", "casual", "sick"];
// Spec 003 FR-ROLE-01. Only an admin assigns these (FR-ROLE-06).
const ROLES: Role[] = ["user", "lead", "manager", "admin"];
const ROLE_LABEL: Record<Role, string> = {
  user: "User",
  lead: "Lead",
  manager: "Manager",
  admin: "Admin",
};
const currentPeriod = () => new Date().toISOString().slice(0, 7);

/**
 * The admin panel — FR-ADMIN, FR-HOL, FR-AUTH-03/06.
 *
 * G-3: allowances are administered by HR, not by engineers editing code. Every
 * number this page writes goes to the `allowances` table, and nothing about
 * leave entitlement is a constant anywhere in this codebase (FR-BAL-02).
 */
export default function AdminPage() {
  const [notice, setNotice] = useState<string | null>(null);

  const { data, error, setError, reload } = useAsync<{
    users: PortalUser[];
    allowances: Allowance[];
    holidays: Holiday[];
    settings: AppSetting[];
    backfills: BackfillEntry[];
  }>(async () => {
    const [users, allowances, holidays, settings, backfills] = await Promise.all([
      listUsers(),
      listAllowances(),
      listHolidays(),
      listSettings(),
      listBackfills(),
    ]);
    return { users, allowances, holidays, settings, backfills };
  }, []);

  const users = data?.users ?? [];
  const allowances = data?.allowances ?? [];
  const holidays = data?.holidays ?? [];
  const settings = data?.settings ?? [];
  const backfills = data?.backfills ?? [];
  const currency = String(
    settings.find((s) => s.key === "currency_code")?.value ?? "INR"
  ).replace(/"/g, "");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="font-heading text-lg font-semibold">Admin</h1>
        {/* Spec 003 — projects have their own page, shared with managers. */}
        <Link href="/projects" className="text-sm text-muted-foreground hover:text-foreground">
          Projects and allocations →
        </Link>
      </div>

      {error && (
        <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}
      {notice && <div className="rounded-md bg-muted p-3 text-sm">{notice}</div>}

      <Tabs defaultValue="people">
        {/* Scrolls sideways on a phone rather than overlapping (NFR-01). */}
        <TabsList className="max-w-full overflow-x-auto">
          <TabsTrigger value="people">People</TabsTrigger>
          <TabsTrigger value="allowances">Allowances</TabsTrigger>
          <TabsTrigger value="holidays">Holidays</TabsTrigger>
          <TabsTrigger value="backfill">Backfill</TabsTrigger>
          <TabsTrigger value="policy">Policy</TabsTrigger>
        </TabsList>

        <TabsContent value="people" className="space-y-4 pt-4">
          <NewUserForm
            leads={users.filter((u) => u.role !== "user" && u.is_active)}
            onDone={(message) => {
              setNotice(message);
              reload();
            }}
            onError={setError}
          />
          <UserTable
            users={users}
            currency={currency}
            onChanged={reload}
            onError={setError}
          />
        </TabsContent>

        <TabsContent value="allowances" className="space-y-4 pt-4">
          <AllowanceForm
            onDone={(message) => {
              setNotice(message);
              reload();
            }}
            onError={setError}
          />
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Set allowances</CardTitle>
              <CardDescription>
                A row with no person is the organisation default. It applies from
                its period onwards until another one replaces it.
              </CardDescription>
            </CardHeader>
            <CardContent className="overflow-x-auto p-0">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th className="p-3 font-medium">Period</th>
                    <th className="p-3 font-medium">Category</th>
                    <th className="p-3 text-right font-medium">Days</th>
                    <th className="p-3 font-medium">Applies to</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {allowances.map((row) => (
                    <tr key={row.id}>
                      <td className="p-3 tabular-nums">{row.period}</td>
                      <td className="p-3">{CATEGORY_LABEL[row.category]}</td>
                      <td className="p-3 text-right tabular-nums">{row.days}</td>
                      <td className="p-3 text-muted-foreground">
                        {row.user_id
                          ? users.find((u) => u.id === row.user_id)?.display_name ?? "—"
                          : "Everyone"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="holidays" className="space-y-4 pt-4">
          <HolidayForm
            onDone={(message) => {
              setNotice(message);
              reload();
            }}
            onError={setError}
          />
          <Card>
            <CardContent className="divide-y p-0">
              {holidays.map((holiday) => (
                <div key={holiday.id} className="flex items-center gap-3 p-3">
                  <span className="tabular-nums text-sm text-muted-foreground">
                    {holiday.date}
                  </span>
                  <span className="flex-1 text-sm font-medium">{holiday.name}</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={async () => {
                      try {
                        await deleteHoliday(holiday.id);
                        setNotice(`Removed ${holiday.name}.`);
                        reload();
                      } catch (err) {
                        setError(errorMessage(err));
                      }
                    }}
                  >
                    Remove
                  </Button>
                </div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="backfill" className="space-y-4 pt-4">
          <BackfillPanel
            people={users}
            entries={backfills}
            onDone={(message) => {
              setNotice(message);
              reload();
            }}
            onError={setError}
          />
        </TabsContent>

        <TabsContent value="policy" className="space-y-4 pt-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Open questions</CardTitle>
              <CardDescription>
                Each of these answers a question in the specification that has
                not been formally closed. They are settings, not code — see
                specs/001-leave-calendar/spec.md §11.
              </CardDescription>
            </CardHeader>
            <CardContent className="divide-y p-0">
              {settings.map((setting) => (
                <div key={setting.key} className="space-y-1 p-3">
                  <div className="flex items-center gap-2">
                    <code className="text-xs font-medium">{setting.key}</code>
                    <Badge variant="secondary">{JSON.stringify(setting.value)}</Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">{setting.description}</p>
                </div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

function NewUserForm({
  leads,
  onDone,
  onError,
}: {
  leads: PortalUser[];
  onDone: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [form, setForm] = useState({
    email: "",
    display_name: "",
    role: "user",
    lead_id: "",
  });
  const [busy, setBusy] = useState(false);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Add someone</CardTitle>
        <CardDescription>
          Accounts exist only because an admin makes one (FR-AUTH-02). No
          password is set — they sign in with Google (FR-AUTH-08), so the
          address must be one they can sign in to Google with. Use the Nunnari
          convention — firstname.nl@gmail.com — where the person has no company
          address.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            try {
              await createUser({ ...form, lead_id: form.lead_id || null });
              onDone(`${form.display_name} can now sign in.`);
              setForm({ email: "", display_name: "", role: "user", lead_id: "" });
            } catch (err) {
              onError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="name">Name</Label>
            <Input
              id="name"
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new-email">Email</Label>
            <Input
              id="new-email"
              type="email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              placeholder="firstname.nl@gmail.com"
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="role">Role</Label>
            <select
              id="role"
              className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value })}
            >
              {ROLES.map((role) => (
                <option key={role} value={role}>
                  {ROLE_LABEL[role]}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground">
              A manager runs projects and sees their money; a lead approves
              leave for their reports. One person can be both — set the role
              to manager and make them somebody&apos;s approver below.
            </p>
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="lead">Approved by</Label>
            <select
              id="lead"
              className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
              value={form.lead_id}
              onChange={(e) => setForm({ ...form, lead_id: e.target.value })}
            >
              {/* Q-05 — somebody with no lead has their leave approved by an
                  admin, which is the right answer for a lead or an admin. */}
              <option value="">An admin</option>
              {leads.map((lead) => (
                <option key={lead.id} value={lead.id}>
                  {lead.display_name}
                </option>
              ))}
            </select>
          </div>
          <div className="sm:col-span-2">
            <Button type="submit" disabled={busy}>
              {busy ? "Creating…" : "Create account"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function UserTable({
  users,
  currency,
  onChanged,
  onError,
}: {
  users: PortalUser[];
  currency: string;
  onChanged: () => void;
  onError: (message: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">People</CardTitle>
        <CardDescription>
          The cost rate is the fully-loaded hourly cost the company attributes to
          a person&apos;s time, used to work out what a project cost. It is not
          what anybody is paid. Only managers and admins ever see it, and a
          person without one makes every project they touch report an incomplete
          cost rather than a cheaper one.
        </CardDescription>
      </CardHeader>
      <CardContent className="overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th className="p-3 font-medium">Name</th>
              <th className="p-3 font-medium">Role</th>
              <th className="p-3 font-medium">Approved by</th>
              <th className="p-3 font-medium">Cost rate ({currency}/h)</th>
              <th className="p-3" />
            </tr>
          </thead>
          <tbody className="divide-y">
            {users.map((user) => (
              <tr key={user.id} className={user.is_active ? "" : "opacity-50"}>
                <td className="p-3">
                  <span className="font-medium">{user.display_name}</span>
                  <span className="block text-xs text-muted-foreground">{user.email}</span>
                </td>
                <td className="p-3">
                  {/* FR-ROLE-06 — only an admin changes a role. */}
                  <select
                    aria-label={`Role for ${user.display_name}`}
                    className="h-8 rounded-md border border-input bg-transparent px-2 text-sm"
                    value={user.role}
                    disabled={!user.is_active}
                    onChange={async (e) => {
                      try {
                        await updateUser(user.id, { role: e.target.value });
                        onChanged();
                      } catch (err) {
                        onError(errorMessage(err));
                      }
                    }}
                  >
                    {ROLES.map((role) => (
                      <option key={role} value={role}>
                        {ROLE_LABEL[role]}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="p-3 text-muted-foreground">
                  {user.lead_id
                    ? users.find((u) => u.id === user.lead_id)?.display_name ?? "—"
                    : "An admin"}
                </td>
                <td className="p-3">
                  <CostRateField
                    key={user.cost_rate_hourly ?? ""}
                    user={user}
                    onChanged={onChanged}
                    onError={onError}
                  />
                </td>
                <td className="p-3 text-right">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={async () => {
                      try {
                        // FR-AUTH-06 — deactivation, never deletion. Their
                        // bookings and consumption stay in the ledger.
                        await updateUser(user.id, { is_active: !user.is_active });
                        onChanged();
                      } catch (err) {
                        onError(errorMessage(err));
                      }
                    }}
                  >
                    {user.is_active ? "Deactivate" : "Reactivate"}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

/**
 * FR-FIN-01 — an admin sets a person's cost rate.
 *
 * Saves on blur or Enter, not on every keystroke: each save is audited
 * (FR-FIN-08) and a rate typed digit by digit would be five audit rows. Keyed
 * on the saved value by the caller, so a reload reseeds the field.
 */
function CostRateField({
  user,
  onChanged,
  onError,
}: {
  user: PortalUser;
  onChanged: () => void;
  onError: (message: string) => void;
}) {
  const [value, setValue] = useState(
    user.cost_rate_hourly ? String(Number(user.cost_rate_hourly)) : ""
  );
  const [busy, setBusy] = useState(false);

  async function commit() {
    const next = value.trim();
    const current = user.cost_rate_hourly ?? "";
    const unchanged =
      next === current || (next !== "" && current !== "" && Number(next) === Number(current));
    if (unchanged) return;
    setBusy(true);
    try {
      await updateUser(user.id, { cost_rate_hourly: next || null });
      onChanged();
    } catch (err) {
      onError(errorMessage(err));
      setValue(current);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Input
      type="number"
      inputMode="decimal"
      min="0"
      step="50"
      aria-label={`Cost rate for ${user.display_name}`}
      className="h-8 w-28 tabular-nums"
      value={value}
      disabled={busy || !user.is_active}
      placeholder="not set"
      onChange={(e) => setValue(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
      }}
    />
  );
}

function AllowanceForm({
  onDone,
  onError,
}: {
  onDone: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [form, setForm] = useState({
    period: currentPeriod(),
    category: "casual" as Category,
    days: "1.5",
  });
  const [busy, setBusy] = useState(false);

  return (
    <Card>
      <CardContent className="p-4">
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            try {
              await setAllowance({ ...form, user_id: null });
              onDone(
                `${CATEGORY_LABEL[form.category]} set to ${form.days} days from ${form.period}.`
              );
            } catch (err) {
              onError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="period">From month</Label>
            <Input
              id="period"
              type="month"
              value={form.period}
              onChange={(e) => setForm({ ...form, period: e.target.value })}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="category">Category</Label>
            <select
              id="category"
              className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
              value={form.category}
              onChange={(e) => setForm({ ...form, category: e.target.value as Category })}
            >
              {CATEGORIES.map((category) => (
                <option key={category} value={category}>
                  {CATEGORY_LABEL[category]}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="days">Days per month</Label>
            <Input
              id="days"
              type="number"
              step="0.5"
              min="0"
              className="w-28"
              value={form.days}
              onChange={(e) => setForm({ ...form, days: e.target.value })}
              required
            />
          </div>
          <Button type="submit" disabled={busy}>
            {busy ? "Saving…" : "Set"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function HolidayForm({
  onDone,
  onError,
}: {
  onDone: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [form, setForm] = useState({ date: "", name: "" });
  const [busy, setBusy] = useState(false);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Declare a holiday</CardTitle>
        <CardDescription>
          Applies to everyone and consumes nobody&apos;s allowance. Anyone who
          had already booked that day gets their days back and is told.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            try {
              const created = await declareHoliday(form);
              // FR-HOL-05 — saying how many bookings were released matters:
              // the admin has just changed other people's plans.
              const released = created.released_bookings ?? 0;
              onDone(
                released > 0
                  ? `${form.name} declared. ${released} existing booking${
                      released === 1 ? " was" : "s were"
                    } released and the days returned.`
                  : `${form.name} declared.`
              );
              setForm({ date: "", name: "" });
            } catch (err) {
              onError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="holiday-date">Date</Label>
            <Input
              id="holiday-date"
              type="date"
              value={form.date}
              onChange={(e) => setForm({ ...form, date: e.target.value })}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="holiday-name">Name</Label>
            <Input
              id="holiday-name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Independence Day"
              required
            />
          </div>
          <Button type="submit" disabled={busy}>
            {busy ? "Declaring…" : "Declare"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
