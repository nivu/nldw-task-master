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
import { mergeCrmCompanies, previewCrmMerge } from "@/lib/api/crm";
import type { MergePreview } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";

/**
 * Merge two companies — spec 007 FR-CRM-DUP-03, -04.
 *
 * The person picks the other company and which of the two survives; then,
 * for each field the two hold differently, whose value to keep (the
 * preview's default keeps the survivor's, or the only filled one). Every
 * person, deal, task, note, Drive link and, in Labs, TVA project link moves
 * to the survivor, which keeps every domain of both. The other company goes
 * to the Trash and its links open the survivor from then on.
 */

const MOVES: [keyof MergePreview["moves"], string, string][] = [
  ["people", "person", "people"],
  ["deals", "deal", "deals"],
  ["tasks", "task", "tasks"],
  ["notes", "note", "notes"],
  ["links", "Drive link", "Drive links"],
  ["projects", "TVA project link", "TVA project links"],
  ["campaign_members", "campaign membership", "campaign memberships"],
];

function shown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

export function MergeDialog({
  open,
  onOpenChange,
  workspace,
  company,
  searchCompanies,
  onMerged,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspace: string;
  /** The company whose page the merge started on. */
  company: { id: string; name: string };
  /** Other companies of the workspace. */
  searchCompanies: RelationSearch;
  onMerged: (survivorId: string) => void;
}) {
  const [other, setOther] = useState<string | null>(null);
  const [keepThis, setKeepThis] = useState(true);
  const [preview, setPreview] = useState<MergePreview | null>(null);
  const [choices, setChoices] = useState<Record<string, "a" | "b">>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setOther(null);
    setKeepThis(true);
    setPreview(null);
    setChoices({});
    setError(null);
  }

  async function review() {
    if (!other) {
      setError("Choose the company to merge with.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const [a, b] = keepThis ? [company.id, other] : [other, company.id];
      const found = await previewCrmMerge(workspace, a, b);
      setPreview(found);
      setChoices(Object.fromEntries(found.differing_fields.map((f) => [f.field, f.default])));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      const survivor = await mergeCrmCompanies(workspace, {
        survivor_id: preview.a.id,
        merged_id: preview.b.id,
        choices,
        expect: {
          survivor_updated_at: preview.a.updated_at,
          merged_updated_at: preview.b.updated_at,
        },
      });
      onOpenChange(false);
      reset();
      onMerged(survivor.id);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const moving = preview ? MOVES.filter(([key]) => preview.moves[key] > 0) : [];

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{preview ? `Merge into ${preview.a.name}` : `Merge ${company.name}`}</DialogTitle>
          <DialogDescription>
            {preview
              ? `${preview.b.name} goes to the Trash, and everything on it moves to ${preview.a.name}.`
              : "Pick the duplicate, then choose what the merged company keeps."}
          </DialogDescription>
        </DialogHeader>

        {!preview ? (
          <div className="space-y-3 text-sm">
            <FieldEditor
              id="merge-other"
              spec={{ name: "other", label: "Merge with", type: "relation", choices: [], search: searchCompanies }}
              value={other}
              onChange={(value) => setOther((value as string | null) ?? null)}
            />
            <fieldset className="space-y-2">
              <legend className="mb-1 font-medium">Which one stays?</legend>
              <label className="flex items-start gap-2">
                <input
                  type="radio"
                  name="survivor"
                  className="mt-0.5"
                  checked={keepThis}
                  onChange={() => setKeepThis(true)}
                />
                Keep {company.name}
              </label>
              <label className="flex items-start gap-2">
                <input
                  type="radio"
                  name="survivor"
                  className="mt-0.5"
                  checked={!keepThis}
                  onChange={() => setKeepThis(false)}
                />
                Keep the other company
              </label>
            </fieldset>
          </div>
        ) : (
          <div className="space-y-4 text-sm">
            {preview.differing_fields.length === 0 ? (
              <p className="text-muted-foreground">The two companies hold the same values.</p>
            ) : (
              <table className="w-full text-left">
                <thead className="text-xs text-muted-foreground">
                  <tr>
                    <th className="pb-1 font-medium">Field</th>
                    <th className="pb-1 font-medium">{preview.a.name}</th>
                    <th className="pb-1 font-medium">{preview.b.name}</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.differing_fields.map((f) => (
                    <tr key={f.field} className="border-t align-top">
                      <td className="py-1.5 pr-2 text-muted-foreground">{f.label}</td>
                      {(["a", "b"] as const).map((side) => (
                        <td key={side} className="py-1.5 pr-2">
                          <label className="flex items-start gap-2">
                            <input
                              type="radio"
                              name={`merge-${f.field}`}
                              aria-label={`${f.label}: ${shown(side === "a" ? f.a_display : f.b_display)}`}
                              className="mt-0.5"
                              checked={choices[f.field] === side}
                              onChange={() => setChoices((c) => ({ ...c, [f.field]: side }))}
                            />
                            <span className="break-words">{shown(side === "a" ? f.a_display : f.b_display)}</span>
                          </label>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="text-muted-foreground">
              {moving.length === 0
                ? `Nothing else is attached to ${preview.b.name}.`
                : `Moves to ${preview.a.name}: ${moving
                    .map(([key, one, many]) => `${preview.moves[key]} ${preview.moves[key] === 1 ? one : many}`)
                    .join(", ")}. Every domain of both is kept.`}
            </p>
          </div>
        )}

        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <DialogFooter>
          {preview ? (
            <>
              <Button variant="outline" onClick={() => setPreview(null)} disabled={busy}>
                Back
              </Button>
              <Button variant="destructive" onClick={confirm} disabled={busy}>
                {busy ? "Merging…" : "Merge"}
              </Button>
            </>
          ) : (
            <>
              <Button
                variant="outline"
                onClick={() => {
                  reset();
                  onOpenChange(false);
                }}
              >
                Cancel
              </Button>
              <Button onClick={review} disabled={busy || !other}>
                {busy ? "Loading…" : "Review"}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
