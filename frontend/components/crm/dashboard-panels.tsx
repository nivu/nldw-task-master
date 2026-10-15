"use client";

import { CapacityPanel } from "@/components/crm/capacity-panel";
import { BarsByLabel } from "@/components/shared/charts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { BookedFigure, Colour, CrmDashboard, PipelineFigures } from "@/lib/api/crm-types";
import type { MePreferences } from "@/lib/api/types";
import { formatAmount, formatDate } from "@/lib/crm/format";
import { cn } from "@/lib/utils";

/**
 * The dashboard's panels — spec 007 FR-CRM-DASH-02 to -04, FR-CRM-CAP-02.
 *
 * Every chart draws numbers its table shows beside it; the table stays, the
 * chart is the glance (`components/shared/charts.tsx`). Nothing is computed
 * here that the API did not already decide. A colour appears only where the
 * API sent one (a target, a flag threshold), and always with its word, never
 * colour alone. Per-owner rows stay in the API's order, which is by name
 * (FR-CRM-DASH-04).
 */

type Prefs = Pick<MePreferences, "timezone" | "number_format">;

const COLOUR_CLASS: Record<Colour, string> = {
  green: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  amber: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  red: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
};
const COLOUR_WORD: Record<Colour, string> = { green: "On track", amber: "Behind", red: "Well behind" };

function inr(value: string | null | undefined, prefs: Prefs): string {
  return value === null || value === undefined ? "—" : formatAmount(value, "INR", prefs.number_format);
}

function Pill({ colour, word }: { colour: Colour; word?: string }) {
  return (
    <span className={cn("rounded px-1.5 py-0.5 text-xs font-medium", COLOUR_CLASS[colour])}>
      {word ?? COLOUR_WORD[colour]}
    </span>
  );
}

function Panel({
  title,
  description,
  children,
  testId,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <Card data-testid={testId}>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent className="space-y-3">{children}</CardContent>
    </Card>
  );
}

