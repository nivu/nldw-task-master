"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorMessage } from "@/lib/api/portal";
import { getTaskSettings, updateTaskSettings } from "@/lib/api/tasks";

/**
 * The person's own task settings — spec 008 FR-TASK-DEL-06; contracts §2
 * `GET/PATCH /tasks/settings`.
 *
 * How many days a waiting task may sit untouched before the morning brief
 * asks "nudge?": 1 to 30, 5 unless the person changes it. Shown only when
 * phase 2 is switched on for them.
 */
export function TaskSettings() {
  const [saved, setSaved] = useState<number | null>(null);
  const [days, setDays] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getTaskSettings()
      .then((out) => {
        if (cancelled) return;
        setSaved(out.nudge_days);
        setDays(String(out.nudge_days));
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

  if (saved === null && !error) return null;
  return (
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
  );
}
