"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

import { useQuickActions } from "@/components/crm/command-menu";
import { DeleteCompanyDialog } from "@/components/crm/delete-company-dialog";
import { DriveLinks } from "@/components/crm/drive-links";
import { EmailsTab } from "@/components/crm/emails-tab";
import { NotesTab } from "@/components/crm/note-composer";
import { Chip, RecordPage, type FieldGroup } from "@/components/crm/record-page";
import { RelatedPanel } from "@/components/crm/related-panel";
import type { RelationSearch } from "@/components/crm/field-editor";
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
import type { CrmMember, CrmPerson } from "@/lib/api/crm-types";
import { useAsync } from "@/lib/use-async";

/** Spec 007 §5.7, grouped for the field panel (FR-CRM-REC-02). */
function personGroups(members: CrmMember[], searchCompanies: RelationSearch, current: CrmPerson): FieldGroup[] {
  const companyChoices = current.company_id
    ? [{ value: current.company_id, label: current.company_name ?? "Current company" }]
    : [];
  return [
    {
      title: "Person",
      fields: [
        { name: "first_name", label: "First name", type: "text", required: true },
        { name: "last_name", label: "Last name", type: "text" },
        { name: "job_title", label: "Job title", type: "text" },
        {
          name: "company_id",
          label: "Company",
          type: "relation",
          choices: companyChoices,
          search: searchCompanies,
        },
        { name: "city", label: "City", type: "text" },
      ],
    },
    {
      title: "Reaching them",
      fields: [
        { name: "primary_email", label: "Email", type: "email" },
        { name: "additional_emails", label: "Additional emails", type: "emails" },
        { name: "phones", label: "Phones", type: "phones" },
        { name: "linkedin", label: "LinkedIn", type: "links" },
        { name: "do_not_contact", label: "Do not contact", type: "boolean" },
      ],
    },
    {
      title: "Ownership",
      fields: [
        {
          name: "owner_id",
          label: "Owner",
          type: "relation",
          choices: members.map((m) => ({ value: m.user_id, label: m.display_name })),
        },
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
 * A person — spec 007 FR-CRM-PER-01, FR-CRM-REC-01 to -04, FR-CRM-LINK-03,
 * FR-CRM-DEL-04. The deals they are on are in Related; Delete is also the
 * command menu's quick action here (FR-CRM-SRCH-02).
 */
export default function PersonPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { bootstrap } = useCrm();
  const ws = bootstrap.workspace.key;
  const [version, setVersion] = useState(0);
  const [deleting, setDeleting] = useState(false);
  const { data, error, reload } = useAsync(
    () => Promise.all([getCrmRecord<CrmPerson>(ws, "people", id), getCrmMembers(ws)]),
    [ws, id]
  );
  const searchCompanies = useCallback<RelationSearch>(
    (q, after) => searchCompanyChoices(ws, q, after),
    [ws]
  );
  const loaded = data?.[0].record;
  const me = bootstrap.me.id;
  // FR-CRM-DEL-04: what they own, or created when nobody owns it.
  const mine = loaded && (loaded.owner_id ? loaded.owner_id === me : loaded.created_by === me);
  const mayDelete = bootstrap.role === "admin" || (bootstrap.role === "member" && !!mine);
  const quickActions = useMemo(
    () =>
      loaded && mayDelete ? [{ id: "delete", label: `Delete ${loaded.name}…`, run: () => setDeleting(true) }] : [],
    [loaded, mayDelete]
  );
  useQuickActions(loaded ? { record_type: "person", record_id: loaded.id } : null, quickActions);

  if (error) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">{error}</p>
        <Link href={`/crm/${ws}/people`} className="text-sm hover:underline">
          Back to people
        </Link>
      </div>
    );
  }
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const [page, members] = data;
  const person = page.record;
  const canEdit = bootstrap.role !== "viewer";
  const refresh = () => {
    reload();
    setVersion((v) => v + 1);
  };

  async function save(name: string, value: unknown) {
    await updateCrmRecord(ws, "people", person.id, { [name]: value }, person.updated_at);
    refresh();
  }

  return (
    <>
      <RecordPage
        pin={{ recordType: "person", id: person.id }}
        title={person.name}
        subtitle={[person.job_title, person.company_name].filter(Boolean).join(" at ") || undefined}
        flags={person}
        chips={[
          <Chip key="owner" label="Owner" value={person.owner_name ?? "—"} />,
          ...(person.do_not_contact ? [<Chip key="dnc" label="Do not contact" value="Yes" />] : []),
        ]}
        actions={
          mayDelete && (
            <Button variant="outline" size="sm" onClick={() => setDeleting(true)}>
              Delete
            </Button>
          )
        }
        groups={personGroups(members, searchCompanies, person)}
        values={person as unknown as Record<string, unknown>}
        canEdit={canEdit}
        timeZone={bootstrap.me.timezone}
        onSave={save}
        timeline={
          <Timeline
            workspace={ws}
            object="people"
            recordId={person.id}
            initial={page.timeline_head}
            timeZone={bootstrap.me.timezone}
            version={version}
          />
        }
        files={
          <DriveLinks
            workspace={ws}
            recordType="person"
            recordId={person.id}
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
            link={{ record_type: "person", record_id: person.id, name: person.name }}
            onChange={refresh}
          />
        }
        notes={
          <NotesTab
            workspace={ws}
            canEdit={canEdit}
            members={members}
            notes={page.notes ?? []}
            link={{ record_type: "person", record_id: person.id }}
            timeZone={bootstrap.me.timezone}
            onChange={refresh}
          />
        }
        emails={
          <EmailsTab
            workspace={ws}
            object="people"
            recordId={person.id}
            timeZone={bootstrap.me.timezone}
            syncOn={bootstrap.email_sync_on}
          />
        }
        related={
          <RelatedPanel workspace={ws} related={page.related} numberFormat={bootstrap.me.number_format} />
        }
      />
      <DeleteCompanyDialog
        open={deleting}
        onOpenChange={setDeleting}
        name={person.name}
        peopleCount={null}
        onDelete={async () => {
          await deleteCrmRecord(ws, "people", person.id);
          router.push(`/crm/${ws}/people`);
        }}
      />
    </>
  );
}
