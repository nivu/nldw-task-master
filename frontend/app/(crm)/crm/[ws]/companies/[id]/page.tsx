"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { AiFieldsPanel } from "@/components/crm/ai-fields-panel";
import { useQuickActions } from "@/components/crm/command-menu";
import { ContactRecencyChip } from "@/components/crm/contact-recency-chip";
import { DeleteCompanyDialog } from "@/components/crm/delete-company-dialog";
import { DriveLinks } from "@/components/crm/drive-links";
import { EmailsTab } from "@/components/crm/emails-tab";
import { EnrichmentPanel } from "@/components/crm/enrichment-panel";
import { MergeDialog } from "@/components/crm/merge-dialog";
import { NotesTab } from "@/components/crm/note-composer";
import type { Choice } from "@/components/crm/field-editor";
import { Chip, RecordPage, type FieldGroup } from "@/components/crm/record-page";
import { RelatedPanel } from "@/components/crm/related-panel";
import { useCrm } from "@/components/crm/shell";
import { TasksTab } from "@/components/crm/task-dialog";
import { Timeline } from "@/components/crm/timeline";
import { Button } from "@/components/ui/button";
import {
  deleteCrmRecord,
  getCrmMembers,
  getCrmRecord,
  searchCompanyChoices,
  updateCrmRecord,
} from "@/lib/api/crm";
import type { CrmBootstrap, CrmCompany, CrmMember } from "@/lib/api/crm-types";
import { useAsync } from "@/lib/use-async";

/** Contracts §2.5.1 — chosen by hand; Deal open, Customer and Partner follow
 *  from deals (FR-CRM-PIPE-04, -05). */
const MANUAL_LEAD_STAGES = ["new", "contacted", "nurturing", "qualified", "disqualified"];

const ENTITIES: Choice[] = [
  { value: "nunnari_labs", label: "Nunnari Labs" },
  { value: "deepweaver", label: "DeepWeaver" },
];

function options(bootstrap: CrmBootstrap, listKey: string): Choice[] {
  return (bootstrap.lists[listKey] ?? []).map((o) => ({
    value: o.id,
    label: o.label,
    inactive: !o.is_active,
  }));
}

/** Spec 007 §5.6, grouped for the field panel (FR-CRM-REC-02). */
function companyGroups(
  bootstrap: CrmBootstrap,
  members: CrmMember[],
  current: CrmCompany
): FieldGroup[] {
  const stages = (bootstrap.lists["company.lead_stage"] ?? [])
    .filter((o) => o.system_key && (MANUAL_LEAD_STAGES.includes(o.system_key) || o.system_key === current.lead_stage))
    .map((o) => ({ value: o.system_key as string, label: o.label }));
  const labs = bootstrap.workspace.has_entity;
  return [
    {
      title: "Company",
      fields: [
        { name: "name", label: "Name", type: "text", required: true },
        { name: "domain", label: "Domain", type: "domain" },
        { name: "additional_domains", label: "Additional domains", type: "domains" },
        ...(labs ? [{ name: "entities", label: "Entity", type: "multi_select" as const, choices: ENTITIES }] : []),
        { name: "description", label: "Description", type: "long_text" },
      ],
    },
    {
      title: "Relationship",
      fields: [
        { name: "lead_stage", label: "Lead stage", type: "select", required: true, choices: stages },
        {
          name: "owner_id",
          label: "Owner",
          type: "relation",
          required: true,
          choices: members.map((m) => ({ value: m.user_id, label: m.display_name })),
        },
        { name: "account_type_id", label: "Account type", type: "select", choices: options(bootstrap, "company.account_type") },
        {
          name: "relationship_status_id",
          label: "Relationship status",
          type: "select",
          choices: options(bootstrap, "company.relationship_status"),
        },
        { name: "channel_ids", label: "Channels", type: "multi_select", choices: options(bootstrap, "channel") },
        { name: "next_touch_on", label: "Next touch", type: "date" },
        { name: "source_id", label: "Source", type: "select", choices: options(bootstrap, "source") },
      ],
    },
    {
      title: "Profile",
      fields: [
        { name: "industry_id", label: "Industry", type: "select", choices: options(bootstrap, "company.industry") },
        ...(!bootstrap.workspace.tva_linked
          ? [
              {
                name: "institution_type_id",
                label: "Institution type",
                type: "select" as const,
                choices: options(bootstrap, "company.institution_type"),
              },
            ]
          : []),
        { name: "size_id", label: "Size", type: "select", choices: options(bootstrap, "company.size") },
        { name: "country", label: "Country", type: "text" },
        { name: "city", label: "City", type: "text" },
        { name: "address", label: "Address", type: "address" },
        { name: "website", label: "Website", type: "links" },
        { name: "linkedin", label: "LinkedIn", type: "links" },
      ],
    },
    {
      title: "Contact history",
      fields: [
        { name: "last_inbound_at", label: "Last inbound", type: "datetime", readOnly: true },
        { name: "last_outbound_at", label: "Last outbound", type: "datetime", readOnly: true },
        { name: "last_contact_at", label: "Last contact", type: "datetime", readOnly: true },
      ],
    },
  ];
}

