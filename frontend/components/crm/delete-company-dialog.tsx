"use client";

import { useState } from "react";

import { FieldEditor, type RelationSearch } from "@/components/crm/field-editor";
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
 * move them to the Trash with it (they come back together on a restore). A
 * company with deals asks the same of them, except that a deal cannot be
 * left without a company: it moves to another one, or to the Trash (Q-54).
 */

export type DealsChoice = "delete" | { move_to: string };
export function DeleteCompanyDialog({
  open,
  onOpenChange,
  name,
  peopleCount,
  dealsCount = null,
  searchCompanies,
  onDelete,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  name: string;
  /** null for a person or a deal, which have nothing to ask about. */
  peopleCount: number | null;
  dealsCount?: number | null;
  /** Where the deals may move: other companies of the workspace. */
  searchCompanies?: RelationSearch;
  onDelete: (people?: "unlink" | "delete", deals?: DealsChoice) => Promise<void>;
}) {
  const [people, setPeople] = useState<"unlink" | "delete">("unlink");
  const [deals, setDeals] = useState<"move" | "delete">("move");
  const [moveTo, setMoveTo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const asks = peopleCount !== null && peopleCount > 0;
  const asksDeals = dealsCount !== null && dealsCount > 0;

  async function confirm() {
    if (asksDeals && deals === "move" && !moveTo) {
      setError("Choose the company the deals move to.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onDelete(
        asks ? people : undefined,
        asksDeals ? (deals === "delete" ? "delete" : { move_to: moveTo as string }) : undefined
      );
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
        {asksDeals && (
          <fieldset className="space-y-2 text-sm">
            <legend className="mb-1 font-medium">
              {name} has {dealsCount} {dealsCount === 1 ? "deal" : "deals"}.
            </legend>
            <label className="flex items-start gap-2">
              <input
                type="radio"
                name="deals"
                className="mt-0.5"
                checked={deals === "move"}
                onChange={() => setDeals("move")}
              />
              Move them to another company
            </label>
            {deals === "move" && searchCompanies && (
              <div className="pl-6">
                <FieldEditor
                  id="deals-move-to"
                  spec={{
                    name: "move_to",
                    label: "Company",
                    type: "relation",
                    choices: [],
                    search: searchCompanies,
                  }}
                  value={moveTo}
                  onChange={(value) => setMoveTo((value as string | null) ?? null)}
                />
              </div>
            )}
            <label className="flex items-start gap-2">
              <input
                type="radio"
                name="deals"
                className="mt-0.5"
                checked={deals === "delete"}
                onChange={() => setDeals("delete")}
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
