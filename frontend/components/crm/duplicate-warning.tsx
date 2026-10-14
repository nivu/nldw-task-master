"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";
import type { CrmDuplicates, CrmObject } from "@/lib/api/crm-types";

/**
 * Possible duplicates — spec 007 FR-CRM-DUP-02.
 *
 * Shown before saving when names are alike. The person may open one of them
 * instead, or carry on and create the record anyway. (An exact domain or
 * email clash is refused by the server, naming the record, FR-CRM-DUP-01.)
 */
export function DuplicateWarning({
  workspace,
  object,
  duplicates,
  busy,
  onContinue,
  onBack,
}: {
  workspace: string;
  object: CrmObject;
  duplicates: CrmDuplicates;
  busy: boolean;
  onContinue: () => void;
  onBack: () => void;
}) {
  const noun = object === "companies" ? "companies" : "people";
  return (
    <div className="space-y-3" role="alert" data-testid="duplicate-warning">
      <p className="text-sm font-medium">
        These {noun} have similar names. Is it one of them?
      </p>
      <ul className="divide-y rounded-md border">
        {duplicates.similar.map((match) => (
          <li key={match.id} className="flex items-center justify-between gap-2 p-2.5 text-sm">
            <Link
              href={`/crm/${workspace}/${object}/${match.id}`}
              className="truncate font-medium hover:underline"
            >
              {match.name}
            </Link>
            <span className="shrink-0 text-xs text-muted-foreground">
              {Math.round(match.similarity * 100)}% alike
            </span>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="outline" onClick={onBack}>
          Back
        </Button>
        <Button onClick={onContinue} disabled={busy}>
          {busy ? "Creating…" : "Create anyway"}
        </Button>
      </div>
    </div>
  );
}
