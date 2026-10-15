"use client";

import Link from "next/link";

import { AmountText } from "@/components/crm/amount-field";
import { ProjectsSection } from "@/components/crm/project-chip";
import type { CrmCampaignMember, CrmOpportunity, CrmRecord, CrmRecordPage } from "@/lib/api/crm-types";
import type { CrmTimezone, NumberFormat } from "@/lib/crm/format";

/**
 * Related — spec 007 FR-CRM-REC-03, FR-CRM-CO-02.
 *
 * A company lists its people and deals; a person shows their company and the
 * deals they are on. A deal's company, point of contact and other contacts
 * are on its own page. A Labs company lists its TVA projects with their
 * status and phase dates, for every role (FR-CRM-CO-02, FR-CRM-TVA-02);
 * a company's and a person's campaign memberships (1b, with their status).
 * A Viewer's deals arrive without amounts and show none.
 */

function Memberships({ workspace, members }: { workspace: string; members: CrmCampaignMember[] }) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">Campaigns ({members.length})</h3>
      {members.length === 0 ? (
        <p className="text-sm text-muted-foreground">Not in any campaign.</p>
      ) : (
        <ul className="divide-y rounded-md border">
          {members.map((m) => (
            <li key={m.id} className="flex flex-wrap items-baseline justify-between gap-2 p-2.5">
              <Link href={`/crm/${workspace}/campaigns/${m.campaign_id}`} className="text-sm font-medium hover:underline">
                {m.campaign_name}
              </Link>
              <span className="text-xs text-muted-foreground">{m.status_label}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Deals({
  workspace,
  deals,
  numberFormat,
}: {
  workspace: string;
  deals: CrmOpportunity[];
  numberFormat: NumberFormat;
}) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">Deals ({deals.length})</h3>
      {deals.length === 0 ? (
        <p className="text-sm text-muted-foreground">No deals yet.</p>
      ) : (
        <ul className="divide-y rounded-md border">
          {deals.map((deal) => (
            <li key={deal.id} className="flex flex-wrap items-baseline justify-between gap-2 p-2.5">
              <div className="min-w-0">
                <Link
                  href={`/crm/${workspace}/opportunities/${deal.id}`}
                  className="text-sm font-medium hover:underline"
                >
                  {deal.name}
                </Link>
                <p className="text-xs text-muted-foreground">{deal.stage_label}</p>
              </div>
              {deal.amount !== undefined && (
                <span className="text-sm">
                  <AmountText
                    amount={deal.amount}
                    currency={deal.currency}
                    inr={deal.amount_in_inr}
                    numberFormat={numberFormat}
                  />
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function RelatedPanel({
  workspace,
  related,
  numberFormat = "indian_short",
  timeZone = "Asia/Kolkata",
}: {
  workspace: string;
  related: CrmRecordPage<CrmRecord>["related"];
  numberFormat?: NumberFormat;
  timeZone?: CrmTimezone;
}) {
  if (related.people !== undefined) {
    return (
      <div className="space-y-6">
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
        {related.opportunities && (
          <Deals workspace={workspace} deals={related.opportunities} numberFormat={numberFormat} />
        )}
        {related.projects && (
          <ProjectsSection
            title={`TVA projects (${related.projects.length})`}
            projects={related.projects}
            timeZone={timeZone}
            empty="No TVA projects yet."
          />
        )}
        {related.campaign_members && <Memberships workspace={workspace} members={related.campaign_members} />}
      </div>
    );
  }
  return (
    <div className="space-y-6">
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
      {related.opportunities && (
        <Deals workspace={workspace} deals={related.opportunities} numberFormat={numberFormat} />
      )}
      {related.campaign_members && <Memberships workspace={workspace} members={related.campaign_members} />}
    </div>
  );
}
