"use client";

import Link from "next/link";

import type { CrmRecordPage, CrmRecord } from "@/lib/api/crm-types";

/**
 * Related — spec 007 FR-CRM-REC-03, FR-CRM-CO-02.
 *
 * A company lists its people; a person shows their company. Deals and, for
 * Labs, the TVA projects with their status and phase dates join with deals
 * (S4, S5); campaign memberships in 1b.
 */
export function RelatedPanel({
  workspace,
  related,
}: {
  workspace: string;
  related: CrmRecordPage<CrmRecord>["related"];
}) {
  if (related.people !== undefined) {
    return (
      <section className="space-y-2">
        <h3 className="text-sm font-medium">People ({related.people.length})</h3>
        {related.people.length === 0 ? (
          <p className="text-sm text-muted-foreground">No people at this company yet.</p>
        ) : (
          <ul className="divide-y rounded-md border">
            {related.people.map((person) => (
              <li key={person.id} className="p-2.5">
                <Link
                  href={`/crm/${workspace}/people/${person.id}`}
                  className="text-sm font-medium hover:underline"
                >
                  {person.name}
                </Link>
                <p className="truncate text-xs text-muted-foreground">
                  {[person.job_title, person.primary_email].filter(Boolean).join(" · ") || "—"}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
    );
  }
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">Company</h3>
      {related.company ? (
        <Link
          href={`/crm/${workspace}/companies/${related.company.id}`}
          className="text-sm hover:underline"
        >
          {related.company.name}
        </Link>
      ) : (
        <p className="text-sm text-muted-foreground">Not linked to a company.</p>
      )}
    </section>
  );
}
