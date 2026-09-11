"use client";

import { useState } from "react";
import { AlertTriangle, ChevronLeft, ChevronRight } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatMoney } from "@/components/portal/projects-panel";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  getAnalyticsProjects,
  getCoverage,
  getCurrentWork,
  getForecast,
  getMe,
  getPeopleFinancials,
  getPnl,
  getProjectEffort,
  getProjectFinancials,
  getTimeline,
} from "@/lib/api/portal";
import type {
  Coverage,
  CurrentWork,
  Forecast,
  PeopleFinancials,
  Pnl,
  Project,
  ProjectEffort,
  ProjectFinancials,
  Timeline,
} from "@/lib/api/types";
import { useAsync } from "@/lib/use-async";
import { cn } from "@/lib/utils";

/**
 * Effort analytics — spec 002 §5.4.
 *
 * Two things govern this page and both are easy to lose while making it
 * prettier.
 *
 * FR-ANALYTICS-05: coverage comes FIRST, above every effort total. A total
 * computed over a partly-filled timesheet is not imprecise, it is biased low,
 * and it gets quoted in a budget conversation as though it were complete.
 * Putting it last would make it decoration.
 *
 * Spec 002 §10: project totals, never person leaderboards. The same data
 * answers "this project needed more people than we budgeted" and "this person
 * logged fewer hours than that one", and the second reading arrives for free
 * unless the page actively declines to serve it. Nothing here ranks people or
 * sorts them by hours.
 *
 * Spec 003 adds two tabs for the manager tier only — Money and Resources.
 * They are rendered on a capability the server handed us and every call
 * behind them is guarded again server-side (FR-FIN-07): a lead who edits the
 * DOM to reveal the tab gets a refusal, not a number.
 */
