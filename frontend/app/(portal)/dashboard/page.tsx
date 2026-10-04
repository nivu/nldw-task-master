"use client";

import Link from "next/link";
import { AlertTriangle, ArrowRight, Lock } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageSkeleton } from "@/components/ui/skeleton";
import { LinesByPerson, MoneyByMonth } from "@/components/shared/charts";
import { formatMoney } from "@/components/portal/projects-panel";
import { BackendError, getDashboard } from "@/lib/api/portal";
import type { Category, DashboardAlert, DashboardCoverage, DashboardSummary, PnlCell, Rag } from "@/lib/api/types";
import { CATEGORY_LABEL } from "@/lib/api/types";
import { useAsync } from "@/lib/use-async";
import { cn } from "@/lib/utils";

/**
 * The CEO dashboard — spec 003 FR-DASH.
 *
 * Seen by the owner and whoever the owner authorises, never by role; the
 * server refuses everyone else and this page shows that refusal rather than
 * an empty screen. Every figure is the one its detail page shows (the links
 * go there), so nothing is computed here beyond the month-on-month arrow.
 * Incompleteness is loud, as on the money pages: a figure missing somebody's
 * CTC or a project's timeline says so beside the number.
 */

const RAG: Record<Rag, string> = { green: "bg-emerald-500", amber: "bg-amber-500", red: "bg-red-500" };
const SEVERITY: Record<DashboardAlert["severity"], string> = {
  high: "border-l-red-500",
  medium: "border-l-amber-500",
  low: "border-l-muted-foreground/40",
};

type Loaded = { summary: DashboardSummary; refused?: undefined } | { refused: string; summary?: undefined };

