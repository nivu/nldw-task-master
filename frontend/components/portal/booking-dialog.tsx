"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { createBooking, errorMessage, withdrawBooking } from "@/lib/api/portal";
import type { Balance, Category, DayCell } from "@/lib/api/types";
import { CATEGORY_LABEL } from "@/lib/api/types";

/**
 * Book a day.
 *
 * NFR-02 caps this at three interactions from the calendar: pick the day
 * (already done — that is what opened this), pick category and duration,
 * confirm. So category and duration are one tap each on a visible row rather
 * than a dropdown that costs an extra tap to open.
 *
 * FR-BOOK-12: the remaining allowance for the selected category is shown
 * before confirming, and updates as the choice changes. §1.1 is explicit that
 * marking leave must "require no arithmetic from the person doing it".
 */

const CATEGORIES: Category[] = ["wfh", "casual", "sick"];

export function BookingDialog({
  day,
  balances,
  onClose,
  onSaved,
}: {
  day: DayCell | null;
  balances: Balance[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const existing = day?.booking ?? null;
  const [category, setCategory] = useState<Category>(existing?.category ?? "wfh");
  const [duration, setDuration] = useState<string>(existing?.duration ?? "1.0");
  const [reason, setReason] = useState(existing?.reason ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!day) return null;

  const remaining = balances.find((b) => b.category === category)?.remaining ?? "0";
  // Q-07 — a reason is required for casual and sick, optional for WFH. The
  // server enforces this; the form only mirrors it so the button state is
  // honest before anyone presses it.
  const reasonRequired = category !== "wfh";
  const canSubmit = !busy && (!reasonRequired || reason.trim().length > 0);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await createBooking({
        date: day!.date,
        category,
        duration,
        reason: reason.trim() || null,
      });
      onSaved();
      onClose();
    } catch (err) {
      // A-17 — the backend writes these for the person reading them
      // ("Casual leave must be requested before the day itself"), so it is
      // shown as-is rather than replaced with something vaguer.
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!existing) return;
    setBusy(true);
    setError(null);
    try {
      await withdrawBooking(existing.id);
      onSaved();
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const pretty = new Date(`${day.date}T00:00:00`).toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });

  return (
    <Dialog open={Boolean(day)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{pretty}</DialogTitle>
          <DialogDescription>
            {existing ? "Change or clear this day." : "Mark this day."}
          </DialogDescription>
        </DialogHeader>

        {error && (
          <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <div className="space-y-4">
          <div className="space-y-2">
            <Label>What</Label>
            <div className="grid grid-cols-3 gap-2">
              {CATEGORIES.map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setCategory(value)}
                  aria-pressed={category === value}
                  className={cn(
                    "rounded-md border px-2 py-2 text-xs font-medium transition-colors",
                    category === value
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border hover:bg-muted"
                  )}
                >
                  {CATEGORY_LABEL[value]}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <Label>How long</Label>
            <div className="grid grid-cols-2 gap-2">
              {[
                { value: "1.0", label: "Full day" },
                { value: "0.5", label: "Half day" },
              ].map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setDuration(option.value)}
                  aria-pressed={duration === option.value}
                  className={cn(
                    "rounded-md border px-2 py-2 text-sm font-medium transition-colors",
                    duration === option.value
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border hover:bg-muted"
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          {/* FR-BOOK-12 */}
          <p className="rounded-md bg-muted px-3 py-2 text-sm">
            <span className="text-muted-foreground">You have </span>
            <span className="font-medium">{remaining}</span>
            <span className="text-muted-foreground">
              {" "}
              {remaining === "1.0" ? "day" : "days"} of{" "}
              {CATEGORY_LABEL[category].toLowerCase()} left this month.
            </span>
          </p>

          <div className="space-y-2">
            <Label htmlFor="reason">
              Reason{" "}
              <span className="font-normal text-muted-foreground">
                {reasonRequired ? "(required)" : "(optional)"}
              </span>
            </Label>
            <Textarea
              id="reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={
                category === "sick" ? "Fever" : category === "casual" ? "Dentist appointment" : ""
              }
              rows={2}
              maxLength={500}
            />
          </div>
        </div>

        <div className="flex gap-2 pt-2">
          {existing && (
            <Button variant="outline" onClick={remove} disabled={busy} className="flex-1">
              Clear day
            </Button>
          )}
          <Button onClick={save} disabled={!canSubmit} className="flex-1">
            {busy ? "Saving…" : existing ? "Update" : "Confirm"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
