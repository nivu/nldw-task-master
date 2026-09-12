"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { cn } from "@/lib/utils";

/**
 * Charts, kept deliberately plain.
 *
 * Every chart here draws numbers the page already shows in a table; the
 * table stays, the chart is the glance. Nothing is computed client-side that
 * the API did not already decide, and nothing ranks people — series are in
 * the order the API returned them, which is by name.
 */

export const SERIES = ["#0ea5e9", "#10b981", "#8b5cf6", "#f59e0b", "#f43f5e", "#14b8a6", "#6366f1", "#f97316", "#d946ef", "#84cc16"];
const GRID = "var(--border)";
const TEXT = "var(--muted-foreground)";

const axis = { tick: { fontSize: 11, fill: TEXT }, axisLine: false, tickLine: false } as const;

function monthLabel(period: string) {
  return new Date(`${period}-01T00:00:00`).toLocaleDateString("en-GB", { month: "short" });
}

const tooltipStyle = {
  contentStyle: {
    background: "var(--popover)",
    color: "var(--popover-foreground)",
    border: "1px solid var(--border)",
    borderRadius: 8,
    fontSize: 12,
  },
  labelStyle: { color: "var(--muted-foreground)" },
};

/** Revenue and cost as bars, profit % as a line — the monthly profit table at a glance. */
export function MoneyByMonth({
  rows,
  currency,
}: {
  rows: { period: string; revenue: number; cost: number | null; profit_pct: number | null; planned: boolean }[];
  currency: string;
}) {
  const data = rows.map((r) => ({ ...r, month: monthLabel(r.period) }));
  return (
    <div className="h-56 w-full">
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="month" {...axis} />
          <YAxis yAxisId="money" {...axis} width={48} tickFormatter={(v: number) => (v >= 1000 ? `${Math.round(v / 1000)}k` : String(v))} />
          <YAxis yAxisId="pct" orientation="right" {...axis} width={36} domain={[-100, 100]} tickFormatter={(v: number) => `${v}%`} />
          <Tooltip
            {...tooltipStyle}
            formatter={(value, name) =>
              name === "Profit %" ? [`${value}%`, name] : [`${currency} ${Number(value).toLocaleString("en-IN")}`, name]
            }
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar yAxisId="money" dataKey="revenue" name="Revenue" fill={SERIES[0]} radius={[3, 3, 0, 0]} />
          <Bar yAxisId="money" dataKey="cost" name="Cost" fill={SERIES[4]} radius={[3, 3, 0, 0]} />
          <Line yAxisId="pct" type="monotone" dataKey="profit_pct" name="Profit %" stroke={SERIES[1]} strokeWidth={2} dot={{ r: 3 }} connectNulls={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/** One line per person over months, with an optional target line. */
export function LinesByPerson({
  months,
  series,
  target,
  unit = "%",
}: {
  months: string[];
  series: { name: string; values: (number | null)[] }[];
  target?: number;
  unit?: string;
}) {
  const data = months.map((m, i) => {
    const row: Record<string, string | number | null> = { month: monthLabel(m) };
    for (const s of series) row[s.name] = s.values[i];
    return row;
  });
  return (
    <div className="h-56 w-full">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="month" {...axis} />
          <YAxis
            {...axis}
            width={36}
            domain={unit === "%" ? [0, 100] : ["auto", "auto"]}
            tickFormatter={(v: number) => `${v}${unit}`}
          />
          <Tooltip {...tooltipStyle} formatter={(value, name) => [`${value}${unit}`, name]} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {target !== undefined && (
            <ReferenceLine y={target} stroke={TEXT} strokeDasharray="4 4" label={{ value: `target ${target}${unit}`, fontSize: 10, fill: TEXT, position: "insideTopRight" }} />
          )}
          {series.map((s, i) => (
            <Line key={s.name} type="monotone" dataKey={s.name} stroke={SERIES[i % SERIES.length]} strokeWidth={2} dot={{ r: 2 }} connectNulls={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Grouped bars per month — demand against supply, or any two-to-four series. */
export function BarsByMonth({
  months,
  series,
  unit = "h",
}: {
  months: string[];
  series: { name: string; values: number[] }[];
  unit?: string;
}) {
  const data = months.map((m, i) => {
    const row: Record<string, string | number> = { month: monthLabel(m) };
    for (const s of series) row[s.name] = s.values[i];
    return row;
  });
  return (
    <div className="h-52 w-full">
      <ResponsiveContainer>
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="month" {...axis} />
          <YAxis {...axis} width={40} />
          <Tooltip {...tooltipStyle} formatter={(value, name) => [`${value}${unit}`, name]} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {series.map((s, i) => (
            <Bar key={s.name} dataKey={s.name} fill={SERIES[i % SERIES.length]} radius={[3, 3, 0, 0]} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Stacked bars per month — leave history by category. */
export function StackedByMonth({
  months,
  series,
  unit = "d",
}: {
  months: string[];
  series: { name: string; values: number[]; colour?: string }[];
  unit?: string;
}) {
  const data = months.map((m, i) => {
    const row: Record<string, string | number> = { month: monthLabel(m) };
    for (const s of series) row[s.name] = s.values[i];
    return row;
  });
  return (
    <div className="h-48 w-full">
      <ResponsiveContainer>
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="month" {...axis} />
          <YAxis {...axis} width={30} allowDecimals={false} />
          <Tooltip {...tooltipStyle} formatter={(value, name) => [`${value}${unit}`, name]} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {series.map((s, i) => (
            <Bar key={s.name} dataKey={s.name} stackId="a" fill={s.colour ?? SERIES[i % SERIES.length]} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/**
 * Horizontal progress bars — a value against a maximum, one row per item.
 * Plain divs: crisp at any size, no axis needed, reads on a phone.
 */
export function ProgressBars({
  rows,
  formatValue = (v) => String(v),
  over = "bg-red-500",
}: {
  rows: { name: string; value: number; max: number | null; hint?: string; colour?: string }[];
  formatValue?: (v: number, max: number | null) => string;
  over?: string;
}) {
  const scale = Math.max(1, ...rows.map((r) => Math.max(r.value, r.max ?? 0)));
  return (
    <div className="space-y-2">
      {rows.map((r) => {
        const pct = Math.min(100, (r.value / (r.max ?? scale)) * 100);
        const isOver = r.max !== null && r.value > r.max;
        return (
          <div key={r.name} className="space-y-1 text-xs">
            <div className="flex items-center gap-2">
              <span className="flex-1 truncate font-medium">{r.name}</span>
              <span className="tabular-nums text-muted-foreground">{formatValue(r.value, r.max)}</span>
              {r.hint && <span className="text-muted-foreground">{r.hint}</span>}
            </div>
            <div className="h-2 w-full overflow-hidden rounded bg-muted">
              <div className={cn("h-full rounded", isOver ? over : "")} style={{ width: `${pct}%`, background: isOver ? undefined : (r.colour ?? SERIES[0]) }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** A ring showing used against total — a balance at a glance. */
export function Ring({ used, total, label, colour = SERIES[0], size = 64 }: { used: number; total: number; label: string; colour?: string; size?: number }) {
  const r = size / 2 - 5;
  const c = 2 * Math.PI * r;
  const frac = total > 0 ? Math.min(1, used / total) : 0;
  return (
    <div className="flex items-center gap-3">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--muted)" strokeWidth={6} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={colour}
          strokeWidth={6}
          strokeLinecap="round"
          strokeDasharray={`${c * frac} ${c * (1 - frac)}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
        <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle" fontSize={12} fill="var(--foreground)" className="tabular-nums">
          {total - used}
        </text>
      </svg>
      <div className="text-xs">
        <p className="font-medium">{label}</p>
        <p className="text-muted-foreground">{used} of {total} used</p>
      </div>
    </div>
  );
}
