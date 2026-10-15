"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { AmountText } from "@/components/crm/amount-field";
import { useQuickActions } from "@/components/crm/command-menu";
import { ContactsPanel } from "@/components/crm/contacts-panel";
import { DeleteCompanyDialog } from "@/components/crm/delete-company-dialog";
import { DriveLinks } from "@/components/crm/drive-links";
import type { Choice } from "@/components/crm/field-editor";
import { LinkProjectDialog } from "@/components/crm/link-project-dialog";
import { LostDialog } from "@/components/crm/lost-dialog";
import { NotesTab } from "@/components/crm/note-composer";
import { ProjectChip } from "@/components/crm/project-chip";
import { Chip, RecordPage, type FieldGroup } from "@/components/crm/record-page";
import { useCrm } from "@/components/crm/shell";
import { STAGE_ORDER, StageDialog } from "@/components/crm/stage-dialog";
import { TasksTab } from "@/components/crm/task-dialog";
import { Timeline } from "@/components/crm/timeline";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  deleteCrmRecord,
  getCrmMembers,
  getCrmRecord,
  searchCompanyChoices,
  searchPersonChoices,
  updateCrmRecord,
} from "@/lib/api/crm";
import type { CrmBootstrap, CrmMember, CrmOpportunity, StageKey } from "@/lib/api/crm-types";
import { useAsync } from "@/lib/use-async";

const ENTITIES: Choice[] = [
  { value: "nunnari_labs", label: "Nunnari Labs" },
  { value: "deepweaver", label: "DeepWeaver" },
];
const CATEGORIES: Choice[] = [
  { value: "client", label: "Client" },
  { value: "poc", label: "POC" },
  { value: "product", label: "Product" },
];

function options(bootstrap: CrmBootstrap, listKey: string): Choice[] {
  return (bootstrap.lists[listKey] ?? []).map((o) => ({
    value: o.id,
    label: o.label,
    inactive: !o.is_active,
  }));
}

function stageLabel(bootstrap: CrmBootstrap, stage: StageKey): string {
  const found = (bootstrap.lists["opportunity.stage"] ?? []).find((o) => o.system_key === stage);
  return found?.label ?? stage.replace("_", " ");
}

/** Spec 007 §5.8, grouped for the field panel (FR-CRM-REC-02). The stage
 *  is moved with Move stage, never edited here. */
