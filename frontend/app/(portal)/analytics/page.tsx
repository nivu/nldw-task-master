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
  getProjectEffort,
  getProjectFinancials,
  getResources,
} from "@/lib/api/portal";
import type {
  Coverage,
  CurrentWork,
  Forecast,
  PeopleFinancials,
  Project,
  ProjectEffort,
  ProjectFinancials,
  ResourcesTimeline,
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
        {unrated.length === 1 ? "has" : "have"} no cost rate, so their hours cost
        an <strong>unknown</strong> amount — not nothing. An admin sets cost
        rates under Admin → People.
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
            Cost is hours × each person&apos;s cost rate as it was when the hours
            were logged, so a rate change later never re-prices this project.
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
 * FR-RES — who is on what, week by week, now and next.
 *
 * A grid of people × weeks. Each cell is the week's peak allocation; over 100%
 * is red (FR-RES-02), under is left pale so free capacity is visible at a
 * glance (FR-RES-03), and approved leave is written in so a full fortnight
 * that is also a holiday reads as what it is (FR-RES-04).
 */
function ResourcesTab() {
  const [offset, setOffset] = useState(0);
  const range = (() => {
    const start = new Date();
    start.setDate(start.getDate() - 14 + offset * 7 * 8);
    const end = new Date(start);
    end.setDate(end.getDate() + 7 * 8 - 1);
    return { start: start.toISOString().slice(0, 10), end: end.toISOString().slice(0, 10) };
  })();

  const { data, error } = useAsync<ResourcesTimeline>(
    () => getResources(range.start, range.end),
    [range.start, range.end]
  );

  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const week = (iso: string) =>
    new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <div className="flex-1">
            <CardTitle className="text-base">Who is on what</CardTitle>
            <CardDescription>
              Peak allocation per week. Red is over 100%; pale is free capacity;
              leave is counted in days.
            </CardDescription>
          </div>
          <Button variant="outline" size="icon" onClick={() => setOffset(offset - 1)} aria-label="Earlier">
            <ChevronLeft className="size-4" />
          </Button>
          <Button variant="outline" size="icon" onClick={() => setOffset(offset + 1)} aria-label="Later">
            <ChevronRight className="size-4" />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="overflow-x-auto p-0">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="sticky left-0 bg-card p-2 font-medium">Person</th>
              {data.weeks.map((w) => (
                <th key={w} className="p-2 text-center font-medium whitespace-nowrap">
                  {week(w)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y">
            {data.people.map((person) => (
              <tr key={person.user_id}>
                <td className="sticky left-0 bg-card p-2 font-medium whitespace-nowrap">
                  {person.display_name}
                </td>
                {person.weeks.map((cell) => {
                  const pct = Number(cell.allocated_pct);
                  const title = [
                    ...cell.projects.map((p) => `${p.project_name} ${p.percent}%`),
                    Number(cell.leave_days) > 0 ? `${cell.leave_days}d leave` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ");
                  return (
                    <td key={cell.week_start} className="p-1">
                      <div
                        title={title || "Unallocated"}
                        className={cn(
                          "rounded px-1.5 py-1 text-center tabular-nums",
                          cell.working_days === 0
                            ? "text-muted-foreground"
                            : cell.over
                              ? "bg-destructive/15 font-medium text-destructive"
                              : pct >= 100
                                ? "bg-muted font-medium"
                                : pct > 0
                                  ? "bg-muted/50"
                                  : "text-muted-foreground"
                        )}
                      >
                        {cell.working_days === 0 ? "—" : `${pct}%`}
                        {Number(cell.leave_days) > 0 && (
                          <span className="block text-[10px] font-normal text-muted-foreground">
                            {cell.leave_days}d leave
                          </span>
                        )}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}
