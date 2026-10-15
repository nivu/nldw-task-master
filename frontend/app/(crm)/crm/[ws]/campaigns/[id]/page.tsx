"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";

import { AmountText } from "@/components/crm/amount-field";
import { useQuickActions } from "@/components/crm/command-menu";
import { ConvertDialog } from "@/components/crm/convert-dialog";
import { DeleteCompanyDialog } from "@/components/crm/delete-company-dialog";
import { DriveLinks } from "@/components/crm/drive-links";
import { FieldEditor, type Choice } from "@/components/crm/field-editor";
import { Kanban, type KanbanColumn } from "@/components/crm/kanban";
import { NotesTab } from "@/components/crm/note-composer";
import { Chip, RecordPage, type FieldGroup } from "@/components/crm/record-page";
import { useCrm } from "@/components/crm/shell";
import { TasksTab } from "@/components/crm/task-dialog";
import { Timeline } from "@/components/crm/timeline";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  addCampaignMembers,
  deleteCrmRecord,
  getCrmMembers,
  getCrmRecord,
  listCampaignMembers,
  listCrmViews,
  searchCompanyChoices,
  searchPersonChoices,
  updateCampaignMember,
  updateCrmRecord,
} from "@/lib/api/crm";
import type {
  CampaignMembers,
  CrmBootstrap,
  CrmCampaign,
  CrmCampaignMember,
  CrmMember,
  CrmOpportunity,
  CrmQueryGroup,
  MembersAdded,
  MemberStatus,
} from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { formatAmount, formatDate, type NumberFormat } from "@/lib/crm/format";
import { NATIVE_SELECT } from "@/lib/crm/view-fields";
import { useAsync } from "@/lib/use-async";

/**
 * A campaign — spec 007 FR-CRM-CAMP-01 to -07, FR-CRM-REC-01 to -04,
 * FR-CRM-ACC-11; journey 4.9.
 *
 * Its figures (members by status, conversion rate, pipeline created, won
 * value and, with a budget, cost per converted member) and its member
 * board: a kanban by status where members are dragged through the
 * statuses, given a last touch and notes, and converted. Dropping a member
 * on Converted asks for the deal (new or existing); the deal then records
 * the campaign as its source. Members are added from companies and people
 * (here, from a saved view, or a list's bulk bar); someone marked Do not
 * contact is skipped and named. A Viewer sees the board and no money.
 */

/** A card as the kanban holds it. */
type Row = Record<string, unknown> & { id: string; updated_at: string };

const asMember = (row: Row) => row as unknown as CrmCampaignMember;

const ENTITIES: Choice[] = [
  { value: "nunnari_labs", label: "Nunnari Labs" },
  { value: "deepweaver", label: "DeepWeaver" },
];

function options(bootstrap: CrmBootstrap, listKey: string): Choice[] {
  return (bootstrap.lists[listKey] ?? []).map((o) => ({ value: o.id, label: o.label, inactive: !o.is_active }));
}

function campaignGroups(bootstrap: CrmBootstrap, members: CrmMember[], campaign: CrmCampaign): FieldGroup[] {
  const groups: FieldGroup[] = [
    {
      title: "Campaign",
      fields: [
        { name: "name", label: "Name", type: "text", required: true },
        { name: "type_id", label: "Type", type: "select", choices: options(bootstrap, "campaign.type") },
        { name: "status_id", label: "Status", type: "select", choices: options(bootstrap, "campaign.status") },
        { name: "channel_id", label: "Channel", type: "select", choices: options(bootstrap, "channel") },
        ...(bootstrap.workspace.has_entity
          ? [{ name: "entity", label: "Entity", type: "select" as const, choices: ENTITIES }]
          : []),
        { name: "starts_on", label: "Start date", type: "date" },
        { name: "ends_on", label: "End date", type: "date" },
        { name: "goal", label: "Goal", type: "long_text" },
      ],
    },
    {
      title: "Ownership",
      fields: [
        {
          name: "owner_id",
          label: "Owner",
          type: "relation",
          required: true,
          choices: members.map((m) => ({ value: m.user_id, label: m.display_name })),
        },
      ],
    },
  ];
  // A Viewer receives no budget (FR-CRM-ACC-11): nothing to show.
  if (campaign.budget !== undefined) {
    groups.push({
      title: "Budget",
      fields: [
        { name: "money", label: "Budget", type: "currency" },
        { name: "inr", label: "Budget in INR", type: "currency", readOnly: true },
      ],
    });
  }
  return groups;
}