function dealGroups(bootstrap: CrmBootstrap, members: CrmMember[], deal: CrmOpportunity): FieldGroup[] {
  const ws = bootstrap.workspace.key;
  const labs = bootstrap.workspace.has_entity;
  const viewer = bootstrap.role === "viewer";
  return [
    {
      title: "Deal",
      fields: [
        { name: "name", label: "Name", type: "text", required: true },
        {
          name: "company_id",
          label: "Company",
          type: "relation",
          required: true,
          choices: [{ value: deal.company_id, label: deal.company_name ?? "Current company" }],
          search: (q, after) => searchCompanyChoices(ws, q, after),
        },
        {
          name: "point_of_contact_id",
          label: "Point of contact",
          type: "relation",
          choices: deal.point_of_contact_id
            ? [{ value: deal.point_of_contact_id, label: deal.point_of_contact_name ?? "Current contact" }]
            : [],
          search: (q, after) => searchPersonChoices(ws, q, after),
        },
        {
          name: "owner_id",
          label: "Owner",
          type: "relation",
          required: true,
          choices: members.map((m) => ({ value: m.user_id, label: m.display_name })),
        },
        ...(labs ? [{ name: "entity", label: "Entity", type: "select" as const, choices: ENTITIES }] : []),
        { name: "channel_id", label: "Channel", type: "select", choices: options(bootstrap, "channel") },
        { name: "deal_type_id", label: "Deal type", type: "select", choices: options(bootstrap, "opportunity.deal_type") },
        {
          name: "engagement_model_id",
          label: "Engagement model",
          type: "select",
          choices: options(bootstrap, "opportunity.engagement_model"),
        },
        ...(labs
          ? [{ name: "project_category", label: "Project category", type: "select" as const, choices: CATEGORIES }]
          : []),
        ...(!bootstrap.workspace.tva_linked
          ? [
              {
                name: "programme_id",
                label: "Programme",
                type: "select" as const,
                choices: options(bootstrap, "opportunity.programme"),
              },
            ]
          : []),
      ],
    },
    {
      title: "Value",
      fields: [
        // A Viewer receives no amounts (FR-CRM-ACC-11): nothing to show.
        ...(!viewer
          ? [
              { name: "money", label: "Amount", type: "currency" as const },
              { name: "inr", label: "Amount in INR", type: "currency" as const, readOnly: true },
              { name: "weighted", label: "Weighted (INR)", type: "currency" as const, readOnly: true },
            ]
          : []),
        { name: "probability", label: "Probability (%)", type: "number" },
        { name: "expected_close_on", label: "Expected close date", type: "date" },
      ],
    },
    {
      title: "Next step",
      fields: [
        { name: "next_step", label: "Next step", type: "text" },
        { name: "next_step_on", label: "Next step date", type: "date" },
        { name: "proposal_sent_on", label: "Proposal sent on", type: "date" },
      ],
    },
    {
      title: "Outcome",
      fields: [
        { name: "won_on", label: "Won on", type: "date", readOnly: true },
        { name: "lost_on", label: "Lost on", type: "date", readOnly: true },
        { name: "lost_reason_label", label: "Lost reason", type: "text", readOnly: true },
        { name: "lost_notes", label: "Lost notes", type: "long_text" },
      ],
    },
  ];
}

/**
 * A deal — spec 007 FR-CRM-OPP-01 to -03, FR-CRM-PIPE-07 to -14,
 * FR-CRM-FX-06, FR-CRM-REC-01 to -04, FR-CRM-LINK-03.
 *
 * Move stage asks the server what the move needs and asks for exactly that
 * (`stage-dialog.tsx`); Closed Lost takes a reason (`lost-dialog.tsx`). In
 * Labs (FR-CRM-TVA) the deal's TVA project is in Related, with its status and
 * phase dates for every role; Closed Won goes through the handoff; a Labs
 * admin may Link TVA project on a deal at Proposal or later that has none
 * (FR-CRM-TVA-18). Move stage, Link TVA project and Delete are also the
 * command menu's quick actions here (FR-CRM-SRCH-02).
 */