function Table({ head, rows }: { head: string[]; rows: React.ReactNode[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs text-muted-foreground">
            {head.map((h, i) => (
              <th key={h} className={cn("p-2 font-medium", i > 0 && "text-right")}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((cells, n) => (
            <tr key={n}>
              {cells.map((cell, i) => (
                <td key={i} className={cn("p-2", i > 0 && "text-right tabular-nums")}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function pipelineRows<T extends PipelineFigures & { label: string }>(rows: T[], prefs: Prefs) {
  return rows.map((r) => [r.label, r.count, inr(r.amount_in_inr, prefs), inr(r.weighted, prefs)]);
}

function PipelineGroup({
  title,
  rows,
  prefs,
}: {
  title: string;
  rows: (PipelineFigures & { label: string })[];
  prefs: Prefs;
}) {
  return (
    <Panel title={title}>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No open deals.</p>
      ) : (
        <Table head={["", "Deals", "Amount", "Weighted"]} rows={pipelineRows(rows, prefs)} />
      )}
    </Panel>
  );
}

function Booked({ figure, prefs }: { figure: BookedFigure; prefs: Prefs }) {
  return (
    <span className="inline-flex flex-wrap items-center justify-end gap-1.5">
      <span>{inr(figure.value, prefs)}</span>
      {figure.target && <span className="text-muted-foreground">of {inr(figure.target, prefs)}</span>}
      {figure.colour && <Pill colour={figure.colour} />}
    </span>
  );
}

export function DashboardPanels({ data, prefs }: { data: CrmDashboard; prefs: Prefs }) {
  const stages = data.pipeline_by_stage.stages;
  const winRate = (rate: string | null) => (rate === null ? "—" : `${rate}%`);
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel title="Open pipeline by stage" description="Amounts in INR; weighted by each deal's probability." testId="panel-pipeline">
        <BarsByLabel
          rows={stages.map((s) => ({
            label: s.stage_label,
            values: { Amount: Number(s.amount_in_inr ?? 0), Weighted: Number(s.weighted ?? 0) },
          }))}
          series={["Amount", "Weighted"]}
          format={(v) => formatAmount(String(v), "INR", prefs.number_format)}
        />
        <Table
          head={["Stage", "Deals", "Amount", "Weighted"]}
          rows={[
            ...pipelineRows(stages.map((s) => ({ ...s, label: s.stage_label })), prefs),
            [
              <strong key="t">Total</strong>,
              data.pipeline_by_stage.total.count,
              inr(data.pipeline_by_stage.total.amount_in_inr, prefs),
              inr(data.pipeline_by_stage.total.weighted, prefs),
            ],
          ]}
        />
      </Panel>

      <Panel
        title="Bookings"
        description="Won this month, quarter and financial year (April–March). A colour shows only against a target an admin has set."
        testId="panel-bookings"
      >
        <Table
          head={["Period", "Deals", "Booked"]}
          rows={data.bookings.flatMap((b) => [
            [
              `${b.label} (${formatDate(b.start, prefs.timezone)} – ${formatDate(b.end, prefs.timezone)})`,
              b.count,
              <Booked key="b" figure={b} prefs={prefs} />,
            ],
            ...(b.by_entity ?? []).map((e) => [
              <span key="e" className="pl-4 text-muted-foreground">
                {e.entity_label}
              </span>,
              e.count,
              <Booked key="b" figure={e} prefs={prefs} />,
            ]),
          ])}
        />
      </Panel>

      <Panel title="Won and lost this quarter" testId="panel-won-lost">
        <Table
          head={["", "Deals", "Amount"]}
          rows={[
            ["Won", data.won_lost_quarter.won.count, inr(data.won_lost_quarter.won.amount_in_inr, prefs)],
            ["Lost", data.won_lost_quarter.lost.count, inr(data.won_lost_quarter.lost.amount_in_inr, prefs)],
          ]}
        />
        {data.won_lost_quarter.lost_reasons.length > 0 && (
          <Table
            head={["Lost reason", "Deals"]}
            rows={data.won_lost_quarter.lost_reasons.map((r) => [r.label, r.count])}
          />
        )}
      </Panel>

      <Panel
        title="Win rate and average deal size"
        description={`Deals closed ${formatDate(data.range.from, prefs.timezone)} – ${formatDate(data.range.to, prefs.timezone)}.`}
        testId="panel-win-rate"
      >
        <Table
          head={["", "Won", "Lost", "Win rate", "Average won"]}
          rows={[
            ["All", data.win_rate.overall.won, data.win_rate.overall.lost, winRate(data.win_rate.overall.win_rate), inr(data.win_rate.overall.average_deal_size, prefs)],
            ...(data.win_rate.by_entity ?? []).map((e) => [e.entity_label, e.won, e.lost, winRate(e.win_rate), inr(e.average_deal_size, prefs)]),
            ...data.win_rate.by_channel.map((c) => [c.label, c.won, c.lost, winRate(c.win_rate), inr(c.average_deal_size, prefs)]),
          ]}
        />
      </Panel>

      <Panel
        title="Time in stage"
        description="Average days in each stage, over the stays that ended in the range; days to close from a deal's first stage to its win."
        testId="panel-time-in-stage"
      >
        <Table
          head={["Stage", "Average days", "Stays"]}
          rows={[
            ...data.time_in_stage.stages.map((s) => [s.stage_label, s.average_days ?? "—", s.stays]),
            ["Days to close", data.time_in_stage.days_to_close.average_days ?? "—", data.time_in_stage.days_to_close.deals],
          ]}
        />
      </Panel>

      <Panel title="Follow-up" description="Records carrying each flag now." testId="panel-flags">
        <Table
          head={["", "Records"]}
          rows={data.flags.map((f) => [
            f.label,
            <span key="v" className="inline-flex items-center justify-end gap-1.5">
              {f.value}
              <Pill colour={f.colour} word={f.colour === "green" ? "None" : f.colour === "amber" ? "Due soon" : "Overdue"} />
            </span>,
          ])}
        />
      </Panel>

      <Panel title="Lead funnel" description="Companies by lead stage, partners left out; conversion is the share that went on to the next stage." testId="panel-funnel">
        <Table
          head={["Lead stage", "Now", "Reached", "On to next"]}
          rows={[
            ...data.lead_funnel.stages.map((s) => [
              s.label,
              s.count,
              s.reached,
              s.conversion === null ? "—" : `${s.conversion}%`,
            ]),
            ["Disqualified", data.lead_funnel.disqualified, "", ""],
          ]}
        />
      </Panel>

      <PipelineGroup title="Pipeline by channel" rows={data.pipeline_by_channel} prefs={prefs} />
      <PipelineGroup title="Pipeline by source campaign" rows={data.pipeline_by_campaign} prefs={prefs} />
      {data.pipeline_by_institution_type && (
        <PipelineGroup title="Pipeline by institution type" rows={data.pipeline_by_institution_type} prefs={prefs} />
      )}
      {data.pipeline_by_programme && (
        <PipelineGroup title="Pipeline by programme" rows={data.pipeline_by_programme} prefs={prefs} />
      )}

      <div className="lg:col-span-2">
        <Panel
          title="Activity"
          description="Emails in and out, notes written and tasks completed, per person per week, in name order."
          testId="panel-activity"
        >
          <Table
            head={["Person", "Emails in", "Emails out", "Notes", "Tasks"]}
            rows={data.activity.owners.map((o) => [
              o.name ?? "Former member",
              o.totals.emails_in,
              o.totals.emails_out,
              o.totals.notes,
              o.totals.tasks,
            ])}
          />
          {data.activity.weeks.length > 0 && data.activity.owners.length > 0 && (
            <details>
              <summary className="cursor-pointer text-sm text-muted-foreground">By week</summary>
              <Table
                head={["Person · week", "Emails in", "Emails out", "Notes", "Tasks"]}
                rows={data.activity.owners.flatMap((o) =>
                  o.weeks.map((w) => [
                    `${o.name ?? "Former member"} · ${formatDate(w.week_start, prefs.timezone)}`,
                    w.emails_in,
                    w.emails_out,
                    w.notes,
                    w.tasks,
                  ])
                )}
              />
            </details>
          )}
        </Panel>
      </div>

      {data.capacity && (
        <div className="lg:col-span-2">
          <Panel title="Capacity" description="The next 13 weeks: hours tentative projects would need, against hours nobody is promised to yet." testId="panel-capacity">
            <CapacityPanel weeks={data.capacity.weeks} timeZone={prefs.timezone} />
          </Panel>
        </div>
      )}
    </div>
  );
}
