"use client";

import { useState } from "react";

import { AiMarker } from "@/components/crm/ai-marker";
import { Button } from "@/components/ui/button";
import { recomputeCrmAiFields } from "@/lib/api/crm";
import type { CrmAiFieldValue } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import type { CrmTimezone } from "@/lib/crm/format";

/**
 * A company's or deal's AI fields — spec 009 FR-CRMAI-FIELD-04 to -06, P-2;
 * plan §5.12.
 *
 * Read-only values with the AI marker, shown on the record page only (never
 * as a view column or filter, Q-28); a number is shown here and nowhere else
 * (Q-27). Viewers see them too (FR-CRMAI-FIELD-06). An Admin or Member asks
 * for a recompute; inside the hour it is held for one trailing recompute at
 * the end of it (Q-38). Every generated string is a text node.
 */
export function AiFieldsPanel({
  workspace,
  object,
  recordId,
  fields,
  canEdit,
  timeZone,
}: {
  workspace: string;
  object: "companies" | "opportunities";
  recordId: string;
  fields: CrmAiFieldValue[];
  /** Admins and Members recompute (FR-CRMAI-FIELD-04). */
  canEdit: boolean;
  timeZone: CrmTimezone;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function recompute() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await recomputeCrmAiFields(workspace, object, recordId);
      setMessage(
        result.queued
          ? "Queued: the AI fields will be recomputed in the background."
          : result.next_allowed_at
            ? `Recomputed at most once an hour; queued for ${new Intl.DateTimeFormat("en-GB", {
                hour: "2-digit",
                minute: "2-digit",
                timeZone,
              }).format(new Date(result.next_allowed_at))}.`
            : "Recomputed at most once an hour; queued for the end of it."
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const rows = fields.filter((field) => field.value !== null && field.value !== "");

  return (
    <section aria-label="AI fields" className="space-y-2 rounded-md border p-3 text-sm" data-testid="ai-fields-panel">
      <div className="flex items-center gap-1.5">
        <h2 className="flex-1 text-sm font-medium">AI fields</h2>
        {canEdit && (
          <Button size="xs" variant="outline" disabled={busy} onClick={recompute}>
            {busy ? "Asking…" : "Recompute"}
          </Button>
        )}
      </div>
      {error && (
        <p role="alert" className="rounded-md bg-destructive/10 p-2 text-xs text-destructive">
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="text-xs text-muted-foreground">
          {message}
        </p>
      )}
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">Not computed yet.</p>
      ) : (
        <dl className="grid grid-cols-[minmax(0,8rem)_minmax(0,1fr)] gap-x-3 gap-y-1.5">
          {rows.map((field) => (
            <div key={field.key} className="contents">
              <dt className="text-xs text-muted-foreground">{field.name}</dt>
              <dd className="flex min-w-0 flex-wrap items-start gap-1.5">
                <span className="break-words whitespace-pre-line">
                  {field.output_type === "number" ? `${field.value} / 100` : String(field.value)}
                </span>
                {field.ai && <AiMarker ai={field.ai} timeZone={timeZone} />}
                {field.reason && (
                  <span className="w-full break-words text-xs text-muted-foreground">{field.reason}</span>
                )}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}