export default function OpportunityPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { bootstrap } = useCrm();
  const ws = bootstrap.workspace.key;
  const numberFormat = bootstrap.me.number_format;
  const [version, setVersion] = useState(0);
  const [deleting, setDeleting] = useState(false);
  const [target, setTarget] = useState<StageKey | null>(null);
  const [linking, setLinking] = useState(false);
  const { data, error, reload } = useAsync(
    () => Promise.all([getCrmRecord<CrmOpportunity>(ws, "opportunities", id), getCrmMembers(ws)]),
    [ws, id]
  );
  const loaded = data?.[0].record;
  const canEdit = bootstrap.role !== "viewer";
  const mayDelete =
    bootstrap.role === "admin" || (bootstrap.role === "member" && loaded?.owner_id === bootstrap.me.id);
  const labs = bootstrap.workspace.tva_linked;
  // FR-CRM-TVA-18: imported deals reach their projects this way.
  const mayLinkProject =
    labs &&
    bootstrap.role === "admin" &&
    !!loaded &&
    !loaded.project &&
    ["proposal", "negotiation", "closed_won"].includes(loaded.stage);
  const quickActions = useMemo(
    () =>
      loaded
        ? [
            ...(canEdit
              ? STAGE_ORDER.filter((s) => s !== loaded.stage).map((s) => ({
                  id: `stage-${s}`,
                  label: `Move to ${stageLabel(bootstrap, s)}…`,
                  run: () => setTarget(s),
                }))
              : []),
            ...(mayLinkProject ? [{ id: "link-project", label: "Link TVA project…", run: () => setLinking(true) }] : []),
            ...(mayDelete ? [{ id: "delete", label: `Delete ${loaded.name}…`, run: () => setDeleting(true) }] : []),
          ]
        : [],
    [loaded, canEdit, mayLinkProject, mayDelete, bootstrap]
  );
  useQuickActions(loaded ? { record_type: "opportunity", record_id: loaded.id } : null, quickActions);

  if (error) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">{error}</p>
        <Link href={`/crm/${ws}/opportunities`} className="text-sm hover:underline">
          Back to opportunities
        </Link>
      </div>
    );
  }
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const [page, members] = data;
  const deal = page.record;
  const refresh = () => {
    reload();
    setVersion((v) => v + 1);
  };
  const moved = () => {
    setTarget(null);
    refresh();
  };

  const values: Record<string, unknown> = {
    ...deal,
    money: deal.amount !== undefined ? { amount: deal.amount, currency: deal.currency ?? "INR" } : undefined,
    inr: deal.amount_in_inr ? { amount: deal.amount_in_inr, currency: "INR" } : null,
    weighted: deal.weighted_amount ? { amount: deal.weighted_amount, currency: "INR" } : null,
  };

  async function save(name: string, value: unknown) {
    let changes: Record<string, unknown> = { [name]: value };
    if (name === "money") {
      const money = value as { amount: string | null; currency: string } | null;
      changes = money?.amount ? { amount: money.amount, currency: money.currency } : { amount: null };
    }
    if (name === "probability") {
      changes = { probability: value === null || value === "" ? null : Number(value) };
    }
    await updateCrmRecord(ws, "opportunities", deal.id, changes, deal.updated_at);
    refresh();
  }

  const stages = STAGE_ORDER.filter((s) => s !== deal.stage);

  return (
    <>
      <RecordPage
        pin={{ recordType: "opportunity", id: deal.id }}
        title={deal.name}
        subtitle={
          <Link href={`/crm/${ws}/companies/${deal.company_id}`} className="hover:underline">
            {deal.company_name}
          </Link>
        }
        flags={deal}
        chips={[
          <Chip key="stage" label="Stage" value={deal.stage_label} />,
          <Chip key="owner" label="Owner" value={deal.owner_name ?? "—"} />,
          ...(deal.amount !== undefined
            ? [
                <Chip
                  key="amount"
                  label="Amount"
                  value={
                    <AmountText
                      amount={deal.amount}
                      currency={deal.currency}
                      inr={deal.amount_in_inr}
                      numberFormat={numberFormat}
                    />
                  }
                />,
              ]
            : []),
          <Chip key="probability" label="Probability" value={`${deal.probability}%`} />,
          ...(deal.project
            ? [
                <Chip
                  key="project"
                  label="TVA project"
                  value={`${deal.project.name} · ${deal.project.is_archived ? "archived" : deal.project.status}`}
                />,
              ]
            : []),
          ...(deal.entity_label ? [<Chip key="entity" label="Entity" value={deal.entity_label} />] : []),
        ]}
        actions={
          <>
            {canEdit && (
              <Select
                items={stages.map((s) => ({ value: s, label: stageLabel(bootstrap, s) }))}
                value=""
                onValueChange={(next) => next && setTarget(next as StageKey)}
              >
                <SelectTrigger aria-label="Move stage" className="h-8 w-40">
                  <SelectValue placeholder="Move stage" />
                </SelectTrigger>
                <SelectContent>
                  {stages.map((s) => (
                    <SelectItem key={s} value={s}>
                      {stageLabel(bootstrap, s)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            {mayLinkProject && (
              <Button variant="outline" size="sm" onClick={() => setLinking(true)}>
                Link TVA project
              </Button>
            )}
            {mayDelete && (
              <Button variant="outline" size="sm" onClick={() => setDeleting(true)}>
                Delete
              </Button>
            )}
          </>
        }
        groups={dealGroups(bootstrap, members, deal)}
        values={values}
        canEdit={canEdit}
        timeZone={bootstrap.me.timezone}
        onSave={save}
        timeline={
          <Timeline
            workspace={ws}
            object="opportunities"
            recordId={deal.id}
            initial={page.timeline_head}
            timeZone={bootstrap.me.timezone}
            version={version}
          />
        }
        tasks={
          <TasksTab
            workspace={ws}
            bootstrap={bootstrap}
            members={members}
            tasks={page.tasks ?? []}
            link={{ record_type: "opportunity", record_id: deal.id, name: deal.name }}
            onChange={refresh}
          />
        }
        notes={
          <NotesTab
            workspace={ws}
            canEdit={canEdit}
            members={members}
            notes={page.notes ?? []}
            link={{ record_type: "opportunity", record_id: deal.id }}
            timeZone={bootstrap.me.timezone}
            onChange={refresh}
          />
        }
        files={
          <DriveLinks
            workspace={ws}
            recordType="opportunity"
            recordId={deal.id}
            links={page.links}
            canEdit={canEdit}
            onChange={refresh}
          />
        }
        related={
          <div className="space-y-6">
            <section className="space-y-1">
              <h3 className="text-sm font-medium">Company</h3>
              <Link href={`/crm/${ws}/companies/${deal.company_id}`} className="text-sm hover:underline">
                {deal.company_name}
              </Link>
            </section>
            <section className="space-y-1">
              <h3 className="text-sm font-medium">Point of contact</h3>
              {page.related.point_of_contact ? (
                <Link
                  href={`/crm/${ws}/people/${page.related.point_of_contact.id}`}
                  className="text-sm hover:underline"
                >
                  {page.related.point_of_contact.name}
                </Link>
              ) : (
                <p className="text-sm text-muted-foreground">None yet; Qualification needs one.</p>
              )}
            </section>
            {labs && (
              <section className="space-y-2">
                <h3 className="text-sm font-medium">TVA project</h3>
                {page.related.project ? (
                  <ProjectChip project={page.related.project} timeZone={bootstrap.me.timezone} />
                ) : (
                  <p className="text-sm text-muted-foreground">
                    None yet; moving to Proposal creates a tentative one.
                  </p>
                )}
              </section>
            )}
            <ContactsPanel
              workspace={ws}
              dealId={deal.id}
              contacts={page.related.contacts ?? []}
              roles={bootstrap.lists["opportunity.contact_role"] ?? []}
              canEdit={canEdit}
              onChange={refresh}
            />
          </div>
        }
      />
      <StageDialog
        open={target !== null && target !== "closed_lost"}
        onOpenChange={(open) => !open && setTarget(null)}
        workspace={ws}
        deal={deal}
        stage={target !== "closed_lost" ? target : null}
        stageLabel={target ? stageLabel(bootstrap, target) : ""}
        numberFormat={numberFormat}
        labs={labs}
        onMoved={moved}
      />
      <LinkProjectDialog
        open={linking}
        onOpenChange={setLinking}
        workspace={ws}
        deal={deal}
        onLinked={refresh}
      />
      <LostDialog
        open={target === "closed_lost"}
        onOpenChange={(open) => !open && setTarget(null)}
        workspace={ws}
        deal={deal}
        reasons={bootstrap.lists["opportunity.lost_reason"] ?? []}
        onMoved={moved}
      />
      <DeleteCompanyDialog
        open={deleting}
        onOpenChange={setDeleting}
        name={deal.name}
        peopleCount={null}
        onDelete={async () => {
          await deleteCrmRecord(ws, "opportunities", deal.id);
          router.push(`/crm/${ws}/opportunities`);
        }}
      />
    </>
  );
}
