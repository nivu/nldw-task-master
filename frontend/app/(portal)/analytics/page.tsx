"use client";

import { useState } from "react";
import { AlertTriangle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  getAnalyticsProjects,
  getCoverage,
  getCurrentWork,
  getForecast,
  getProjectEffort,
} from "@/lib/api/portal";
import type { Coverage, CurrentWork, Forecast, Project, ProjectEffort } from "@/lib/api/types";
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
 */
export default function AnalyticsPage() {
  const [projectId, setProjectId] = useState<string | null>(null);

  const { data, error } = useAsync<{
    projects: Project[];
    coverage: Coverage;
    forecast: Forecast;
    current: CurrentWork[];
  }>(async () => {
    const [projects, coverage, forecast, current] = await Promise.all([
      getAnalyticsProjects(),
      getCoverage(),
      getForecast(),
      getCurrentWork(7),
    ]);
    return { projects, coverage, forecast, current };
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
        <TabsList>
          <TabsTrigger value="projects">Projects</TabsTrigger>
          <TabsTrigger value="current">Right now</TabsTrigger>
          <TabsTrigger value="forecast">Forecast</TabsTrigger>
          <TabsTrigger value="coverage">Coverage</TabsTrigger>
        </TabsList>

        <TabsContent value="projects" className="space-y-3 pt-4">
          {projectId ? (
            <ProjectDetail projectId={projectId} onBack={() => setProjectId(null)} />
          ) : (
            <Card>
              <CardContent className="divide-y p-0">
                {data.projects.length === 0 && (
                  <p className="p-4 text-sm text-muted-foreground">
                    No projects yet. An admin adds them under Admin → Projects.
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

function ProjectDetail({ projectId, onBack }: { projectId: string; onBack: () => void }) {
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
