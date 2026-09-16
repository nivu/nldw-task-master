"use client";

import { useState } from "react";
import { ChevronLeft, ChevronRight, Plus, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage, getTimesheetDay, getTimesheetWeek, saveTimesheetDay } from "@/lib/api/portal";
import type { Activity, TimesheetDay, TimesheetWeek } from "@/lib/api/types";
import { useAsync } from "@/lib/use-async";
import { cn } from "@/lib/utils";
import { isoDate } from "@/lib/dates";

/**
 * Log a day — spec 002 §5.3.
 *
 * NFR-01/02: a full day in under thirty seconds, on a phone, at the point
 * somebody wants to stop working. The whole design follows from that. Projects
 * are pre-filled from current allocations rather than chosen from a list, hours
 * are stepped buttons rather than free text, and the day saves in one action
 * instead of one per line.
 *
 * If this feels like filling in a form, it will be filled in on Friday for the
 * whole week, from memory, and the numbers will be wrong (§1.1).
 *
 * Spec 003 adds two things. A line is for a project OR an activity — learning,
 * internal work, admin — never both (FR-ACT-01), so a day spent on a course is
 * a logged day rather than a gap. And the note is the only record of what was
 * done (Q-03: free text, not a task tracker), so it is labelled as such and
 * sits above the hours rather than as an afterthought below them (FR-TIME-11).
 */

interface Line {
  key: string;
  /** Exactly one of these is set. */
  project_id: string | null;
  activity: Activity | null;
  hours_office: string;
  hours_home: string;
  note: string;
}

const lineKey = (line: { project_id: string | null; activity: Activity | null }) =>
  line.project_id ?? `activity:${line.activity}`;

export default function TimesheetPage() {
  const [day, setDay] = useState<string>("");

  const { data, error, reload } = useAsync<TimesheetDay>(
    () => getTimesheetDay(day || undefined),
    [day]
  );

  if (error) {
    return (
      <div role="alert" className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">
        {error}
      </div>
    );
  }
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  // Keyed on the date so React remounts the form when the day changes, which
  // seeds its state from the freshly-loaded entries. The alternative — an
  // effect that copies props into state — causes the cascading render the
  // React compiler rejects, and silently carries edits across days.
  return (
    <div className="space-y-4">
      <WeekStrip day={data.date} today={data.today} onPick={setDay} version={data.total} />
      <DayForm key={data.date} data={data} onPick={setDay} onSaved={reload} />
    </div>
  );
}

/**
 * The week around the selected day, one bar per day, tap to jump. Context
 * for "did I log Tuesday?" without leaving the page. `version` changes when
 * the day is saved, so the strip refreshes.
 */
function WeekStrip({ day, today, onPick, version }: { day: string; today: string; onPick: (d: string) => void; version: string }) {
  const monday = (() => {
    const d = new Date(`${day}T00:00:00`);
    d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
    return isoDate(d);
  })();
  const { data } = useAsync<TimesheetWeek>(() => getTimesheetWeek(monday), [monday, version]);
  if (!data) return <div className="h-16" />;
  const max = Math.max(8, ...data.days.map((d) => Number(d.total)));
  return (
    <div className="flex items-end gap-1.5">
      {data.days.map((d) => {
        const h = Number(d.total);
        const selected = d.date === day;
        const future = d.date > today;
        return (
          <button
            key={d.date}
            type="button"
            disabled={future}
            onClick={() => onPick(d.date)}
            title={`${d.date}: ${h}h${d.holiday ? " · holiday" : ""}${d.on_leave ? ` · ${d.on_leave}` : ""}`}
            className={cn("flex flex-1 flex-col items-center gap-1 rounded-md p-1", selected && "bg-muted", future && "opacity-40")}
          >
            <div className="flex h-10 w-full items-end rounded bg-muted/60">
              <div
                className={cn("w-full rounded", d.holiday || d.on_leave ? "bg-muted-foreground/30" : d.locked ? "bg-sky-300 dark:bg-sky-800" : "bg-sky-500")}
                style={{ height: `${Math.max(h > 0 ? 8 : 0, (h / max) * 100)}%` }}
              />
            </div>
            <span className={cn("text-[11px] tabular-nums", d.is_today ? "font-semibold" : "text-muted-foreground")}>
              {new Date(`${d.date}T00:00:00`).toLocaleDateString("en-GB", { weekday: "short" }).slice(0, 2)} {d.date.slice(8)}
            </span>
          </button>
        );
      })}
      <div className="w-12 shrink-0 text-right text-xs text-muted-foreground">
        <span className="block text-sm font-semibold tabular-nums text-foreground">{data.total}</span>h
      </div>
    </div>
  );
}