/**
 * A company — spec 007 FR-CRM-CO-01 to -03, FR-CRM-REC-01 to -04,
 * FR-CRM-LINK-03, FR-CRM-DEL-02, FR-CRM-DUP-03. Its deals are in Related,
 * its tasks and notes in their tabs; a Labs company's TVA projects are in
 * Related with their status and phase dates (FR-CRM-CO-02). Admins and
 * Members merge a duplicate into it, or it into one; Merge and Delete are
 * also the command menu's quick actions here. (009, phase 2) Its enriched
 * values sit under the header, each promotable by an Admin or Member
 * (FR-CRMAI-ENR-04); (phase 3) so do its AI fields, read-only, which an
 * Admin or Member may recompute (FR-CRMAI-FIELD-04, -05); (phase 4) its
 * contact recency is a header chip (FR-CRMAI-REL-02).
 */
export default function CompanyPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { bootstrap } = useCrm();
  const ws = bootstrap.workspace.key;
  const [version, setVersion] = useState(0);
  const [deleting, setDeleting] = useState(false);
  const [merging, setMerging] = useState(false);
  const { data, error, reload } = useAsync(
    () => Promise.all([getCrmRecord<CrmCompany>(ws, "companies", id), getCrmMembers(ws)]),
    [ws, id]
  );
  const loaded = data?.[0].record;
  const canEdit = bootstrap.role !== "viewer";
  const mayDelete =
    bootstrap.role === "admin" || (bootstrap.role === "member" && loaded?.owner_id === bootstrap.me.id);
  const quickActions = useMemo(
    () =>
      loaded
        ? [
            ...(canEdit ? [{ id: "merge", label: `Merge ${loaded.name}…`, run: () => setMerging(true) }] : []),
            ...(mayDelete ? [{ id: "delete", label: `Delete ${loaded.name}…`, run: () => setDeleting(true) }] : []),
          ]
        : [],
    [loaded, canEdit, mayDelete]
  );
  useQuickActions(loaded ? { record_type: "company", record_id: loaded.id } : null, quickActions);

  if (error) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">{error}</p>
        <Link href={`/crm/${ws}/companies`} className="text-sm hover:underline">
          Back to companies
        </Link>
      </div>
    );
  }
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const [page, members] = data;
  const company = page.record;
  const refresh = () => {
    reload();
    setVersion((v) => v + 1);
  };

  async function save(name: string, value: unknown) {
    await updateCrmRecord(ws, "companies", company.id, { [name]: value }, company.updated_at);
    refresh();
  }

  return (
    <>
      <RecordPage
        pin={{ recordType: "company", id: company.id }}
        title={company.name}
        subtitle={company.domain ?? undefined}
        notice={
          (page.merged_from || page.enrichment || page.ai_fields) && (
            <>
              {page.merged_from && (
                <p className="rounded-md bg-muted p-2 text-xs">
                  {page.merged_from.name} was merged into this company.
                </p>
              )}
              {page.enrichment && (
                <EnrichmentPanel
                  workspace={ws}
                  enrichment={page.enrichment}
                  lists={bootstrap.lists}
                  canEdit={canEdit}
                  reviewVisible={bootstrap.ai.review_visible}
                  timeZone={bootstrap.me.timezone}
                  onPromote={save}
                />
              )}
              {page.ai_fields && (
                <AiFieldsPanel
                  workspace={ws}
                  object="companies"
                  recordId={company.id}
                  fields={page.ai_fields}
                  canEdit={canEdit}
                  timeZone={bootstrap.me.timezone}
                />
              )}
            </>
          )
        }
        flags={company}
        chips={[
          <Chip key="stage" label="Lead stage" value={company.lead_stage_label} />,
          <Chip key="owner" label="Owner" value={company.owner_name ?? "—"} />,
          ...(company.entity_labels.length
            ? [<Chip key="entity" label="Entity" value={company.entity_labels.join(", ")} />]
            : []),
          ...(page.contact_recency?.level
            ? [<ContactRecencyChip key="recency" recency={page.contact_recency} />]
            : []),
        ]}
        actions={
          (canEdit || mayDelete) && (
            <>
              {canEdit && (
                <Button variant="outline" size="sm" onClick={() => setMerging(true)}>
                  Merge
                </Button>
              )}
              {mayDelete && (
                <Button variant="outline" size="sm" onClick={() => setDeleting(true)}>
                  Delete
                </Button>
              )}
            </>
          )
        }
        groups={companyGroups(bootstrap, members, company)}
        values={company as unknown as Record<string, unknown>}
        canEdit={canEdit}
        timeZone={bootstrap.me.timezone}
        onSave={save}
        timeline={
          <Timeline
            workspace={ws}
            object="companies"
            recordId={company.id}
            initial={page.timeline_head}
            timeZone={bootstrap.me.timezone}
            version={version}
            summaries={page.summaries}
          />
        }
        files={
          <DriveLinks
            workspace={ws}
            recordType="company"
            recordId={company.id}
            links={page.links}
            canEdit={canEdit}
            onChange={refresh}
          />
        }
        tasks={
          <TasksTab
            workspace={ws}
            bootstrap={bootstrap}
            members={members}
            tasks={page.tasks ?? []}
            link={{ record_type: "company", record_id: company.id, name: company.name }}
            onChange={refresh}
          />
        }
        notes={
          <NotesTab
            workspace={ws}
            canEdit={canEdit}
            members={members}
            notes={page.notes ?? []}
            link={{ record_type: "company", record_id: company.id }}
            timeZone={bootstrap.me.timezone}
            onChange={refresh}
          />
        }
        emails={
          <EmailsTab
            workspace={ws}
            object="companies"
            recordId={company.id}
            timeZone={bootstrap.me.timezone}
            syncOn={bootstrap.email_sync_on}
          />
        }
        related={
          <RelatedPanel
            workspace={ws}
            related={page.related}
            numberFormat={bootstrap.me.number_format}
            timeZone={bootstrap.me.timezone}
          />
        }
      />
      {canEdit && (
        <MergeDialog
          open={merging}
          onOpenChange={setMerging}
          workspace={ws}
          company={{ id: company.id, name: company.name }}
          searchCompanies={async (q, after) => {
            const found = await searchCompanyChoices(ws, q, after);
            return { ...found, choices: found.choices.filter((c) => c.value !== company.id) };
          }}
          onMerged={(survivorId) => {
            if (survivorId === company.id) refresh();
            else router.push(`/crm/${ws}/companies/${survivorId}`);
          }}
        />
      )}
      <DeleteCompanyDialog
        open={deleting}
        onOpenChange={setDeleting}
        name={company.name}
        peopleCount={page.related.people?.length ?? 0}
        dealsCount={page.related.opportunities?.length ?? 0}
        searchCompanies={async (q, after) => {
          const found = await searchCompanyChoices(ws, q, after);
          return { ...found, choices: found.choices.filter((c) => c.value !== company.id) };
        }}
        onDelete={async (people, deals) => {
          await deleteCrmRecord(ws, "companies", company.id, {
            ...(people ? { people } : {}),
            ...(deals ? { deals } : {}),
          });
          router.push(`/crm/${ws}/companies`);
        }}
      />
    </>
  );
}
