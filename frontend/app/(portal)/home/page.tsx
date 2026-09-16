"use client";

import Link from "next/link";
import { AlertTriangle, ArrowRight, CheckCircle2, Clock } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageSkeleton } from "@/components/ui/skeleton";
import { Ring } from "@/components/shared/charts";
import { getHome } from "@/lib/api/portal";
import type { Category, HomeSummary, Rag } from "@/lib/api/types";
import { CATEGORY_LABEL } from "@/lib/api/types";
import { useAsync } from "@/lib/use-async";
import { cn } from "@/lib/utils";

/**
 * Home — what needs me today.
 *
 * One screen, one call, blocks in order of who sees them: everyone's own
 * day and week first, then the lead's queue, the manager's projects, the
 * admin's loose ends. Every tile links to the page that does the work; the
 * page itself does none. Sorted by name, never by any number.
 */

const RING: Record<Category, string> = { wfh: "#0ea5e9", casual: "#8b5cf6", sick: "#f43f5e", compoff: "#10b981" };
const RAG: Record<Rag, string> = { green: "bg-emerald-500", amber: "bg-amber-500", red: "bg-red-500" };

export default function HomePage() {
  const { data, error } = useAsync<HomeSummary>(() => getHome(), []);

  if (error) {
    return <div role="alert" className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">{error}</div>;
  }
  if (!data) return <PageSkeleton rows={4} />;

  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  const pretty = new Date(`${data.today}T00:00:00`).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  const m = data.mine;
  const weekMax = Math.max(8, ...m.week.days.map((d) => Number(d.total)));

  return (
    <div className="space-y-5">
      <div>
        <h1 className="font-heading text-lg font-semibold">
          {greeting}, {data.display_name.split(" ")[0]}
        </h1>
        <p className="text-sm text-muted-foreground">
          {pretty}
          {m.today && ` · ${m.today.label}${m.today.duration === "0.5" ? " (half day)" : ""}, ${m.today.status}`}
        </p>
      </div>

      {/* The one thing most people need to do today. */}
      {!m.logged_today && !m.today && (
        <Card className="border-amber-300 dark:border-amber-800">
          <CardContent className="flex flex-wrap items-center gap-3 p-4">
            <Clock className="size-5 text-amber-600" />
            <div className="flex-1 text-sm">
              <p className="font-medium">Nothing logged for today yet</p>
              <p className="text-muted-foreground">Takes under a minute. What did you work on?</p>
            </div>
            <Button render={<Link href="/timesheet" />} nativeButton={false}>
              Log today <ArrowRight className="size-4" />
            </Button>
          </CardContent>
        </Card>
      )}
      {m.logged_today && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <CheckCircle2 className="size-4 text-emerald-600" /> Today is logged.
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {/* This week's hours, as a bar per day. */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">This week</CardTitle>
            <CardDescription>{m.week.total} hours logged so far.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex items-end gap-2">
              {m.week.days.map((d) => {
                const h = Number(d.total);
                const off = d.holiday || (d.on_leave && d.on_leave !== null);
                return (
                  <Link
                    key={d.date}
                    href="/timesheet"
                    className="flex flex-1 flex-col items-center gap-1"
                    title={`${d.date}: ${h}h${d.holiday ? " · holiday" : ""}${d.on_leave ? ` · ${d.on_leave}` : ""}`}
                  >
                    <div className="flex h-24 w-full items-end rounded bg-muted/60">
                      <div
                        className={cn("w-full rounded", off ? "bg-muted-foreground/30" : d.is_today ? "bg-sky-500" : "bg-sky-300 dark:bg-sky-700")}
                        style={{ height: `${Math.max(h > 0 ? 6 : 0, (h / weekMax) * 100)}%` }}
                      />
                    </div>
                    <span className={cn("text-[11px]", d.is_today ? "font-semibold" : "text-muted-foreground")}>
                      {new Date(`${d.date}T00:00:00`).toLocaleDateString("en-GB", { weekday: "short" }).slice(0, 2)}
                    </span>
                  </Link>
                );
              })}
            </div>
          </CardContent>
        </Card>

        {/* Balances as rings. */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Your balances</CardTitle>
            <CardDescription>
              {m.pending_requests > 0
                ? `${m.pending_requests} request${m.pending_requests === 1 ? "" : "s"} waiting for your lead.`
                : "Days left this month."}
              {Number(m.compoff_available) > 0 && ` ${m.compoff_available} comp-off day${m.compoff_available === "1.0" ? "" : "s"} available.`}
            </CardDescription>
          </CardHeader>
          <CardContent className="grid grid-cols-3 gap-2">
            {m.balances.map((b) => (
              <Ring
                key={b.category}
                used={Number(b.used)}
                total={Number(b.opening) + Number(b.allowance)}
                label={CATEGORY_LABEL[b.category]}
                colour={RING[b.category]}
                size={52}
              />
            ))}
          </CardContent>
        </Card>
      </div>

      {m.upcoming.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Coming up</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {m.upcoming.map((u) => (
              <Badge key={u.date} variant={u.status === "approved" ? "secondary" : "outline"}>
                {new Date(`${u.date}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" })} · {u.label}
                {u.status === "pending" && " · pending"}
              </Badge>
            ))}
          </CardContent>
        </Card>
      )}

      {data.lead && (
        <section className="space-y-3">
          <h2 className="font-heading text-sm font-semibold">Your team</h2>
          <div className="grid gap-4 md:grid-cols-3">
            <Tile
              href="/approvals"
              title="Approvals"
              value={data.lead.approvals_waiting}
              hint={data.lead.approvals_waiting === 0 ? "Nothing waiting" : "waiting for you"}
              warn={data.lead.approvals_waiting > 0}
            />
            <Tile
              href="/team"
              title="Comp-off claims"
              value={data.lead.compoff_claims_waiting}
              hint={data.lead.compoff_claims_waiting === 0 ? "Nothing waiting" : "to decide"}
              warn={data.lead.compoff_claims_waiting > 0}
            />
            <Tile
              href="/team"
              title="Gaps this week"
              value={data.lead.gaps_this_week.length}
              hint={data.lead.gaps_this_week.length === 0 ? "Everyone is up to date" : data.lead.gaps_this_week.map((g) => `${g.display_name} ${g.missing}`).join(" · ")}
              warn={data.lead.gaps_this_week.length > 0}
            />
          </div>
          <Card>
            <CardContent className="p-3 text-sm">
              <span className="font-medium">Out today: </span>
              {data.lead.out_today.length === 0
                ? "nobody"
                : data.lead.out_today.map((o) => `${o.display_name} (${o.label}${o.duration === "0.5" ? ", half day" : ""})`).join(" · ")}
            </CardContent>
          </Card>
        </section>
      )}

      {data.manager && (
        <section className="space-y-3">
          <h2 className="font-heading text-sm font-semibold">Projects</h2>
          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Health</CardTitle>
                <CardDescription>Burn, margin and schedule — worst of the three.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {data.manager.health.length === 0 && <p className="text-sm text-muted-foreground">No active projects.</p>}
                {data.manager.health.map((h) => (
                  <Link key={h.project_id} href="/analytics" className="flex items-center gap-2 text-sm hover:underline">
                    <span className={cn("size-2.5 rounded-full", RAG[h.overall])} />
                    <span className="flex-1">{h.project_name}</span>
                    <span className="text-xs text-muted-foreground">{h.overall}</span>
                  </Link>
                ))}
              </CardContent>
            </Card>
            <div className="grid gap-4">
              <Tile
                href="/analytics"
                title="Over-allocated this week"
                value={data.manager.over_allocated.length}
                hint={data.manager.over_allocated.length === 0 ? "Nobody" : data.manager.over_allocated.map((o) => `${o.display_name} ${o.peak_percent}%`).join(" · ")}
                warn={data.manager.over_allocated.length > 0}
              />
              <Tile
                href="/analytics"
                title="On the bench"
                value={data.manager.bench.length}
                hint={data.manager.bench.length === 0 ? "Everyone is allocated" : data.manager.bench.join(", ")}
              />
              {data.manager.unrated.length > 0 && (
                <Tile href="/admin" title="No CTC recorded" value={data.manager.unrated.length} hint={data.manager.unrated.join(", ")} warn />
              )}
            </div>
          </div>
        </section>
      )}

      {data.admin && (
        <section className="space-y-3">
          <h2 className="font-heading text-sm font-semibold">Admin</h2>
          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Checklists in progress</CardTitle>
              </CardHeader>
              <CardContent className="space-y-1 text-sm">
                {data.admin.open_checklists.length === 0 && <p className="text-muted-foreground">None open.</p>}
                {data.admin.open_checklists.map((c, i) => (
                  <Link key={i} href="/admin" className="flex items-center gap-2 hover:underline">
                    <span className="flex-1">{c.display_name} · {c.kind}</span>
                    <span className="tabular-nums text-muted-foreground">{c.done}/{c.total}</span>
                  </Link>
                ))}
              </CardContent>
            </Card>
            <Card className={!data.admin.slack_configured ? "border-amber-300 dark:border-amber-800" : undefined}>
              <CardContent className="flex items-start gap-3 p-4 text-sm">
                {!data.admin.slack_configured && <AlertTriangle className="mt-0.5 size-4 text-amber-600" />}
                <div>
                  <p className="font-medium">
                    {data.admin.slack_configured ? "Slack is connected" : "Slack is not connected"}
                  </p>
                  <p className="text-muted-foreground">
                    {data.admin.slack_configured
                      ? data.admin.slack_out_channel
                        ? `Morning post goes to ${data.admin.slack_out_channel}.`
                        : "Set slack_out_channel under Policy to turn on the morning post."
                      : "Nudges and the digest send nothing until a bot token is set on the server."}
                  </p>
                </div>
              </CardContent>
            </Card>
          </div>
        </section>
      )}
    </div>
  );
}

function Tile({ href, title, value, hint, warn }: { href: string; title: string; value: number; hint: string; warn?: boolean }) {
  return (
    <Link href={href} className="block">
      <Card className={cn("h-full transition-colors hover:bg-muted/60", warn && "border-amber-300 dark:border-amber-800")}>
        <CardContent className="p-4">
          <p className="text-xs text-muted-foreground">{title}</p>
          <p className={cn("text-2xl font-semibold tabular-nums", value === 0 && "text-muted-foreground")}>{value}</p>
          <p className="truncate text-xs text-muted-foreground" title={hint}>{hint}</p>
        </CardContent>
      </Card>
    </Link>
  );
}