/** FR-CRM-CAMP-05 — the figures, money only where the person may see it. */
function Figures({ campaign, numberFormat }: { campaign: CrmCampaign; numberFormat: NumberFormat }) {
  const money = (value: string | null | undefined) => (value ? formatAmount(value, "INR", numberFormat) : "—");
  const tiles: { label: string; value: string }[] = [
    { label: "Members", value: String(campaign.member_count) },
    { label: "Converted", value: String(campaign.converted_members) },
    { label: "Conversion rate", value: campaign.conversion_rate ? `${campaign.conversion_rate}%` : "—" },
    { label: "Deals", value: String(campaign.converted_deals) },
  ];
  if (campaign.pipeline_created !== undefined) {
    tiles.push(
      { label: "Pipeline created", value: money(campaign.pipeline_created) },
      { label: "Won value", value: money(campaign.won_value) },
      { label: "Cost per converted", value: money(campaign.cost_per_converted) }
    );
  }
  return (
    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7" data-testid="campaign-figures">
      {tiles.map((tile) => (
        <div key={tile.label} className="rounded-md border p-2">
          <dt className="text-xs text-muted-foreground">{tile.label}</dt>
          <dd className="font-heading text-base font-semibold">{tile.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export default function CampaignPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { bootstrap } = useCrm();
  const ws = bootstrap.workspace.key;
  const [version, setVersion] = useState(0);
  const [deleting, setDeleting] = useState(false);
  const { data, error, reload } = useAsync(
    () =>
      Promise.all([
        getCrmRecord<CrmCampaign>(ws, "campaigns", id),
        getCrmMembers(ws),
        listCampaignMembers(ws, id),
      ]),
    [ws, id]
  );
  const loaded = data?.[0].record;
  const me = bootstrap.me.id;
  const mine = loaded && (loaded.owner_id ? loaded.owner_id === me : loaded.created_by === me);
  const mayDelete = bootstrap.role === "admin" || (bootstrap.role === "member" && !!mine);
  const quickActions = useMemo(
    () =>
      loaded && mayDelete ? [{ id: "delete", label: `Delete ${loaded.name}…`, run: () => setDeleting(true) }] : [],
    [loaded, mayDelete]
  );
  useQuickActions(loaded ? { record_type: "campaign", record_id: loaded.id } : null, quickActions);

  if (error) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">{error}</p>
        <Link href={`/crm/${ws}/campaigns`} className="text-sm hover:underline">
          Back to campaigns
        </Link>
      </div>
    );
  }
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const [page, members, board] = data;
  const campaign = page.record;
  const canEdit = bootstrap.role !== "viewer";
  const numberFormat = bootstrap.me.number_format;
  const timeZone = bootstrap.me.timezone;
  const refresh = () => {
    reload();
    setVersion((v) => v + 1);
  };

  const values: Record<string, unknown> = {
    ...(campaign as unknown as Record<string, unknown>),
    money:
      campaign.budget !== undefined
        ? { amount: campaign.budget, currency: campaign.budget_currency ?? "INR" }
        : undefined,
    inr: campaign.budget_in_inr ? { amount: campaign.budget_in_inr, currency: "INR" } : null,
  };

  async function save(name: string, value: unknown) {
    let changes: Record<string, unknown> = { [name]: value };
    if (name === "money") {
      const money = value as { amount: string | null; currency: string } | null;
      changes = money?.amount
        ? { budget: money.amount, budget_currency: money.currency }
        : { budget: null, budget_currency: null };
    }
    await updateCrmRecord(ws, "campaigns", campaign.id, changes, campaign.updated_at);
    refresh();
  }

  const dates = [campaign.starts_on, campaign.ends_on]
    .map((d) => (d ? formatDate(d, timeZone) : null))
    .filter(Boolean)
    .join(" – ");

  return (
    <>
      <RecordPage
        pin={{ recordType: "campaign", id: campaign.id }}
        title={campaign.name}
        subtitle={[campaign.type_label, dates].filter(Boolean).join(" · ") || undefined}
        chips={[
          <Chip key="status" label="Status" value={campaign.status_label ?? "—"} />,
          <Chip key="owner" label="Owner" value={campaign.owner_name ?? "—"} />,
          ...(campaign.budget
            ? [
                <Chip
                  key="budget"
                  label="Budget"
                  value={
                    <AmountText
                      amount={campaign.budget}
                      currency={campaign.budget_currency}
                      inr={campaign.budget_in_inr ?? null}
                      numberFormat={numberFormat}
                    />
                  }
                />,
              ]
            : []),
        ]}
        actions={
          mayDelete && (
            <Button variant="outline" size="sm" onClick={() => setDeleting(true)}>
              Delete
            </Button>
          )
        }
        notice={
          <div className="space-y-4 pt-2">
            <Figures campaign={campaign} numberFormat={numberFormat} />
            <MemberBoard
              bootstrap={bootstrap}
              campaign={campaign}
              board={board}
              canEdit={canEdit}
              onChange={refresh}
            />
          </div>
        }
        groups={campaignGroups(bootstrap, members, campaign)}
        values={values}
        canEdit={canEdit}
        timeZone={timeZone}
        onSave={save}
        timeline={
          <Timeline
            workspace={ws}
            object="campaigns"
            recordId={campaign.id}
            initial={page.timeline_head}
            timeZone={timeZone}
            version={version}
          />
        }
        files={
          <DriveLinks
            workspace={ws}
            recordType="campaign"
            recordId={campaign.id}
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
            link={{ record_type: "campaign", record_id: campaign.id, name: campaign.name }}
            onChange={refresh}
          />
        }
        notes={
          <NotesTab
            workspace={ws}
            canEdit={canEdit}
            members={members}
            notes={page.notes ?? []}
            link={{ record_type: "campaign", record_id: campaign.id }}
            timeZone={timeZone}
            onChange={refresh}
          />
        }
        related={<ConvertedDeals workspace={ws} deals={page.related.opportunities ?? []} numberFormat={numberFormat} />}
      />
      <DeleteCompanyDialog
        open={deleting}
        onOpenChange={setDeleting}
        name={campaign.name}
        peopleCount={null}
        onDelete={async () => {
          await deleteCrmRecord(ws, "campaigns", campaign.id);
          router.push(`/crm/${ws}/campaigns`);
        }}
      />
    </>
  );
}

/** Related: the deals its members converted to (FR-CRM-CAMP-04). */
function ConvertedDeals({
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
      <h3 className="text-sm font-medium">Converted deals ({deals.length})</h3>
      {deals.length === 0 ? (
        <p className="text-sm text-muted-foreground">No member has converted yet.</p>
      ) : (
        <ul className="divide-y rounded-md border">
          {deals.map((deal) => (
            <li key={deal.id} className="flex flex-wrap items-baseline justify-between gap-2 p-2.5">
              <div className="min-w-0">
                <Link href={`/crm/${workspace}/opportunities/${deal.id}`} className="text-sm font-medium hover:underline">
                  {deal.name}
                </Link>
                <p className="text-xs text-muted-foreground">
                  {[deal.company_name, deal.stage_label].filter(Boolean).join(" · ")}
                </p>
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

/** FR-CRM-CAMP-02 — the member board, a kanban by status. */
function MemberBoard({
  bootstrap,
  campaign,
  board,
  canEdit,
  onChange,
}: {
  bootstrap: CrmBootstrap;
  campaign: CrmCampaign;
  board: CampaignMembers;
  canEdit: boolean;
  onChange: () => void;
}) {
  const ws = bootstrap.workspace.key;
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<CrmCampaignMember | null>(null);
  const [converting, setConverting] = useState<CrmCampaignMember | null>(null);
  const settle = useRef<((row: Row | null) => void) | null>(null);
  const statuses = bootstrap.lists["campaign_member.status"] ?? [];
  const columns: KanbanColumn[] = statuses.map((o) => ({ key: o.system_key ?? o.id, label: o.label }));
  const groups: CrmQueryGroup[] = columns.map((c) => ({
    key: c.key,
    label: c.label,
    count: board.member_counts[c.key as MemberStatus] ?? 0,
  }));
  const rows = board.rows as unknown as Row[];

  function convert(row: Row): Promise<Row | null> {
    settle.current?.(null);
    return new Promise((resolve) => {
      settle.current = resolve;
      setConverting(asMember(row));
    });
  }

  function endConvert(row: Row | null) {
    settle.current?.(row);
    settle.current = null;
    setConverting(null);
    if (row) onChange();
  }

  async function move(row: Row, key: string): Promise<Row | null> {
    if (key === "converted") return convert(row);
    const updated = await updateCampaignMember(ws, row.id, { status: key as MemberStatus }, row.updated_at);
    onChange();
    return updated as unknown as Row;
  }

  const hrefFor = (row: Row) => {
    const member = asMember(row);
    if (member.company_id) return `/crm/${ws}/companies/${member.company_id}`;
    return member.person_id ? `/crm/${ws}/people/${member.person_id}` : null;
  };

  return (
    <section className="space-y-3" aria-label="Members">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="flex-1 font-heading text-base font-semibold">Members by status</h2>
        {canEdit && (
          <Button size="sm" onClick={() => setAdding(true)}>
            Add members
          </Button>
        )}
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No members yet. Add companies or people here, or from a list&apos;s bulk bar.
        </p>
      ) : (
        <Kanban
          kind="campaign_member"
          rows={rows}
          field="status"
          columns={columns}
          groups={groups}
          cardFields={["person_company_name", "last_touch_on", "converted_opportunity_id"]}
          canMove={(row) => canEdit && asMember(row).status !== "converted"}
          onMove={move}
          hrefFor={hrefFor}
          detailFor={(row) => {
            const member = asMember(row);
            if (!canEdit) return member.do_not_contact ? "Do not contact" : null;
            return (
              <span className="flex flex-wrap gap-2">
                {member.do_not_contact && <span>Do not contact</span>}
                <button type="button" className="underline" onClick={() => setEditing(member)}>
                  Touch or note
                </button>
                {member.status !== "converted" && (
                  <button type="button" className="underline" onClick={() => void convert(row)}>
                    Convert
                  </button>
                )}
              </span>
            );
          }}
          lists={bootstrap.lists}
          timeZone={bootstrap.me.timezone}
          numberFormat={bootstrap.me.number_format}
        />
      )}
      {canEdit && (
        <AddMembersDialog
          open={adding}
          onOpenChange={setAdding}
          workspace={ws}
          campaign={campaign}
          onAdded={onChange}
        />
      )}
      {canEdit && (
        <EditMemberDialog
          member={editing}
          onClose={() => setEditing(null)}
          workspace={ws}
          bootstrap={bootstrap}
          onSaved={onChange}
        />
      )}
      <ConvertDialog
        open={converting !== null}
        onOpenChange={(open) => !open && endConvert(null)}
        workspace={ws}
        member={converting}
        canSeeAmounts={bootstrap.role !== "viewer"}
        onConverted={(done) => endConvert(done.member as unknown as Row)}
      />
    </section>
  );
}

/** FR-CRM-CAMP-03 — companies and people one at a time, or a saved view's. */
function AddMembersDialog({
  open,
  onOpenChange,
  workspace,
  campaign,
  onAdded,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspace: string;
  campaign: CrmCampaign;
  onAdded: () => void;
}) {
  const [company, setCompany] = useState<string | null>(null);
  const [person, setPerson] = useState<string | null>(null);
  const [view, setView] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<MembersAdded | null>(null);
  const views = useAsync(
    () =>
      open
        ? Promise.all([listCrmViews(workspace, "company"), listCrmViews(workspace, "person")]).then(([a, b]) => [
            ...a,
            ...b,
          ])
        : Promise.resolve([]),
    [workspace, open]
  );

  function close(next: boolean) {
    if (!next) {
      setCompany(null);
      setPerson(null);
      setView("");
      setError(null);
      if (result) onAdded();
      setResult(null);
    }
    onOpenChange(next);
  }

  async function add() {
    setBusy(true);
    setError(null);
    try {
      setResult(
        await addCampaignMembers(workspace, campaign.id, {
          company_ids: company ? [company] : [],
          person_ids: person ? [person] : [],
          ...(view ? { view_id: view } : {}),
        })
      );
      setCompany(null);
      setPerson(null);
      setView("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add members</DialogTitle>
          <DialogDescription>
            Each company or person joins {campaign.name} once. People marked Do not contact are skipped.
          </DialogDescription>
        </DialogHeader>
        <form
          id="members-form"
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            add();
          }}
        >
          {error && (
            <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
              {error}
            </p>
          )}
          {result && (
            <div role="status" className="space-y-1 rounded-md bg-muted p-2 text-sm" data-testid="members-added">
              <p>
                {result.added} added
                {result.skipped.length ? `, ${result.skipped.length} skipped:` : "."}
              </p>
              {result.skipped.length > 0 && (
                <ul className="list-disc pl-4">
                  {result.skipped.map((s) => (
                    <li key={s.id}>{s.reason}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="member-company">Company</Label>
            <FieldEditor
              id="member-company"
              spec={{
                name: "company_id",
                label: "Company",
                type: "relation",
                choices: [],
                search: (q, after) => searchCompanyChoices(workspace, q, after),
              }}
              value={company}
              onChange={(value) => setCompany((value as string | null) ?? null)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="member-person">Person</Label>
            <FieldEditor
              id="member-person"
              spec={{
                name: "person_id",
                label: "Person",
                type: "relation",
                choices: [],
                search: (q, after) => searchPersonChoices(workspace, q, after),
              }}
              value={person}
              onChange={(value) => setPerson((value as string | null) ?? null)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="member-view">Everyone a saved view lists</Label>
            <select
              id="member-view"
              className={`${NATIVE_SELECT} w-full`}
              value={view}
              onChange={(e) => setView(e.target.value)}
            >
              <option value="">None</option>
              {(views.data ?? []).map((v) => (
                <option key={v.id} value={v.id}>
                  {v.object === "company" ? "Companies" : "People"}: {v.name}
                </option>
              ))}
            </select>
          </div>
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => close(false)}>
            {result ? "Done" : "Cancel"}
          </Button>
          <Button type="submit" form="members-form" disabled={busy || !(company || person || view)}>
            {busy ? "Adding…" : "Add"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** FR-CRM-CAMP-02 — a member's status, last touch and notes. */
function EditMemberDialog({
  member,
  onClose,
  workspace,
  bootstrap,
  onSaved,
}: {
  member: CrmCampaignMember | null;
  onClose: () => void;
  workspace: string;
  bootstrap: CrmBootstrap;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<{ id: string; status: string; touch: string; notes: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const current =
    member && draft?.id === member.id
      ? draft
      : member
        ? { id: member.id, status: member.status, touch: member.last_touch_on ?? "", notes: member.notes ?? "" }
        : null;
  const statuses = (bootstrap.lists["campaign_member.status"] ?? []).filter(
    (o) => o.system_key !== "converted" || member?.status === "converted"
  );

  async function save() {
    if (!member || !current) return;
    setBusy(true);
    setError(null);
    try {
      await updateCampaignMember(
        workspace,
        member.id,
        {
          ...(current.status !== member.status ? { status: current.status as MemberStatus } : {}),
          last_touch_on: current.touch || null,
          notes: current.notes.trim() || null,
        },
        member.updated_at
      );
      onSaved();
      setDraft(null);
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={member !== null}
      onOpenChange={(open) => {
        if (!open) {
          setDraft(null);
          setError(null);
          onClose();
        }
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{member?.name ?? "Member"}</DialogTitle>
          <DialogDescription>Sends happen outside the CRM; record what came of them here.</DialogDescription>
        </DialogHeader>
        {current && (
          <form
            id="member-form"
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              save();
            }}
          >
            {error && (
              <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
                {error}
              </p>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="member-status">Status</Label>
              <select
                id="member-status"
                className={`${NATIVE_SELECT} w-full`}
                value={current.status}
                disabled={member?.status === "converted"}
                onChange={(e) => setDraft({ ...current, status: e.target.value })}
              >
                {statuses.map((o) => (
                  <option key={o.id} value={o.system_key ?? o.id}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="member-touch">Last touch</Label>
              <Input
                id="member-touch"
                type="date"
                value={current.touch}
                onChange={(e) => setDraft({ ...current, touch: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="member-notes">Notes</Label>
              <Textarea
                id="member-notes"
                maxLength={20000}
                value={current.notes}
                onChange={(e) => setDraft({ ...current, notes: e.target.value })}
              />
            </div>
          </form>
        )}
        <DialogFooter>
          <Button type="submit" form="member-form" disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
