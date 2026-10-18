"use client";

import Link from "next/link";
import { useState } from "react";

import { AiMarker } from "@/components/crm/ai-marker";
import { Button } from "@/components/ui/button";
import type { CrmEnrichment, CrmEnrichmentValues, CrmLists, EnrichmentField } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import type { CrmTimezone } from "@/lib/crm/format";

/**
 * A company's enriched values — spec 009 FR-CRMAI-ENR-03 to -06, P-2; plan §5.7.
 *
 * What was read from the company's own website, kept apart from the fields a
 * person entered, with its status and the AI marker. An Admin or Member
 * promotes one value to its main field with one click: an ordinary edit
 * (PATCH /crm/companies/{id}), audited as theirs. A value that differs from
 * a filled field has no Promote; it waits in the Review Queue instead. An
 * industry with no matching option says so and offers nothing until an
 * Admin adds the option. Every generated string is a text node.
 */

type ValueKey = Exclude<keyof CrmEnrichmentValues, "products">;

/** Spec 009 §7.2, in order; `field` is what the value promotes to. */
const ROWS: { key: ValueKey; label: string; field?: EnrichmentField; list?: string }[] = [
  { key: "industry", label: "Industry", field: "industry_id", list: "company.industry" },
  { key: "sub_industry", label: "Sub-industry" },
  { key: "employee_band", label: "Employees", field: "size_id", list: "company.size" },
  { key: "city", label: "HQ city", field: "city" },
  { key: "country", label: "HQ country", field: "country" },
  { key: "description", label: "Description", field: "description" },
  { key: "linkedin", label: "LinkedIn", field: "linkedin" },
];

/** The only link shown is a LinkedIn company page (FR-CRMAI-LLM-04). */
const LINKEDIN_COMPANY = /^https:\/\/([a-z]+\.)?linkedin\.com\/company\//i;

/** The select option whose label matches, case-insensitively (plan §5.7). */
function optionFor(lists: CrmLists, list: string, value: string): string | null {
  const wanted = value.trim().toLowerCase();
  return (lists[list] ?? []).find((o) => o.is_active && o.label.trim().toLowerCase() === wanted)?.id ?? null;
}

export function EnrichmentPanel({
  workspace,
  enrichment,
  lists,
  canEdit,
  reviewVisible,
  timeZone,
  onPromote,
}: {
  workspace: string;
  enrichment: CrmEnrichment;
  lists: CrmLists;
  /** Admins and Members promote (FR-CRMAI-ENR-04). */
  canEdit: boolean;
  /** Whether the Review Queue is shown, so the conflict link leads somewhere. */
  reviewVisible: boolean;
  timeZone: CrmTimezone;
  onPromote: (field: EnrichmentField, value: unknown) => Promise<void>;
}) {
  const [busy, setBusy] = useState<EnrichmentField | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { status, values } = enrichment;

  async function promote(field: EnrichmentField, value: unknown) {
    setBusy(field);
    setError(null);
    try {
      await onPromote(field, value);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  const rows = ROWS.filter((row) => values[row.key]);
  const products = values.products ?? [];

  return (
    <section
      aria-label="From the website"
      className="space-y-2 rounded-md border p-3 text-sm"
      data-testid="enrichment-panel"
    >
      <div className="flex items-center gap-1.5">
        <h2 className="text-sm font-medium">From the website</h2>
        {enrichment.ai && <AiMarker ai={enrichment.ai} timeZone={timeZone} />}
      </div>
      {status === "queued" && (
        <p className="text-xs text-muted-foreground">
          Queued: the company&apos;s website will be read in the background.
        </p>
      )}
      {status === "failed" && (
        <p className="text-xs text-muted-foreground">
          Not enriched{enrichment.failure_reason ? `: ${enrichment.failure_reason}` : ""}.
        </p>
      )}
      {error && (
        <p role="alert" className="rounded-md bg-destructive/10 p-2 text-xs text-destructive">
          {error}
        </p>
      )}
      {(rows.length > 0 || products.length > 0) && (
        <dl className="grid grid-cols-[minmax(0,8rem)_minmax(0,1fr)] gap-x-3 gap-y-1.5">
          {rows.map((row) => {
            const text = values[row.key] as string;
            const optionId = row.list ? optionFor(lists, row.list, text) : null;
            const promoted =
              row.field === "linkedin"
                ? { primary: { url: text }, others: [] }
                : row.list
                  ? optionId
                  : text;
            const offer = canEdit && row.field && enrichment.promotable[row.field] && promoted !== null;
            return (
              <div key={row.key} className="contents">
                <dt className="text-xs text-muted-foreground">{row.label}</dt>
                <dd className="flex min-w-0 flex-wrap items-start gap-1.5">
                  {row.key === "linkedin" && LINKEDIN_COMPANY.test(text) ? (
                    <a href={text} target="_blank" rel="noopener noreferrer" className="break-all hover:underline">
                      {text}
                    </a>
                  ) : (
                    <span className="break-words">{text}</span>
                  )}
                  {row.key === "industry" && !optionId && (
                    <span className="text-xs text-muted-foreground">No matching industry option</span>
                  )}
                  {offer && row.field && (
                    <Button
                      size="xs"
                      variant="outline"
                      disabled={busy !== null}
                      onClick={() => promote(row.field as EnrichmentField, promoted)}
                    >
                      {busy === row.field ? "Promoting…" : "Promote"}
                    </Button>
                  )}
                </dd>
              </div>
            );
          })}
          {products.length > 0 && (
            <div className="contents">
              <dt className="text-xs text-muted-foreground">Products or services</dt>
              <dd className="min-w-0 break-words">{products.join(", ")}</dd>
            </div>
          )}
        </dl>
      )}
      {enrichment.conflicts.length > 0 && reviewVisible && (
        <p className="text-xs text-muted-foreground">
          {enrichment.conflicts.length === 1
            ? "One value differs from the company's field and "
            : `${enrichment.conflicts.length} values differ from the company's fields and `}
          <Link href={`/crm/${workspace}/review#review-${enrichment.conflicts[0]}`} className="underline">
            {enrichment.conflicts.length === 1 ? "is waiting for review" : "are waiting for review"}
          </Link>
          .
        </p>
      )}
    </section>
  );
}
