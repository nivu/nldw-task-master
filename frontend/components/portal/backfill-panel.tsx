"use client";

import { useState } from "react";
import { AlertTriangle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { backfillLeave, errorMessage, undoBackfill } from "@/lib/api/portal";
import type { BackfillEntry, Category, PortalUser } from "@/lib/api/types";
import { CATEGORY_LABEL } from "@/lib/api/types";

const CATEGORIES: Category[] = ["wfh", "casual", "sick"];

/**
 * Spec A-21 — recording leave that was already taken.
 *
 * This screen is deliberately not styled like the rest of the admin panel. It
 * is the one place in the product where the rule in §6.3 — that a booking is
 * locked once its date has passed — can be set aside, and someone using it
 * should be in no doubt about that. The warning is not decoration; it is the
 * distinct labelling that made this capability acceptable to add at all.
 *
 * Everything entered here is permanently marked as admin-entered, appears on
 * the person's own calendar as such, and is written to the append-only audit
 * log along with the note explaining why.
 */
export function BackfillPanel({
  people,
  entries,
  onDone,
  onError,
}: {
  people: PortalUser[];
  entries: BackfillEntry[];
  onDone: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [form, setForm] = useState({
    user_id: "",
    date: "",
    category: "casual" as Category,
    duration: "1.0",
    reason: "",
    note: "",
  });
  const [busy, setBusy] = useState(false);

  const reasonRequired = form.category !== "wfh";
  const ready =
    form.user_id &&
    form.date &&
    form.note.trim() &&
    (!reasonRequired || form.reason.trim()) &&
    !busy;

  return (
    <div className="space-y-4">
      <div className="flex gap-3 rounded-md border border-amber-300 bg-amber-50 p-4 text-sm dark:border-amber-800 dark:bg-amber-950/40">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-700 dark:text-amber-400" />
        <div className="space-y-1">
          <p className="font-medium text-amber-900 dark:text-amber-200">
            This is the one place a locked day can be changed.
          </p>
          <p className="text-amber-800 dark:text-amber-300">
            Normally a booking cannot be created or edited once its date has
            passed — that is what stops someone taking a day and later erasing
            the record. Use this only to enter leave people genuinely took
            before they were using the portal. Every entry is marked as
            admin-entered, shows up that way on their own calendar, and is
            recorded in the audit log with your note.
          </p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Record leave already taken</CardTitle>
          <CardDescription>
            Past dates only. Allowances are not enforced here — this records what
            happened, so a balance may go below zero, and that is reported rather
            than hidden.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="grid gap-3 sm:grid-cols-2"
            onSubmit={async (event) => {
              event.preventDefault();
              setBusy(true);
              try {
                await backfillLeave({
                  user_id: form.user_id,
                  date: form.date,
                  category: form.category,
                  duration: form.duration,
                  reason: form.reason.trim() || null,
                  note: form.note.trim(),
                });
                const who = people.find((p) => p.id === form.user_id)?.display_name ?? "";
                onDone(`Recorded ${CATEGORY_LABEL[form.category].toLowerCase()} for ${who} on ${form.date}.`);
                setForm({ ...form, date: "", reason: "" });
              } catch (err) {
                onError(errorMessage(err));
              } finally {
                setBusy(false);
              }
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="bf-person">Person</Label>
              <select
                id="bf-person"
                className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
                value={form.user_id}
                onChange={(e) => setForm({ ...form, user_id: e.target.value })}
                required
              >
                <option value="">Choose…</option>
                {people
                  .filter((p) => p.is_active)
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.display_name}
                    </option>
                  ))}
              </select>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="bf-date">Date</Label>
              <Input
                id="bf-date"
                type="date"
                value={form.date}
                onChange={(e) => setForm({ ...form, date: e.target.value })}
                required
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="bf-category">What</Label>
              <select
                id="bf-category"
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
              <Label htmlFor="bf-duration">How long</Label>
              <select
                id="bf-duration"
                className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
                value={form.duration}
                onChange={(e) => setForm({ ...form, duration: e.target.value })}
              >
                <option value="1.0">Full day</option>
                <option value="0.5">Half day</option>
              </select>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="bf-reason">
                Their reason{" "}
                <span className="font-normal text-muted-foreground">
                  {reasonRequired ? "(required)" : "(optional)"}
                </span>
              </Label>
              <Input
                id="bf-reason"
                value={form.reason}
                onChange={(e) => setForm({ ...form, reason: e.target.value })}
                maxLength={500}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="bf-note">Why you are entering it</Label>
              <Textarea
                id="bf-note"
                value={form.note}
                onChange={(e) => setForm({ ...form, note: e.target.value })}
                placeholder="Recorded at go-live from the email thread"
                rows={2}
                maxLength={500}
                required
              />
            </div>

            <div className="sm:col-span-2">
              <Button type="submit" disabled={!ready}>
                {busy ? "Recording…" : "Record it"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Everything entered by hand</CardTitle>
          <CardDescription>
            {/* A go-live produces a burst of these. Reviewing them as a set is
                what makes a mistyped entry findable at all. */}
            A go-live produces a lot of these at once. Check them here rather
            than hunting one date at a time.
          </CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto p-0">
          {entries.length === 0 ? (
            <p className="p-4 text-sm text-muted-foreground">Nothing has been entered by hand.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="p-3 font-medium">Date</th>
                  <th className="p-3 font-medium">Person</th>
                  <th className="p-3 font-medium">What</th>
                  <th className="p-3 font-medium">Note</th>
                  <th className="p-3" />
                </tr>
              </thead>
              <tbody className="divide-y">
                {entries.map((entry) => (
                  <tr key={entry.id} className={entry.status === "withdrawn" ? "opacity-50" : ""}>
                    <td className="p-3 tabular-nums">{entry.date}</td>
                    <td className="p-3">{entry.display_name}</td>
                    <td className="p-3">
                      {CATEGORY_LABEL[entry.category]}
                      {entry.duration === "0.5" && " (half)"}
                    </td>
                    <td className="max-w-xs p-3 text-xs text-muted-foreground">{entry.note}</td>
                    <td className="p-3 text-right">
                      {entry.status === "withdrawn" ? (
                        <Badge variant="outline">undone</Badge>
                      ) : (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={async () => {
                            try {
                              await undoBackfill(entry.id);
                              onDone(`Undone: ${entry.display_name}, ${entry.date}.`);
                            } catch (err) {
                              onError(errorMessage(err));
                            }
                          }}
                        >
                          Undo
                        </Button>
                      )}
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
