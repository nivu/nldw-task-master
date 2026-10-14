"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { errorMessage } from "@/lib/api/portal";

/**
 * Delete a record — spec 007 FR-CRM-DEL-01, -02, -04.
 *
 * Deleting moves the record to the Trash, where an admin can restore it for
 * 90 days. A company with people asks what happens to them: unlink them, or
 * move them to the Trash with it (they come back together on a restore).
 */
export function DeleteCompanyDialog({
  open,
  onOpenChange,
  name,
  peopleCount,
  onDelete,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  name: string;
  /** null for a person, who has nothing to ask about. */
  peopleCount: number | null;
  onDelete: (people?: "unlink" | "delete") => Promise<void>;
}) {
  const [people, setPeople] = useState<"unlink" | "delete">("unlink");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const asks = peopleCount !== null && peopleCount > 0;

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await onDelete(asks ? people : undefined);
      onOpenChange(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete {name}?</DialogTitle>
          <DialogDescription>
            It goes to the Trash. A workspace admin can restore it for 90 days.
          </DialogDescription>
        </DialogHeader>
        {asks && (
          <fieldset className="space-y-2 text-sm">
            <legend className="mb-1 font-medium">
              {name} has {peopleCount} {peopleCount === 1 ? "person" : "people"}.
            </legend>
            <label className="flex items-start gap-2">
              <input
                type="radio"
                name="people"
                className="mt-0.5"
                checked={people === "unlink"}
                onChange={() => setPeople("unlink")}
              />
              Keep them, without a company
            </label>
            <label className="flex items-start gap-2">
              <input
                type="radio"
                name="people"
                className="mt-0.5"
                checked={people === "delete"}
                onChange={() => setPeople("delete")}
              />
              Move them to the Trash too
            </label>
          </fieldset>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={confirm} disabled={busy}>
            {busy ? "Deleting…" : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
