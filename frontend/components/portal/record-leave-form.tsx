"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { Category } from "@/lib/api/types";
import { CATEGORY_LABEL } from "@/lib/api/types";

const CATEGORIES: Category[] = ["wfh", "casual", "sick", "compoff"];

export interface RecordLeaveInput {
  category: Category;
  duration: string;
  reason: string | null;
  note: string;
}

/**
 * The inline form behind "Record leave taken" and "Convert to leave" on the
 * Team page — spec 001 FR-BACK-08/10. The person and the date come from the
 * row it opens on, so only what was taken is asked for here.
 */
export function RecordLeaveForm({
  submitLabel,
  onSubmit,
  onCancel,
}: {
  submitLabel: string;
  onSubmit: (input: RecordLeaveInput) => Promise<void>;
  onCancel: () => void;
}) {
  const [form, setForm] = useState({
    category: "casual" as Category,
    duration: "1.0",
    reason: "",
    note: "Recorded after the fact",
  });
  const [busy, setBusy] = useState(false);

  // Q-07 — casual and sick need the person's reason.
  const reasonRequired = form.category === "casual" || form.category === "sick";
  const ready = form.note.trim() && (!reasonRequired || form.reason.trim()) && !busy;

  return (
    <form
      className="grid w-full gap-3 rounded-md border p-3 sm:grid-cols-2"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        try {
          await onSubmit({
            category: form.category,
            duration: form.duration,
            reason: form.reason.trim() || null,
            note: form.note.trim(),
          });
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="space-y-1.5">
        <Label htmlFor="rl-category">What</Label>
        <select
          id="rl-category"
          className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
          value={form.category}
          onChange={(e) => setForm({ ...form, category: e.target.value as Category })}
        >
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {CATEGORY_LABEL[c]}
            </option>
          ))}
        </select>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="rl-duration">How long</Label>
        <select
          id="rl-duration"
          className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
          value={form.duration}
          onChange={(e) => setForm({ ...form, duration: e.target.value })}
        >
          <option value="1.0">Full day</option>
          <option value="0.5">Half day</option>
        </select>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="rl-reason">
          Their reason{" "}
          <span className="font-normal text-muted-foreground">
            {reasonRequired ? "(required)" : "(optional)"}
          </span>
        </Label>
        <Input
          id="rl-reason"
          value={form.reason}
          onChange={(e) => setForm({ ...form, reason: e.target.value })}
          maxLength={500}
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="rl-note">Why you are entering it</Label>
        <Input
          id="rl-note"
          value={form.note}
          onChange={(e) => setForm({ ...form, note: e.target.value })}
          maxLength={500}
          required
        />
      </div>

      <div className="flex gap-2 sm:col-span-2">
        <Button type="submit" size="sm" disabled={!ready}>
          {busy ? "Saving…" : submitLabel}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
