"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorMessage } from "@/lib/api/portal";
import type { TaskOut, TaskTemplate } from "@/lib/api/task-types";
import { addTaskTemplate, getTaskSettings, updateTaskSettings } from "@/lib/api/tasks";

/**
 * The person's own task settings — spec 008 FR-TASK-DEL-06; contracts §2
 * `GET/PATCH /tasks/settings`.
 *
 * How many days a waiting task may sit untouched before the morning brief
 * asks "nudge?": 1 to 30, 5 unless the person changes it. Shown only when
 * phase 2 is switched on for them.
 *
 * From phase 3 the backend also lists the starter templates (FR-TASK-RRULE-05):
 * each adds one recurring task to the person's My Tasks, only when they
 * choose it. The statutory dates stay unavailable until the owner supplies
 * the list (spec §15.1).
 */
export function TaskSettings({ onAdded }: { onAdded?: (task: TaskOut) => void } = {}) {
  const [saved, setSaved] = useState<number | null>(null);
  const [days, setDays] = useState("");
  const [templates, setTemplates] = useState<TaskTemplate[]>([]);
  const [added, setAdded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getTaskSettings()
      .then((out) => {
        if (cancelled) return;
        setSaved(out.nudge_days);
        setDays(String(out.nudge_days));
        setTemplates(out.templates ?? []);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const wanted = Number(days);
  const valid = Number.isInteger(wanted) && wanted >= 1 && wanted <= 30;

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const out = await updateTaskSettings({ nudge_days: wanted });
      setSaved(out.nudge_days);
      setDays(String(out.nudge_days));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function addTemplate(template: TaskTemplate) {
    setSaving(true);
    setError(null);
    setAdded(null);
    try {
      const task = await addTaskTemplate(template.key);
      setAdded(`Added "${task.title}".`);
      onAdded?.(task);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  if (saved === null && !error) return null;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <Label htmlFor="task-nudge-days" className="text-xs text-muted-foreground">
            Suggest a nudge when a waiting task is untouched for (days, 1 to 30)
          </Label>
          <Input
            id="task-nudge-days"
            type="number"
            inputMode="numeric"
            min={1}
            max={30}
            className="h-8 w-24"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
        </div>
        <Button size="sm" disabled={!valid || saving || wanted === saved} onClick={save}>
          Save
        </Button>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </div>
      {templates.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs text-muted-foreground">
            Starter templates: each adds one recurring task to your list.
          </p>
          <ul className="space-y-1">
            {templates.map((t) => (
              <li key={t.key} className="flex flex-wrap items-center gap-2 text-sm">
                <span>{t.title}</span>
                <span className="text-xs text-muted-foreground">
                  {t.available ? t.repeats : "Waiting for the owner's list of dates"}
                </span>
                <Button
                  size="xs"
                  variant="outline"
                  disabled={!t.available || saving}
                  onClick={() => addTemplate(t)}
                >
                  Add
                </Button>
              </li>
            ))}
          </ul>
          {added && (
            <p role="status" className="text-xs text-muted-foreground">
              {added}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