export default function AnalyticsPage() {
  const [projectId, setProjectId] = useState<string | null>(null);

  const { data, error } = useAsync<{
    projects: Project[];
    coverage: Coverage;
    forecast: Forecast;
    current: CurrentWork[];
    financials: boolean;
  }>(async () => {
    const [projects, coverage, forecast, current, me] = await Promise.all([
      getAnalyticsProjects(),
      getCoverage(),
      getForecast(),
      getCurrentWork(7),
      getMe(),
    ]);
    return { projects, coverage, forecast, current, financials: me.capabilities.financials };
  }, []);

  if (error) {
    return (
      <div role="alert" className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">
        {error}
      </div>
    );
  }
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <div className="space-y-5">
      <h1 className="font-heading text-lg font-semibold">Analytics</h1>

      <CoverageBanner coverage={data.coverage} />

      <Tabs defaultValue="projects">
        {/* Scrolls sideways on a phone rather than overlapping (NFR-01). */}
        <TabsList className="max-w-full overflow-x-auto">
          <TabsTrigger value="projects">Projects</TabsTrigger>
          <TabsTrigger value="current">Right now</TabsTrigger>
          <TabsTrigger value="forecast">Forecast</TabsTrigger>
          <TabsTrigger value="coverage">Coverage</TabsTrigger>
          {data.financials && <TabsTrigger value="resources">Resources</TabsTrigger>}
          {data.financials && <TabsTrigger value="money">Money</TabsTrigger>}
        </TabsList>

        <TabsContent value="projects" className="space-y-3 pt-4">
          {projectId ? (
            <ProjectDetail
              projectId={projectId}
              financials={data.financials}
              onBack={() => setProjectId(null)}
            />
          ) : (
            <Card>
              <CardContent className="divide-y p-0">
                {data.projects.length === 0 && (
                  <p className="p-4 text-sm text-muted-foreground">
                    No projects yet. A manager or admin adds them under Projects.
                  </p>
                )}
                {data.projects.map((project) => (
                  <button
                    key={project.id}
                    onClick={() => setProjectId(project.id)}
                    className="flex w-full items-center gap-3 p-3 text-left hover:bg-muted/60"
                  >
                    <span className="flex-1">
                      <span className="text-sm font-medium">{project.name}</span>
                      {project.client && (
                        <span className="block text-xs text-muted-foreground">{project.client}</span>
                      )}
                    </span>
                    {project.is_archived && <Badge variant="outline">archived</Badge>}
                    <span className="tabular-nums text-sm">{project.logged_hours}h</span>
                  </button>
                ))}
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="current" className="space-y-3 pt-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">The last seven days</CardTitle>
              <CardDescription>
                What people have actually been working on — from logged hours, not
                from what they were allocated to.
              </CardDescription>
            </CardHeader>
            <CardContent className="divide-y p-0">
              {data.current.map((person) => (
                <div key={person.user_id} className="space-y-1 p-3">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{person.display_name}</span>
                    <span className="ml-auto tabular-nums text-sm text-muted-foreground">
                      {person.total}h
                    </span>
                  </div>
                  {person.projects.length === 0 ? (
                    <p className="text-xs text-muted-foreground">Nothing logged.</p>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      {person.projects.map((p) => `${p.project_name} (${p.hours}h)`).join(" · ")}
                    </p>
                  )}
                  {person.latest_note && (
                    <p className="text-xs italic text-muted-foreground">
                      &ldquo;{person.latest_note}&rdquo;
                    </p>
                  )}
                </div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="forecast" className="space-y-3 pt-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Capacity from allocations</CardTitle>
              <CardDescription>
                The next 90 days, excluding weekends, declared holidays and approved
                leave. A forecast over raw calendar days would say a team has
                capacity it does not have.
              </CardDescription>
            </CardHeader>
            <CardContent className="divide-y p-0">
              {data.forecast.projects.length === 0 && (
                <p className="p-4 text-sm text-muted-foreground">
                  Nobody is allocated to anything yet.
                </p>
              )}
              {data.forecast.projects.map((project) => (
                <div key={project.project_id} className="space-y-1 p-3">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{project.project_name}</span>
                    <span className="ml-auto tabular-nums text-sm">
                      {project.capacity_hours}h
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {project.people
                      .map((p) => `${p.display_name} @${p.percent}% → ${p.hours}h`)
                      .join(" · ")}
                  </p>
                </div>
              ))}
            </CardContent>
          </Card>

          {/* FR-ALLOC-04 — surfaced, not forbidden. */}
          {data.forecast.over_allocated.length > 0 && (
            <Card className="border-amber-300 dark:border-amber-800">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <AlertTriangle className="size-4 text-amber-600" />
                  Promised to more work than exists
                </CardTitle>
              </CardHeader>
              <CardContent className="divide-y p-0">
                {data.forecast.over_allocated.map((person) => (
                  <div key={person.user_id} className="p-3 text-sm">
                    <span className="font-medium">{person.display_name}</span>
                    <span className="text-muted-foreground">
                      {" "}
                      — {person.days} day{person.days === 1 ? "" : "s"} from {person.first} to{" "}
                      {person.last}, peaking at {person.peak_percent}%
                    </span>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {data.financials && (
          <TabsContent value="resources" className="space-y-3 pt-4">
            <ResourcesTab />
          </TabsContent>
        )}

        {data.financials && (
          <TabsContent value="money" className="space-y-3 pt-4">
            <MoneyTab onOpenProject={setProjectId} projects={data.projects} />
          </TabsContent>
        )}

        <TabsContent value="coverage" className="space-y-3 pt-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Who has logged what</CardTitle>
              <CardDescription>
                Working days only — weekends, holidays and full days of approved
                leave are not gaps.
              </CardDescription>
            </CardHeader>
            <CardContent className="divide-y p-0">
              {data.coverage.people.map((person) => (
                <div key={person.user_id} className="flex items-center gap-3 p-3 text-sm">
                  <span className="flex-1 font-medium">{person.display_name}</span>
                  <span className="tabular-nums text-muted-foreground">
                    {person.logged_days}/{person.expected_days} days
                  </span>
                  {person.missing_days.length > 0 && (
                    <Badge variant="outline">{person.missing_days.length} missing</Badge>
                  )}
                </div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

/**
 * FR-ANALYTICS-05, given the position it deserves.
 *
 * Everything else on this page is only as trustworthy as this number, so it
 * sits above the tabs rather than inside one.
 */
function CoverageBanner({ coverage }: { coverage: Coverage }) {
  if (coverage.coverage === null) return null;
  const ratio = Number(coverage.coverage);
  const complete = ratio >= 0.95;

  return (
    <div
      className={cn(
        "rounded-md border p-3 text-sm",
        complete
          ? "border-border bg-muted/50"
          : "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
      )}
    >
      <p className="font-medium">
        {Math.round(ratio * 100)}% of the last 30 days is logged
        <span className="font-normal">
          {" "}
          ({coverage.logged_days} of {coverage.expected_days} working days)
        </span>
      </p>
      {!complete && (
        <p className="mt-1">
          Effort totals below are computed over an incomplete timesheet, so they
          are <strong>lower than reality</strong> — not merely approximate. Chase
          the gaps before quoting any of these numbers.
        </p>
      )}
    </div>
  );
}

function ProjectDetail({
  projectId,
  financials,
  onBack,
}: {
  projectId: string;
  financials: boolean;
  onBack: () => void;
}) {
  const { data, error } = useAsync<ProjectEffort>(() => getProjectEffort(projectId), [projectId]);

  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <div className="space-y-3">
      <button onClick={onBack} className="text-sm text-muted-foreground hover:text-foreground">
        ← All projects
      </button>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{data.project.name}</CardTitle>
          <CardDescription>
            {data.project.client ?? "Internal"} · {data.total.logged_hours}h logged
            {data.total.budget_hours && ` of ${data.total.budget_hours}h budgeted`} ·{" "}
            {data.total.hours_office}h office, {data.total.hours_home}h home
          </CardDescription>
        </CardHeader>
      </Card>

      {/* Spec 003 FR-FIN-04 — money, only for the manager tier. A separate
          request behind a separate guard, so the effort view above never
          carries a figure it must not. */}
      {financials && <ProjectMoney projectId={projectId} />}

      {data.phases.map((phase) => {
        const over = phase.over_by !== null && phase.over_by !== undefined;
        return (
          <Card key={phase.id} className={cn(over && "border-destructive/50")}>
            <CardContent className="space-y-2 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium">{phase.label}</span>
                <span className="text-xs text-muted-foreground">
                  {phase.starts_on} → {phase.ends_on}
                </span>
                {over && <Badge variant="destructive">over by {phase.over_by}h</Badge>}
              </div>
              <p className="text-sm">
                <span className="text-2xl font-semibold tabular-nums">{phase.logged_hours}</span>
                <span className="text-muted-foreground">
                  {phase.budget_hours ? ` of ${phase.budget_hours} hours` : " hours (no budget set)"}
                </span>
              </p>
              <p className="text-xs text-muted-foreground">
                {phase.hours_office}h office · {phase.hours_home}h home
              </p>
              {/* NFR-04 — every total decomposes. Sorted by name, never by
                  hours: ordering by contribution is how a table quietly
                  becomes a leaderboard. */}
              {phase.people && phase.people.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  {phase.people.map((p) => `${p.display_name} ${p.hours}h`).join(" · ")}
                </p>
              )}
            </CardContent>
          </Card>
        );
      })}

      {Number(data.outside_any_phase.logged_hours) > 0 && (
        <Card className="border-amber-300 dark:border-amber-800">
          <CardContent className="space-y-1 p-4">
            <p className="text-sm font-medium">Logged outside every phase window</p>
            <p className="text-2xl font-semibold tabular-nums">
              {data.outside_any_phase.logged_hours}
              <span className="ml-1 text-sm font-normal text-muted-foreground">hours</span>
            </p>
            <p className="text-xs text-muted-foreground">
              Work on this project on dates no phase covers — usually a phase whose
              dates need extending, and exactly the overrun that goes unnoticed
              when it is not named.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Spec 003 — money and resourcing. Rendered only for the manager tier.
// ---------------------------------------------------------------------------

const money = (currency: string, value: string | null) =>
  value === null ? "—" : `${currency} ${formatMoney(value)}`;

/**
 * FR-FIN-06 given the same treatment as coverage: an incomplete figure is
 * flagged BEFORE the number, because a margin that quietly omits somebody's
 * cost is a better number than the truth and will be quoted as the truth.
 */
function IncompleteNotice({
  unrated,
  what,
}: {
  unrated: { user_id: string; display_name: string }[];
  what: string;
}) {
  if (unrated.length === 0) return null;
  return (
    <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
      <p className="font-medium">
        {what} {unrated.length === 1 ? "is" : "are"} incomplete
      </p>
      <p className="mt-1">
        {unrated.map((p) => p.display_name).join(", ")}{" "}
        {unrated.length === 1 ? "has" : "have"} no CTC recorded for some of these
        days, so their time costs an <strong>unknown</strong> amount — not
        nothing. An admin records CTC under Admin → People.
      </p>
    </div>
  );
}

function ProjectMoney({ projectId }: { projectId: string }) {
  const { data, error } = useAsync<ProjectFinancials>(
    () => getProjectFinancials(projectId),
    [projectId]
  );

  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!data) return null;

  const c = data.currency;
  return (
    <div className="space-y-3">
      <IncompleteNotice unrated={data.unrated} what="Cost and margin" />
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Money</CardTitle>
          <CardDescription>
            Cost is hours × each person&apos;s hourly cost from the CTC in force on
            the day the hours were logged, so a CTC change later never re-prices
            this project.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Figure label="Revenue" value={money(c, data.revenue)} />
            <Figure
              label={data.complete ? "Cost (COGS)" : "Cost so far (incomplete)"}
              value={money(c, data.complete ? data.cogs : data.cogs_partial)}
              muted={!data.complete}
            />
            <Figure label="Margin" value={money(c, data.margin)} />
            <Figure
              label="Margin %"
              value={data.margin_pct === null ? "—" : `${data.margin_pct}%`}
            />
          </div>
          {data.revenue === null && (
            <p className="text-xs text-muted-foreground">
              No revenue set for this project, so there is no margin to report.
              Set it under Projects.
            </p>
          )}
          {data.people.length > 0 && (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="py-2 font-medium">Person</th>
                  <th className="py-2 text-right font-medium">Hours</th>
                  <th className="py-2 text-right font-medium">Cost</th>
                  <th className="py-2 text-right font-medium">Attributed revenue</th>
                </tr>
              </thead>
              {/* Sorted by name, never by money — spec 003 §9. */}
              <tbody className="divide-y">
                {data.people.map((p) => (
                  <tr key={p.user_id}>
                    <td className="py-2">{p.display_name}</td>
                    <td className="py-2 text-right tabular-nums">{p.hours}</td>
                    <td className="py-2 text-right tabular-nums">
                      {p.cogs === null ? (
                        <Badge variant="outline">unrated</Badge>
                      ) : (
                        money(c, p.cogs)
                      )}
                    </td>
                    <td className="py-2 text-right tabular-nums">
                      {money(c, p.attributed_revenue)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Figure({ label, value, muted }: { label: string; value: string; muted?: boolean }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn("text-lg font-semibold tabular-nums", muted && "text-muted-foreground")}>
        {value}
      </p>
    </div>
  );
}

/**
 * FR-FIN-05 — each person across every project.
 *
 * Per project AND per person is the whole of what spec 003 asks for. There is
 * no column sort here on purpose: sorting by cost or by attributed revenue is
 * how this table becomes a leaderboard without anybody deciding it should
 * (spec 003 §9).
 */
function MoneyTab({
  projects,
  onOpenProject,
}: {
  projects: Project[];
  onOpenProject: (id: string) => void;
}) {
  const { data, error } = useAsync<PeopleFinancials>(() => getPeopleFinancials(), []);

  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const c = data.currency;
  return (
    <div className="space-y-3">
      <IncompleteNotice unrated={data.unrated} what="Some of these figures" />

      <MonthlyProfit currency={c} />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Per person, across projects</CardTitle>
          <CardDescription>
            Attributed revenue is each project&apos;s revenue shared out by hours.
            It says what a person&apos;s time went into, not what they are worth —
            and it is never shown to the person themselves.
          </CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th className="p-3 font-medium">Person</th>
                <th className="p-3 text-right font-medium">Projects</th>
                <th className="p-3 text-right font-medium">Hours</th>
                <th className="p-3 text-right font-medium">Cost</th>
                <th className="p-3 text-right font-medium">Attributed revenue</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {data.people.length === 0 && (
                <tr>
                  <td colSpan={5} className="p-4 text-muted-foreground">
                    No project hours logged yet.
                  </td>
                </tr>
              )}
              {data.people.map((p) => (
                <tr key={p.user_id}>
                  <td className="p-3 font-medium">{p.display_name}</td>
                  <td className="p-3 text-right tabular-nums">{p.projects}</td>
                  <td className="p-3 text-right tabular-nums">{p.hours}</td>
                  <td className="p-3 text-right tabular-nums">
                    {p.complete ? money(c, p.cogs) : <Badge variant="outline">incomplete</Badge>}
                  </td>
                  <td className="p-3 text-right tabular-nums">{money(c, p.attributed_revenue)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Per project</CardTitle>
          <CardDescription>Open a project for its revenue, cost and margin.</CardDescription>
        </CardHeader>
        <CardContent className="divide-y p-0">
          {projects.map((project) => (
            <button
              key={project.id}
              onClick={() => onOpenProject(project.id)}
              className="flex w-full items-center gap-3 p-3 text-left hover:bg-muted/60"
            >
              <span className="flex-1 text-sm font-medium">{project.name}</span>
              {project.is_archived && <Badge variant="outline">archived</Badge>}
              <span className="tabular-nums text-sm text-muted-foreground">
                {project.logged_hours}h
              </span>
            </button>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

/**
 * Spec 005 FR-PNL — revenue, cost and profit by month.
 *
 * Past months are actual (logged hours), the current and future months are
 * planned (allocations). Incompleteness is loud per cell: a person with no
 * CTC for some of the month, or a project with revenue but no timeline, says
 * so where the number would otherwise be quietly wrong.
 */
function MonthlyProfit({ currency }: { currency: string }) {
  const [offset, setOffset] = useState(0);
  const range = (() => {
    const now = new Date();
    const s = new Date(now.getFullYear(), now.getMonth() - 3 + offset * 9, 1);
    const e = new Date(now.getFullYear(), now.getMonth() + 5 + offset * 9, 1);
    const ym = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    return { start: ym(s), end: ym(e) };
  })();
  const { data, error } = useAsync<Pnl>(() => getPnl(range.start, range.end), [range.start, range.end]);
  const [view, setView] = useState<"people" | "projects">("people");

  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const label = (period: string) =>
    new Date(`${period}-01T00:00:00`).toLocaleDateString("en-GB", { month: "short", year: "2-digit" });
  const pct = (v: string | null) => (v === null ? "—" : `${Number(v).toFixed(0)}%`);
  const amt = (v: string | null) => (v === null ? "?" : formatMoney(v));

  const rows =
    view === "people"
      ? data.people.map((p) => ({ key: p.user_id, name: p.display_name, cells: p.cells, note: null as string | null }))
      : data.projects
          .filter((p) => !p.is_archived || p.cells.some((c) => Number(c.revenue) > 0))
          .map((p) => ({
            key: p.project_id,
            name: p.project_name,
            cells: p.cells,
            note: p.revenue !== null && !p.has_timeline ? "no phases, so no monthly revenue" : null,
          }));

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex-1">
            <CardTitle className="text-base">By month</CardTitle>
            <CardDescription>
              Revenue, cost and profit. Months that have ended use logged hours;
              the current month and the future use allocations and are marked planned.
            </CardDescription>
          </div>
          <div className="flex gap-1">
            <Button variant={view === "people" ? "secondary" : "outline"} size="sm" onClick={() => setView("people")}>
              People
            </Button>
            <Button variant={view === "projects" ? "secondary" : "outline"} size="sm" onClick={() => setView("projects")}>
              Projects
            </Button>
            <Button variant="outline" size="icon" onClick={() => setOffset(offset - 1)} aria-label="Earlier months">
              <ChevronLeft className="size-4" />
            </Button>
            <Button variant="outline" size="icon" onClick={() => setOffset(offset + 1)} aria-label="Later months">
              <ChevronRight className="size-4" />
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 p-0">
        {data.unrated.length > 0 && (
          <div className="px-4 pt-1">
            <IncompleteNotice unrated={data.unrated} what="Some months" />
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="sticky left-0 bg-card p-2 font-medium">{view === "people" ? "Person" : "Project"}</th>
                {data.months.map((m) => (
                  <th key={m.period} className="p-2 text-right font-medium whitespace-nowrap">
                    {label(m.period)}
                    {m.basis === "planned" && (
                      <span className="block text-[10px] font-normal">planned</span>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y">
              {rows.map((row) => (
                <tr key={row.key}>
                  <td className="sticky left-0 bg-card p-2 font-medium whitespace-nowrap">
                    {row.name}
                    {row.note && <span className="block text-[10px] font-normal text-muted-foreground">{row.note}</span>}
                  </td>
                  {row.cells.map((cell, i) => (
                    <td
                      key={i}
                      className={cn(
                        "p-2 text-right tabular-nums align-top",
                        !cell.complete && "bg-amber-50 dark:bg-amber-950/30"
                      )}
                      title={`${currency} ${amt(cell.revenue)} revenue · ${amt(cell.cost)} cost${
                        cell.complete ? "" : " · incomplete"
                      }`}
                    >
                      <span className={cn("block font-medium", cell.profit !== null && Number(cell.profit) < 0 && "text-destructive")}>
                        {pct(cell.profit_pct)}
                      </span>
                      <span className="block text-muted-foreground">{amt(cell.revenue)}</span>
                      <span className="block text-muted-foreground">−{amt(cell.cost)}</span>
                    </td>
                  ))}
                </tr>
              ))}
              <tr className="border-t-2 font-medium">
                <td className="sticky left-0 bg-card p-2">Total</td>
                {data.totals.map((cell, i) => (
                  <td key={i} className="p-2 text-right tabular-nums align-top">
                    <span className="block">{pct(cell.profit_pct)}</span>
                    <span className="block text-muted-foreground">{amt(cell.revenue)}</span>
                    <span className="block text-muted-foreground">−{amt(cell.cost)}</span>
                    {Number(cell.unattributed) > 0 && (
                      <span className="block text-[10px] font-normal text-amber-700 dark:text-amber-300">
                        {amt(cell.unattributed)} unattributed
                      </span>
                    )}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
        <p className="px-4 pb-3 text-[11px] text-muted-foreground">
          Each cell: profit %, then revenue, then cost. &ldquo;?&rdquo; means a
          CTC is missing for some of the month. Sorted by name.
        </p>
      </CardContent>
    </Card>
  );
}

/** One colour per project, stable within a response. */
const PALETTE = [
  "bg-sky-500/80 text-white",
  "bg-emerald-500/80 text-white",
  "bg-violet-500/80 text-white",
  "bg-amber-500/85 text-black",
  "bg-rose-500/80 text-white",
  "bg-teal-500/80 text-white",
  "bg-indigo-500/80 text-white",
  "bg-orange-500/85 text-black",
  "bg-fuchsia-500/80 text-white",
  "bg-lime-500/85 text-black",
];

/**
 * Spec 005 FR-TL — the timeline matrix.
 *
 * People as rows, months (or weeks) as columns, each allocation a bar from
 * its start to its end. Height is the percent: 100% fills the row, two 50%
 * bars stack. Bars are positioned by date arithmetic against the visible
 * range, so a bar that begins before the range is clipped at the left edge
 * rather than lost.
 */
function ResourcesTab() {
  const [unit, setUnit] = useState<"month" | "week">("month");
  const [offset, setOffset] = useState(0);

  const range = (() => {
    const now = new Date();
    if (unit === "month") {
      const s = new Date(now.getFullYear(), now.getMonth() + offset * 6, 1);
      const e = new Date(now.getFullYear(), now.getMonth() + offset * 6 + 6, 0);
      return { start: s, end: e };
    }
    const monday = new Date(now);
    monday.setDate(now.getDate() - ((now.getDay() + 6) % 7) + offset * 8 * 7);
    const e = new Date(monday);
    e.setDate(monday.getDate() + 8 * 7 - 1);
    return { start: monday, end: e };
  })();
  const iso = (d: Date) => d.toISOString().slice(0, 10);

  const { data, error } = useAsync<Timeline>(
    () => getTimeline(iso(range.start), iso(range.end)),
    [unit, offset]
  );

  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const dayMs = 86400000;
  const rangeStart = new Date(`${data.start}T00:00:00`).getTime();
  const rangeEnd = new Date(`${data.end}T00:00:00`).getTime() + dayMs;
  const span = rangeEnd - rangeStart;

  // Column boundaries.
  const columns: { label: string; left: number; width: number }[] = [];
  if (unit === "month") {
    let cursor = new Date(range.start);
    while (cursor <= range.end) {
      const next = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1);
      const left = (cursor.getTime() - rangeStart) / span;
      const right = Math.min(next.getTime(), rangeEnd) - rangeStart;
      columns.push({
        label: cursor.toLocaleDateString("en-GB", { month: "short", year: "2-digit" }),
        left: left * 100,
        width: (right / span - left) * 100,
      });
      cursor = next;
    }
  } else {
    let cursor = new Date(range.start);
    while (cursor <= range.end) {
      const next = new Date(cursor);
      next.setDate(cursor.getDate() + 7);
      columns.push({
        label: cursor.toLocaleDateString("en-GB", { day: "numeric", month: "short" }),
        left: ((cursor.getTime() - rangeStart) / span) * 100,
        width: (7 * dayMs / span) * 100,
      });
      cursor = next;
    }
  }

  const ROW = 44; // px for 100%

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex-1">
            <CardTitle className="text-base">Who is on what</CardTitle>
            <CardDescription>
              One bar per allocation, coloured by project. A full-height bar is
              100% of the person; half-height is 50%. Over 100% overflows and is flagged.
            </CardDescription>
          </div>
          <div className="flex gap-1">
            <Button variant={unit === "month" ? "secondary" : "outline"} size="sm" onClick={() => { setUnit("month"); setOffset(0); }}>
              Months
            </Button>
            <Button variant={unit === "week" ? "secondary" : "outline"} size="sm" onClick={() => { setUnit("week"); setOffset(0); }}>
              Weeks
            </Button>
            <Button variant="outline" size="icon" onClick={() => setOffset(offset - 1)} aria-label="Earlier">
              <ChevronLeft className="size-4" />
            </Button>
            <Button variant="outline" size="icon" onClick={() => setOffset(offset + 1)} aria-label="Later">
              <ChevronRight className="size-4" />
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 p-0">
        {data.projects.length > 0 && (
          <div className="flex flex-wrap gap-2 px-4 text-xs">
            {data.projects.map((p) => (
              <span key={p.project_id} className={cn("rounded px-2 py-0.5", PALETTE[p.colour % PALETTE.length])}>
                {p.name}
              </span>
            ))}
          </div>
        )}
        <div className="overflow-x-auto">
          <div className="min-w-[720px]">
            {/* Header */}
            <div className="flex border-b text-xs text-muted-foreground">
              <div className="w-36 shrink-0 p-2 font-medium">Person</div>
              <div className="relative h-8 flex-1">
                {columns.map((c, i) => (
                  <div
                    key={i}
                    className="absolute top-0 h-full border-l px-1 py-2 whitespace-nowrap overflow-hidden"
                    style={{ left: `${c.left}%`, width: `${c.width}%` }}
                  >
                    {c.label}
                  </div>
                ))}
              </div>
            </div>
            {/* Rows */}
            {data.people.map((person) => {
              // Stack bars: each bar occupies a vertical slice proportional
              // to its percent, in order; the row grows past 100% when over.
              let top = 0;
              const bars = person.allocations.map((bar) => {
                const s = Math.max(new Date(`${bar.starts_on}T00:00:00`).getTime(), rangeStart);
                const e = Math.min(new Date(`${bar.ends_on}T00:00:00`).getTime() + dayMs, rangeEnd);
                const height = (Number(bar.percent) / 100) * ROW;
                const placed = { ...bar, left: ((s - rangeStart) / span) * 100, width: ((e - s) / span) * 100, top, height };
                top += height;
                return placed;
              });
              const rowHeight = Math.max(ROW, top);
              return (
                <div key={person.user_id} className={cn("flex border-b", person.over && "bg-destructive/5")}>
                  <div className="w-36 shrink-0 p-2 text-sm font-medium whitespace-nowrap">
                    {person.display_name}
                    {person.over && (
                      <span className="block text-[10px] font-normal text-destructive">
                        peaks at {person.peak_percent}%
                      </span>
                    )}
                  </div>
                  <div className="relative flex-1" style={{ height: rowHeight + 8 }}>
                    {columns.map((c, i) => (
                      <div key={i} className="absolute top-0 h-full border-l border-border/60" style={{ left: `${c.left}%` }} />
                    ))}
                    {bars.map((bar) => (
                      <div
                        key={bar.id}
                        title={`${bar.project_name} · ${bar.percent}% · ${bar.starts_on} → ${bar.ends_on}`}
                        className={cn(
                          "absolute overflow-hidden rounded px-1.5 text-[11px] leading-tight whitespace-nowrap",
                          PALETTE[bar.colour % PALETTE.length]
                        )}
                        style={{
                          left: `${bar.left}%`,
                          width: `calc(${bar.width}% - 2px)`,
                          top: bar.top + 4,
                          height: Math.max(bar.height - 2, 12),
                          lineHeight: `${Math.max(bar.height - 2, 12)}px`,
                        }}
                      >
                        {bar.project_name} {bar.percent}%
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
            {data.people.every((p) => p.allocations.length === 0) && (
              <p className="p-4 text-sm text-muted-foreground">Nobody is allocated in this range.</p>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