function DayForm({
  data,
  onPick,
  onSaved,
}: {
  data: TimesheetDay;
  onPick: (day: string) => void;
  onSaved: () => void;
}) {
  const [lines, setLines] = useState<Line[]>(() =>
    data.entries.map((entry) => ({
      key: entry.id,
      project_id: entry.project_id,
      activity: entry.activity,
      hours_office: entry.hours_office,
      hours_home: entry.hours_home,
      note: entry.note ?? "",
    }))
  );
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const used = new Set(lines.map(lineKey));
  const available = data.projects.filter((p) => !used.has(p.id));
  const availableActivities = data.activities.filter((a) => !used.has(`activity:${a.id}`));
  const total = lines.reduce(
    (sum, l) => sum + (Number(l.hours_office) || 0) + (Number(l.hours_home) || 0),
    0
  );
  const overLimit = total > Number(data.max_hours);

  function shiftDay(delta: number) {
    const next = new Date(`${data.date}T00:00:00`);
    next.setDate(next.getDate() + delta);
    onPick(isoDate(next));
  }

  function update(key: string, patch: Partial<Line>) {
    setLines((current) => current.map((l) => (l.key === key ? { ...l, ...patch } : l)));
    setDirty(true);
  }

  async function save() {
    setSaving(true);
    setProblem(null);
    try {
      const result = await saveTimesheetDay({
        date: data.date,
        lines: lines
          .filter((l) => Number(l.hours_office) > 0 || Number(l.hours_home) > 0)
          .map((l) => ({
            project_id: l.project_id,
            activity: l.activity,
            hours_office: l.hours_office || "0",
            hours_home: l.hours_home || "0",
            note: l.note.trim() || null,
          })),
      });
      setSaved(`Saved — ${result.total} hours.`);
      setDirty(false);
      onSaved();
    } catch (err) {
      // A-17 — the backend writes these for the person reading them.
      setProblem(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  const pretty = new Date(`${data.date}T00:00:00`).toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-heading text-lg font-semibold">
            {data.date === data.today ? "Today" : pretty}
          </h1>
          <p className="text-sm text-muted-foreground">
            {data.date === data.today ? pretty : null}
          </p>
        </div>
        <div className="flex gap-1">
          <Button variant="outline" size="icon" onClick={() => shiftDay(-1)} aria-label="Previous day">
            <ChevronLeft className="size-4" />
          </Button>
          <Button
            variant="outline"
            size="icon"
            onClick={() => shiftDay(1)}
            disabled={data.date >= data.today}
            aria-label="Next day"
          >
            <ChevronRight className="size-4" />
          </Button>
        </div>
      </div>

      {/* Q-03 / FR-TIME-10 — a warning, never a refusal. People do work on a
          sick day, and refusing would make the effort vanish from the project. */}
      {data.leave_warning && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          {data.leave_warning}
        </div>
      )}

      {!data.can_log && data.refusal && (
        <div role="alert" className="rounded-md bg-muted p-3 text-sm">
          {data.refusal}
        </div>
      )}

      {problem && (
        <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {problem}
        </div>
      )}
      {saved && <div className="rounded-md bg-muted p-3 text-sm">{saved}</div>}

      <div className="space-y-3">
        {lines.map((line) => {
          const project = line.project_id
            ? data.projects.find((p) => p.id === line.project_id)
            : undefined;
          const activity = line.activity
            ? data.activities.find((a) => a.id === line.activity)
            : undefined;
          return (
            <Card key={line.key}>
              <CardContent className="space-y-3 p-3">
                <div className="flex items-center gap-2">
                  <span className="flex-1 text-sm font-medium">
                    {project?.name ?? activity?.name ?? "—"}
                  </span>
                  {activity && (
                    <Badge variant="secondary" title="Not for a client project">
                      activity
                    </Badge>
                  )}
                  {/* Q-07 — allowed, and named rather than hidden. */}
                  {project && !project.allocated && (
                    <Badge variant="outline" title="You are not allocated to this project">
                      not allocated
                    </Badge>
                  )}
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Remove line"
                    disabled={!data.can_log}
                    onClick={() => {
                      setLines((c) => c.filter((l) => l.key !== line.key));
                      setDirty(true);
                    }}
                  >
                    <X className="size-4" />
                  </Button>
                </div>

                {/* FR-TIME-11 — the only task record there is, so it comes
                    first and says what it is for. */}
                <div className="space-y-1.5">
                  <Label htmlFor={`note-${line.key}`} className="text-xs">
                    What did you do?
                  </Label>
                  <Textarea
                    id={`note-${line.key}`}
                    value={line.note}
                    disabled={!data.can_log}
                    onChange={(e) => update(line.key, { note: e.target.value })}
                    placeholder={
                      activity
                        ? "e.g. Finished module 3 of the Rust course"
                        : "e.g. Built the export API, reviewed the data model"
                    }
                    maxLength={500}
                    rows={2}
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <HoursField
                    label="Office"
                    value={line.hours_office}
                    disabled={!data.can_log}
                    onChange={(v) => update(line.key, { hours_office: v })}
                  />
                  <HoursField
                    label="Home"
                    value={line.hours_home}
                    disabled={!data.can_log}
                    onChange={(v) => update(line.key, { hours_home: v })}
                  />
                </div>
              </CardContent>
            </Card>
          );
        })}

        {data.can_log && (available.length > 0 || availableActivities.length > 0) && (
          <div className="space-y-2">
            {available.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-muted-foreground">Projects</span>
                {available.map((project) => (
                  <Button
                    key={project.id}
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setLines((c) => [
                        ...c,
                        {
                          key: `new-${project.id}`,
                          project_id: project.id,
                          activity: null,
                          hours_office: "0",
                          hours_home: "0",
                          note: "",
                        },
                      ]);
                      setDirty(true);
                    }}
                  >
                    <Plus className="size-3.5" />
                    {project.name}
                  </Button>
                ))}
              </div>
            )}
            {/* FR-TIME-12 — always offered, so a day with no project work is
                still a logged day rather than a gap in coverage. */}
            {availableActivities.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-muted-foreground">Not for a project</span>
                {availableActivities.map((activity) => (
                  <Button
                    key={activity.id}
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setLines((c) => [
                        ...c,
                        {
                          key: `new-activity-${activity.id}`,
                          project_id: null,
                          activity: activity.id,
                          hours_office: "0",
                          hours_home: "0",
                          note: "",
                        },
                      ]);
                      setDirty(true);
                    }}
                  >
                    <Plus className="size-3.5" />
                    {activity.name}
                  </Button>
                ))}
              </div>
            )}
          </div>
        )}

        {data.projects.length === 0 && (
          <Card>
            <CardContent className="p-4 text-sm text-muted-foreground">
              You are not allocated to any project for this date. A manager can
              allocate you, and it will appear here. Learning, internal and admin
              time can still be logged above.
            </CardContent>
          </Card>
        )}
      </div>

      <div className="flex items-center gap-3 border-t pt-4">
        <div className="flex-1">
          <p className={cn("text-2xl font-semibold tabular-nums", overLimit && "text-destructive")}>
            {total}
            <span className="ml-1 text-sm font-normal text-muted-foreground">hours</span>
          </p>
          <p className="text-xs text-muted-foreground">
            {data.locked
              ? `Locked since ${data.locks_on}`
              : `Editable until ${data.locks_on}`}
          </p>
        </div>
        <Button onClick={save} disabled={!data.can_log || !dirty || saving || overLimit}>
          {saving ? "Saving…" : "Save day"}
        </Button>
      </div>
    </div>
  );
}

/**
 * Hours as stepped buttons plus a number field.
 *
 * The buttons carry the common cases so a normal day is two taps, and the
 * field stays for the rest. Quarter-hour steps match what the server accepts,
 * so the UI cannot offer a value the API will reject.
 */
function HoursField({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const current = Number(value) || 0;
  const step = (delta: number) => onChange(String(Math.max(0, current + delta)));

  return (
    <div className="space-y-1.5">
      <Label className="text-xs">{label}</Label>
      <div className="flex items-center gap-1">
        <Button
          variant="outline"
          size="icon"
          className="size-8 shrink-0"
          disabled={disabled || current <= 0}
          onClick={() => step(-0.5)}
          aria-label={`${label} minus half an hour`}
        >
          −
        </Button>
        <Input
          type="number"
          inputMode="decimal"
          step="0.25"
          min="0"
          max="24"
          className="text-center tabular-nums"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
        <Button
          variant="outline"
          size="icon"
          className="size-8 shrink-0"
          disabled={disabled}
          onClick={() => step(0.5)}
          aria-label={`${label} plus half an hour`}
        >
          +
        </Button>
      </div>
    </div>
  );
}
