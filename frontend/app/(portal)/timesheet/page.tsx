"use client";

import { useState } from "react";
import { ChevronLeft, ChevronRight, Plus, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorMessage, getTimesheetDay, saveTimesheetDay } from "@/lib/api/portal";
import type { TimesheetDay } from "@/lib/api/types";
import { useAsync } from "@/lib/use-async";
import { cn } from "@/lib/utils";

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
 */

interface Line {
  key: string;
  project_id: string;
  hours_office: string;
  hours_home: string;
  note: string;
}

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
  return <DayForm key={data.date} data={data} onPick={setDay} onSaved={reload} />;
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
      hours_office: entry.hours_office,
      hours_home: entry.hours_home,
      note: entry.note ?? "",
    }))
  );
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const used = new Set(lines.map((l) => l.project_id));
  const available = data.projects.filter((p) => !used.has(p.id));
  const total = lines.reduce(
    (sum, l) => sum + (Number(l.hours_office) || 0) + (Number(l.hours_home) || 0),
    0
  );
  const overLimit = total > Number(data.max_hours);

  function shiftDay(delta: number) {
    const next = new Date(`${data.date}T00:00:00`);
    next.setDate(next.getDate() + delta);
    onPick(next.toISOString().slice(0, 10));
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
          const project = data.projects.find((p) => p.id === line.project_id);
          return (
            <Card key={line.key}>
              <CardContent className="space-y-3 p-3">
                <div className="flex items-center gap-2">
                  <span className="flex-1 text-sm font-medium">{project?.name ?? "—"}</span>
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

                <Input
                  value={line.note}
                  disabled={!data.can_log}
                  onChange={(e) => update(line.key, { note: e.target.value })}
                  placeholder="What did you work on?"
                  maxLength={500}
                />
              </CardContent>
            </Card>
          );
        })}

        {data.can_log && available.length > 0 && (
          <div className="flex flex-wrap gap-2">
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

        {data.projects.length === 0 && (
          <Card>
            <CardContent className="p-4 text-sm text-muted-foreground">
              You are not allocated to any project for this date. Ask an admin to
              allocate you, and it will appear here.
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