export default function DashboardPage() {
  const { data, error } = useAsync<Loaded>(async () => {
    try {
      return { summary: await getDashboard() };
    } catch (err) {
      // FR-DASH-02 — a refusal is an answer, not a failure: say so plainly.
      if (err instanceof BackendError && err.status === 403) return { refused: err.detail };
      throw err;
    }
  }, []);

  if (error) {
    return <div role="alert" className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">{error}</div>;
  }
  if (!data) return <PageSkeleton rows={5} />;
  if (data.refused !== undefined) {
    return (
      <Card>
        <CardContent className="flex items-start gap-3 p-6 text-sm">
          <Lock className="mt-0.5 size-4 text-muted-foreground" />
          <div>
            <p className="font-medium">You do not have access to the dashboard</p>
            <p className="text-muted-foreground">{data.refused} Ask the owner if you need it.</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  const d = data.summary;
  const c = d.money.currency;
  const pretty = new Date(`${d.today.date}T00:00:00`).toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="font-heading text-lg font-semibold">Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          {pretty}
          {d.today.holiday && ` · ${d.today.holiday}`}
        </p>
        {d.attention.length > 0 && (
          <a href="#attention" className="ml-auto text-sm text-muted-foreground hover:text-foreground">
            {d.attention.length} to look at ↓
          </a>
        )}
      </div>

      <IncompleteMoney money={d.money} />

      {/* Today and data health — whether the rest of the page can be trusted. */}
      <div className="grid gap-4 md:grid-cols-2">
        <TodayCard today={d.today} />
        <DataHealthCard health={d.data_health} />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <MoneyCard money={d.money} />
        <CashCard cash={d.cash} />
        <DeliveryCard delivery={d.delivery} currency={c} />
        <PeopleCard people={d.people} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Twelve months</CardTitle>
          <CardDescription>
            Revenue, cost and profit % from the monthly profit table — ended months use
            logged hours, this month is planned. Utilisation is billable hours against
            capacity; coverage is the share of expected timesheet days logged (none before
            the portal start date).
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 px-2 md:grid-cols-2">
          <MoneyByMonth
            currency={c}
            rows={d.trends.map((t) => ({
              period: t.period,
              revenue: Number(t.revenue),
              cost: t.cost === null ? null : Number(t.cost),
              profit_pct: t.profit_pct === null ? null : Number(t.profit_pct),
              planned: t.basis === "planned",
            }))}
          />
          <LinesByPerson
            months={d.trends.map((t) => t.period)}
            target={Number(d.people.utilisation.target_pct)}
            series={[
              { name: "Utilisation", values: d.trends.map((t) => (t.utilisation_pct === null ? null : Number(t.utilisation_pct))) },
              { name: "Coverage", values: d.trends.map((t) => (t.coverage === null ? null : Math.round(Number(t.coverage) * 100))) },
            ]}
          />
          {d.trends.some((t) => !t.complete) && (
            <p className="px-2 text-xs text-amber-700 dark:text-amber-300 md:col-span-2">
              Incomplete months:{" "}
              {d.trends
                .filter((t) => !t.complete)
                .map((t) => monthLabel(t.period))
                .join(", ")}{" "}
              — somebody&apos;s CTC is missing, so cost and profit there are not the whole picture.
            </p>
          )}
        </CardContent>
      </Card>

      <section id="attention" className="space-y-3">
        <h2 className="font-heading text-sm font-semibold">Needs attention</h2>
        {d.attention.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing flagged today.</p>
        ) : (
          <div className="space-y-2">
            {d.attention.map((a, i) => (
              <Link key={i} href={a.link} className="block">
                <Card className={cn("border-l-4 transition-colors hover:bg-muted/60", SEVERITY[a.severity])}>
                  <CardContent className="flex items-start gap-3 p-3 text-sm">
                    <Badge variant={a.severity === "high" ? "destructive" : "outline"} className="mt-0.5 shrink-0">
                      {a.severity}
                    </Badge>
                    <div className="min-w-0 flex-1">
                      <p className="font-medium">{a.title}</p>
                      <p className="text-muted-foreground">{a.detail}</p>
                    </div>
                    <ArrowRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </section>

      <p className="text-xs text-muted-foreground">
        The pages these figures link to keep their own access: being able to see this
        dashboard does not open them.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------

const monthLabel = (period: string) =>
  new Date(`${period}-01T00:00:00`).toLocaleDateString("en-GB", { month: "short", year: "2-digit" });

const pctOf = (ratio: string | null) => (ratio === null ? "—" : `${Math.round(Number(ratio) * 100)}%`);

function amount(currency: string, value: string | null, complete = true) {
  if (value === null) return complete ? "—" : "unknown";
  return `${currency} ${formatMoney(value)}`;
}

function Stat({
  label,
  value,
  hint,
  incomplete,
  warn,
}: {
  label: string;
  value: string;
  hint?: React.ReactNode;
  incomplete?: boolean;
  warn?: boolean;
}) {
  return (
    <div className={cn("min-w-0 rounded-md p-2", incomplete && "bg-amber-50 dark:bg-amber-950/30")}>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn("truncate text-lg font-semibold tabular-nums", warn && "text-destructive")} title={value}>
        {value}
      </p>
      {incomplete && (
        <Badge variant="outline" className="border-amber-400 text-amber-800 dark:text-amber-200">
          incomplete
        </Badge>
      )}
      {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}

/** ▲▼ against last month, in percent of last month's figure. */
function Change({ now, before, suffix = "" }: { now: string | null; before: string | null; suffix?: string }) {
  if (now === null || before === null || Number(before) === 0) return null;
  const change = ((Number(now) - Number(before)) / Math.abs(Number(before))) * 100;
  const up = change >= 0;
  return (
    <span className={cn("tabular-nums", up ? "text-emerald-600 dark:text-emerald-400" : "text-destructive")}>
      {up ? "▲" : "▼"} {Math.abs(change).toFixed(0)}%{suffix}
    </span>
  );
}

/** FR-FIN-06 — named, before any number, so it is never quoted as complete. */
function IncompleteMoney({ money }: { money: DashboardSummary["money"] }) {
  const { unrated, no_timeline } = money.incomplete;
  if (unrated.length === 0 && no_timeline.length === 0) return null;
  return (
    <div className="flex gap-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <div>
        <p className="font-medium">This or last month&apos;s money is incomplete</p>
        {unrated.length > 0 && (
          <p className="mt-1">
            No CTC recorded for {unrated.map((u) => u.display_name).join(", ")} on some of these days, so
            their time costs an <strong>unknown</strong> amount — not nothing. Recorded under Admin → People.
          </p>
        )}
        {no_timeline.length > 0 && (
          <p className="mt-1">
            {no_timeline.map((p) => p.project_name).join(", ")} {no_timeline.length === 1 ? "has" : "have"} revenue
            but no phases, so no monthly revenue. Set under Projects.
          </p>
        )}
      </div>
    </div>
  );
}

function TodayCard({ today }: { today: DashboardSummary["today"] }) {
  const s = today.status;
  const away = Object.entries(s.leave).filter(([, n]) => (n ?? 0) > 0) as [Category, number][];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Today</CardTitle>
        <CardDescription>{s.people} active people, company-wide.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label="In" value={String(s.present)} />
          <Stat label="Working from home" value={String(s.wfh)} />
          <Stat
            label="On leave"
            value={String(away.reduce((sum, [, n]) => sum + n, 0))}
            hint={away.map(([cat, n]) => `${CATEGORY_LABEL[cat] ?? cat} ${n}`).join(" · ") || undefined}
          />
          <Stat label="Unrecognised" value={String(s.unrecognised)} warn={s.unrecognised > 0} />
        </div>
        <Link href="/approvals" className="flex items-center gap-2 text-sm hover:underline">
          <span className="flex-1">
            <span className="font-medium tabular-nums">{today.pending_approvals}</span> leave request
            {today.pending_approvals === 1 ? "" : "s"} waiting
            {today.pending_compoff_claims > 0 && ` · ${today.pending_compoff_claims} comp-off claim${today.pending_compoff_claims === 1 ? "" : "s"}`}
          </span>
          <ArrowRight className="size-4 text-muted-foreground" />
        </Link>
      </CardContent>
    </Card>
  );
}

function DataHealthCard({ health }: { health: DashboardSummary["data_health"] }) {
  const tile = (label: string, cov: DashboardCoverage) => (
    <Stat
      label={label}
      value={pctOf(cov.coverage)}
      warn={cov.coverage !== null && Number(cov.coverage) < 0.8}
      hint={`${cov.logged_days} of ${cov.expected_days} days`}
    />
  );
  const missing = health.missing_last_working_day;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Data health</CardTitle>
        <CardDescription>
          Timesheet coverage. Every effort and cost figure is only as good as this.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-3 gap-2">
          {tile(
            new Date(`${health.last_working_day.start}T00:00:00`).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" }),
            health.last_working_day
          )}
          {tile("Week to date", health.week_to_date)}
          {tile("Month to date", health.month_to_date)}
        </div>
        <p className="text-sm">
          {missing.length === 0 ? (
            <span className="text-muted-foreground">Everyone logged the last working day.</span>
          ) : (
            <>
              <span className="font-medium">Not logged: </span>
              {missing.map((m) => m.display_name).join(", ")}
            </>
          )}
        </p>
      </CardContent>
    </Card>
  );
}

function MoneyCard({ money }: { money: DashboardSummary["money"] }) {
  const c = money.currency;
  const now = money.this_month;
  const last = money.last_month;
  const profitLine = (cell: PnlCell) =>
    cell.profit_pct === null ? "—" : `${Number(cell.profit_pct).toFixed(0)}%`;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Money · {monthLabel(now.period)}</CardTitle>
        <CardDescription>
          This month is planned from allocations; {monthLabel(last.period)} is actual.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <Stat
            label="Revenue"
            value={amount(c, now.revenue)}
            hint={<><Change now={now.revenue} before={last.revenue} /> vs {amount(c, last.revenue)}</>}
          />
          <Stat
            label="Cost"
            value={amount(c, now.cost, now.complete)}
            incomplete={!now.complete}
            hint={`last month ${amount(c, last.cost, last.complete)}`}
          />
          <Stat
            label="Profit"
            value={amount(c, now.profit, now.complete)}
            incomplete={!now.complete}
            warn={now.profit !== null && Number(now.profit) < 0}
            hint={<><Change now={now.profit} before={last.profit} /> vs {amount(c, last.profit, last.complete)}</>}
          />
          <Stat
            label="Profit %"
            value={profitLine(now)}
            incomplete={!now.complete}
            hint={`last month ${profitLine(last)}${last.complete ? "" : " (incomplete)"}`}
          />
        </div>
        <table className="w-full text-xs">
          <tbody className="divide-y">
            {money.categories.map((cat) => (
              <tr key={cat.category} className={cn(!cat.complete && "bg-amber-50 dark:bg-amber-950/30")}>
                <td className="py-1.5 pr-2">{cat.label}</td>
                <td className="py-1.5 text-right tabular-nums">{amount(c, cat.revenue)}</td>
                <td className="py-1.5 text-right tabular-nums text-muted-foreground">
                  {cat.profit_pct === null ? "—" : `${Number(cat.profit_pct).toFixed(0)}%`}
                  {!cat.complete && " · incomplete"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="rounded-md bg-muted/50 p-2 text-xs">
          <span className="font-medium">Pipeline (tentative, not in the totals): </span>
          {amount(c, money.pipeline.revenue)} this month
          {money.pipeline.weighted_revenue !== null && `, ${amount(c, money.pipeline.weighted_revenue)} weighted by probability`}
          .
        </p>
      </CardContent>
    </Card>
  );
}

function CashCard({ cash }: { cash: DashboardSummary["cash"] }) {
  const c = cash.currency;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Cash</CardTitle>
        <CardDescription>From invoicing milestones; payment terms {cash.payment_terms_days} days.</CardDescription>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-2">
        <Stat label="Receivable" value={amount(c, cash.totals.receivable)} hint="invoiced, not paid" />
        <Stat
          label="Overdue"
          value={amount(c, cash.totals.overdue_receivable)}
          warn={cash.overdue_count > 0}
          hint={`${cash.overdue_count} invoice${cash.overdue_count === 1 ? "" : "s"} past terms${
            cash.uninvoiced_overdue_count > 0 ? ` · ${cash.uninvoiced_overdue_count} not yet invoiced` : ""
          }`}
        />
        <Stat label="Due next 30 days" value={amount(c, cash.totals.due_next_30_days)} hint="to invoice" />
        <Stat label="Paid this month" value={amount(c, cash.totals.paid_this_month)} />
      </CardContent>
    </Card>
  );
}

function DeliveryCard({ delivery, currency }: { delivery: DashboardSummary["delivery"]; currency: string }) {
  const flagged = [...delivery.red.map((p) => ({ ...p, rag: "red" as Rag })), ...delivery.amber.map((p) => ({ ...p, rag: "amber" as Rag }))];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Delivery</CardTitle>
        <CardDescription>Project health — the worst of burn, margin and schedule.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="flex gap-4">
          {(["green", "amber", "red"] as Rag[]).map((rag) => (
            <span key={rag} className="flex items-center gap-1.5">
              <span className={cn("size-2.5 rounded-full", RAG[rag])} />
              <span className="font-semibold tabular-nums">{delivery.counts[rag]}</span>
              <span className="text-muted-foreground">{rag}</span>
            </span>
          ))}
        </div>
        {flagged.length > 0 && (
          <ul className="space-y-1">
            {flagged.map((p) => (
              <li key={p.project_id} className="flex items-start gap-2">
                <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", RAG[p.rag])} />
                <span>
                  <span className="font-medium">{p.project_name}</span>
                  <span className="block text-xs text-muted-foreground">{p.reasons.join(" ")}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
        {delivery.spillover.length > 0 && (
          <div>
            <p className="text-xs font-medium">In spill-over (unpaid)</p>
            {delivery.spillover.map((s) => (
              <p key={s.project_id} className={cn("text-xs", !s.complete && "text-amber-700 dark:text-amber-300")}>
                {s.project_name}: {amount(currency, s.cost, s.complete)} planned cost this month, {amount(currency, s.revenue)} revenue
                {!s.complete && " · incomplete"}
              </p>
            ))}
          </div>
        )}
        {delivery.burn_over_80.length > 0 && (
          <div>
            <p className="text-xs font-medium">Hours budget over 80% burnt</p>
            {delivery.burn_over_80.map((b) => (
              <p key={b.project_id} className="text-xs text-muted-foreground">
                {b.project_name}: {Number(b.burn_pct).toFixed(0)}% ({b.logged_hours} of {b.budget_hours}h)
              </p>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function PeopleCard({ people }: { people: DashboardSummary["people"] }) {
  const u = people.utilisation;
  const below = u.utilisation_pct !== null && Number(u.utilisation_pct) < Number(u.target_pct);
  const names = (rows: { display_name: string }[]) => rows.map((r) => r.display_name).join(", ");
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">People</CardTitle>
        <CardDescription>Everyone who keeps a timesheet and is employed today.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="grid grid-cols-2 gap-2">
          <Stat label="Headcount" value={String(people.headcount)} />
          <Stat
            label={`Billable utilisation · ${monthLabel(u.period)}`}
            value={u.utilisation_pct === null ? "—" : `${Number(u.utilisation_pct).toFixed(0)}%`}
            warn={below}
            hint={`target ${u.target_pct}% · month so far against the month's capacity`}
          />
        </div>
        <p>
          <span className="font-medium">Unallocated today: </span>
          {people.unallocated.length === 0 ? <span className="text-muted-foreground">nobody</span> : names(people.unallocated)}
        </p>
        <p>
          <span className="font-medium">Work ends within 30 days, nothing after: </span>
          {people.ending.length === 0 ? (
            <span className="text-muted-foreground">nobody</span>
          ) : (
            people.ending.map((p) => `${p.display_name} (${p.ends_on})`).join(", ")
          )}
        </p>
        <p>
          <span className="font-medium">Over-allocated (next 30 days): </span>
          {people.over_allocated.length === 0 ? (
            <span className="text-muted-foreground">nobody</span>
          ) : (
            people.over_allocated.map((o) => `${o.display_name} ${o.peak_percent}%`).join(", ")
          )}
        </p>
      </CardContent>
    </Card>
  );
}
