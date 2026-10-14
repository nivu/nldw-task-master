"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useMemo, useRef, useState } from "react";

import { BulkBar } from "@/components/crm/bulk-bar";
import { ColumnsMenu, DataTable } from "@/components/crm/data-table";
import { FilterBuilder } from "@/components/crm/filter-builder";
import { Kanban, type KanbanColumn } from "@/components/crm/kanban";
import { LostDialog } from "@/components/crm/lost-dialog";
import { useCrm } from "@/components/crm/shell";
import { SortMenu } from "@/components/crm/sort-menu";
import { StageDialog } from "@/components/crm/stage-dialog";
import { ViewSearch } from "@/components/crm/view-search";
import { ViewSwitcher, type SaveAs } from "@/components/crm/view-switcher";
import { Button } from "@/components/ui/button";
import {
  addCrmFavourite,
  createCrmView,
  deleteCrmView,
  getCrmMembers,
  listCrmViews,
  queryCrm,
  removeCrmFavourite,
  updateCrmRecord,
  updateCrmView,
} from "@/lib/api/crm";
import type {
  CrmObject,
  CrmOpportunity,
  CrmRecord,
  CrmSavedView,
  StageKey,
  ViewAggregate,
  ViewColumn,
  ViewConfig,
  ViewObjectKind,
} from "@/lib/api/crm-types";
import {
  choicesFor,
  fieldMeta,
  fieldsFor,
  groupKeyOf,
  KANBAN_TYPES,
  OBJECT_PATH,
  patchFor,
  TITLE_FIELD,
  type ViewFieldMeta,
} from "@/lib/crm/view-fields";
import { errorMessage } from "@/lib/api/portal";
import { useAsync } from "@/lib/use-async";

/**
 * One object's list on its saved views — spec 007 FR-CRM-VIEW-01 to -09,
 * FR-CRM-UI-02; contracts §2.7.
 *
 * The views come from `GET /crm/views`; the one open is named in the URL
 * (`?view=<id>`), so a pinned view is a link. What is on screen — layout,
 * filters, sorts, grouping, columns, totals — starts as the saved view and
 * can be changed, saved back (by whoever may edit it) or saved as a new
 * view. Every page of rows is one `POST /crm/query` carrying the whole of
 * what is on screen, so the server filters, sorts, groups, totals and pages
 * it (FR-CRM-VIEW-10). A Viewer opening a shared view with amounts in it
 * gets it without them, and the notice saying so (Q-67).
 *
 * Edits from a cell or a card are the record's own PATCH with its
 * `updated_at`; a deal's stage goes through the stage form, the lost form or
 * the handoff, exactly as from the deal's page.
 */

type Row = Record<string, unknown> & { id: string; updated_at: string };

interface Draft {
  viewId: string;
  kind: "table" | "kanban";
  config: ViewConfig;
}

const PAGE = { table: 50, kanban: 200 } as const;

/** What a list shows when it has no saved view (People has none seeded). */
const DEFAULT_COLUMNS: Record<ViewObjectKind, string[]> = {
  company: ["name", "domain", "lead_stage", "account_type_id", "owner_id", "updated_at"],
  person: ["name", "company_id", "primary_email", "job_title", "owner_id", "updated_at"],
  opportunity: ["name", "company_id", "stage", "amount", "next_step", "next_step_on", "owner_id"],
  task: ["title", "status", "due_on", "bucket", "assignee_id"],
  note: ["body", "kind", "occurred_on", "created_by"],
};

/** A deal kanban's headers: the count, the INR sum and the weighted sum. */
const DEAL_TOTALS: ViewAggregate[] = [
  { fn: "count" },
  { fn: "sum", field: "amount_in_inr" },
  { fn: "sum", field: "weighted_amount" },
];

function columnsOf(config: ViewConfig, kind: ViewObjectKind, shown: ViewFieldMeta[]): ViewColumn[] {
  const allowed = new Set(shown.map((f) => f.field));
  const raw = config.columns?.length ? config.columns : DEFAULT_COLUMNS[kind];
  return raw
    .map((c) => (typeof c === "string" ? { field: c } : c))
    .filter((c) => allowed.has(c.field));
}

function draftOf(view: CrmSavedView | null): Draft {
  return {
    viewId: view?.id ?? "",
    kind: view?.kind ?? "table",
    config: view?.config ?? { sorts: [{ field: "updated_at", dir: "desc" }] },
  };
}

/** A view's config with nothing undefined, for saving and comparing. */
function configOf(draft: Draft): ViewConfig {
  const c = draft.config;
  const out: ViewConfig = {
    filters: c.filters ?? { op: "and", items: [] },
    sorts: c.sorts ?? [],
    ...(c.columns ? { columns: c.columns } : {}),
    ...(c.aggregates ? { aggregates: c.aggregates } : {}),
    ...(c.card_fields ? { card_fields: c.card_fields } : {}),
  };
  if (draft.kind === "table" && c.group_by) out.group_by = c.group_by;
  if (c.kanban_field) out.kanban_field = c.kanban_field;
  return out;
}

export function ObjectList(props: {
  kind: ViewObjectKind;
  title: string;
  /** Buttons beside the title (New company, …). */
  actions?: React.ReactNode;
  /** Open this seeded view only, without the view tabs (Pipeline). */
  lockedView?: string;
  /** Changes when a record is created elsewhere on the page. */
  refreshKey?: number;
}) {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <ObjectListView {...props} />
    </Suspense>
  );
}

function ObjectListView({
  kind,
  title,
  actions,
  lockedView,
  refreshKey = 0,
}: {
  kind: ViewObjectKind;
  title: string;
  actions?: React.ReactNode;
  lockedView?: string;
  refreshKey?: number;
}) {
  const { bootstrap, favourites, reloadFavourites } = useCrm();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const ws = bootstrap.workspace.key;
  const viewer = bootstrap.role === "viewer";
  const tvaLinked = bootstrap.workspace.tva_linked;
  const timeZone = bootstrap.me.timezone;
  const numberFormat = bootstrap.me.number_format;

  // Fields: a Viewer filters on what the server will refuse with its own
  // sentence (FR-CRM-ACC-11), but is never shown or offered an amount to
  // see, sort or total.
  const visibleFields = useMemo(() => fieldsFor(kind, { tvaLinked, viewer }), [kind, tvaLinked, viewer]);
  const filterFields = useMemo(() => fieldsFor(kind, { tvaLinked, viewer: false }), [kind, tvaLinked]);
  const kanbanFields = visibleFields.filter((f) => KANBAN_TYPES.includes(f.type) && f.groupable);

  const [viewsNonce, setViewsNonce] = useState(0);
  const setup = useAsync(
    () => Promise.all([listCrmViews(ws, kind), getCrmMembers(ws)]),
    [ws, kind, viewsNonce]
  );
  const views = setup.data?.[0] ?? [];
  const members = setup.data?.[1] ?? [];

  const wanted = lockedView ?? params.get("view");
  const current =
    views.find((v) => v.id === wanted || (wanted && v.system_key === wanted)) ??
    views.find((v) => v.is_default) ??
    views[0] ??
    null;

  const [draftState, setDraft] = useState<Draft | null>(null);
  const draft = draftState && draftState.viewId === (current?.id ?? "") ? draftState : draftOf(current);
  const dirty = JSON.stringify(configOf(draft)) !== JSON.stringify(configOf(draftOf(current))) ||
    draft.kind !== (current?.kind ?? "table");
  const change = (patch: Partial<Omit<Draft, "viewId">>) =>
    setDraft({ ...draft, ...patch, viewId: current?.id ?? "" });
  const changeConfig = (patch: Partial<ViewConfig>) => change({ config: { ...draft.config, ...patch } });

  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [queryNonce, setQueryNonce] = useState(0);
  const [extra, setExtra] = useState<Row[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [saved, setSaved] = useState<Record<string, Row>>({});

  const kanbanField =
    draft.kind === "kanban"
      ? (draft.config.kanban_field ?? kanbanFields[0]?.field)
      : undefined;
  const spec = {
    filters: draft.config.filters,
    sorts: draft.config.sorts,
    group_by: draft.kind === "kanban" ? kanbanField : draft.config.group_by,
    aggregates:
      draft.config.aggregates ??
      (draft.kind === "kanban" && kind === "opportunity" && !viewer ? DEAL_TOTALS : undefined),
    q: q || undefined,
    limit: PAGE[draft.kind],
  };
  const specKey = JSON.stringify(spec);

  const result = useAsync(async () => {
    if (!setup.data) return null;
    const page = await queryCrm<CrmRecord>(ws, kind, JSON.parse(specKey));
    setExtra([]);
    setSaved({});
    setNext(page.next);
    return page;
  }, [ws, kind, specKey, Boolean(setup.data), queryNonce, refreshKey]);
  const page = result.data;
  const rows = useMemo(
    () =>
      [...((page?.rows ?? []) as unknown as Row[]), ...extra].map((row) => saved[row.id] ?? row),
    [page, extra, saved]
  );

  async function more() {
    if (!next) return;
    try {
      const further = await queryCrm<CrmRecord>(ws, kind, { ...JSON.parse(specKey), after: next });
      setExtra((current) => [...current, ...(further.rows as unknown as Row[])]);
      setNext(further.next);
    } catch (err) {
      result.setError(errorMessage(err));
    }
  }

  const keep = useCallback((row: Row) => setSaved((s) => ({ ...s, [row.id]: row })), []);

  // -------------------------------------------------------------------------
  // Edits: a cell, a card, a deal's stage
  // -------------------------------------------------------------------------

  const canEditCell = (row: Row, meta: ViewFieldMeta) =>
    !viewer || (kind === "task" && meta.field === "status" && row.assignee_id === bootstrap.me.id);

  async function edit(row: Row, meta: ViewFieldMeta, value: unknown) {
    const updated = await updateCrmRecord<CrmRecord>(
      ws,
      OBJECT_PATH[kind] as CrmObject,
      row.id,
      patchFor(meta, value),
      row.updated_at
    );
    keep(updated as unknown as Row);
  }

  const [moving, setMoving] = useState<{ deal: CrmOpportunity; stage: StageKey } | null>(null);
  const settle = useRef<((row: Row | null) => void) | null>(null);

  function moveStage(row: Row, stage: string): Promise<Row | null> {
    settle.current?.(null);
    return new Promise((resolve) => {
      settle.current = resolve;
      setMoving({ deal: row as unknown as CrmOpportunity, stage: stage as StageKey });
    });
  }

  function endMove(row: Row | null) {
    if (row) keep(row);
    settle.current?.(row);
    settle.current = null;
    setMoving(null);
  }

  async function moveCard(row: Row, key: string): Promise<Row | null> {
    if (!kanbanField) return null;
    if (kind === "opportunity" && kanbanField === "stage") return moveStage(row, key);
    const meta = fieldMeta(kind, kanbanField)!;
    const updated = await updateCrmRecord<CrmRecord>(
      ws,
      OBJECT_PATH[kind] as CrmObject,
      row.id,
      { [meta.field]: key === "" ? null : key },
      row.updated_at
    );
    keep(updated as unknown as Row);
    return updated as unknown as Row;
  }

  const stageLabel = (stage: string) =>
    (bootstrap.lists["opportunity.stage"] ?? []).find((o) => o.system_key === stage)?.label ?? stage;

  // -------------------------------------------------------------------------
  // Saved views
  // -------------------------------------------------------------------------

  function select(view: CrmSavedView) {
    setDraft(null);
    setSelected(new Set());
    router.replace(`${pathname}?view=${view.id}`, { scroll: false });
  }

  async function save() {
    if (!current) return;
    await updateCrmView(ws, current.id, {
      config: configOf(draft),
      ...(draft.kind !== current.kind && !current.system_key ? { kind: draft.kind } : {}),
    });
    setDraft(null);
    setViewsNonce((n) => n + 1);
  }

  async function saveAs(made: SaveAs) {
    const view = await createCrmView(ws, {
      object: kind,
      name: made.name,
      icon: made.icon,
      kind: draft.kind,
      visibility: made.visibility,
      config: { ...configOf(draft), ...(draft.kind === "kanban" ? { kanban_field: kanbanField } : {}) },
    });
    if (made.is_default) await updateCrmView(ws, view.id, { is_default: true });
    setDraft(null);
    setViewsNonce((n) => n + 1);
    reloadFavourites();
    router.replace(`${pathname}?view=${view.id}`, { scroll: false });
  }

  async function moveView(view: CrmSavedView, by: -1 | 1) {
    const i = views.findIndex((v) => v.id === view.id);
    const other = views[i + by];
    if (!other) return;
    // Swap the two positions; equal ones are pulled apart first.
    const [a, b] =
      view.position === other.position ? [other.position + by, other.position] : [other.position, view.position];
    await updateCrmView(ws, view.id, { position: Math.max(0, a) });
    await updateCrmView(ws, other.id, { position: Math.max(0, b) });
    setViewsNonce((n) => n + 1);
  }

  async function makeDefault(view: CrmSavedView) {
    await updateCrmView(ws, view.id, { is_default: true });
    setViewsNonce((n) => n + 1);
  }

  async function removeView(view: CrmSavedView) {
    await deleteCrmView(ws, view.id);
    setDraft(null);
    setViewsNonce((n) => n + 1);
    reloadFavourites();
    router.replace(pathname, { scroll: false });
  }

  const pin = favourites.find((f) => f.target_type === "view" && f.target_id === current?.id);
  async function togglePin() {
    if (!current) return;
    if (pin) await removeCrmFavourite(ws, pin.id);
    else await addCrmFavourite(ws, { target_type: "view", target_id: current.id });
    reloadFavourites();
  }

  // -------------------------------------------------------------------------

  const columns = columnsOf(draft.config, kind, visibleFields);
  const kanbanMeta = kanbanField ? fieldMeta(kind, kanbanField) : undefined;
  const kanbanColumns: KanbanColumn[] = useMemo(() => {
    if (!kanbanMeta) return [];
    const present = new Set(rows.map((r) => groupKeyOf(r, kanbanMeta.field)));
    for (const g of page?.groups ?? []) present.add(g.key === null ? "" : String(g.key));
    const out = choicesFor(kanbanMeta, bootstrap)
      .filter((c) => !c.inactive || present.has(c.value))
      .map((c) => ({ key: c.value, label: c.label }));
    if (present.has("")) out.push({ key: "", label: "None" });
    return out;
  }, [kanbanMeta, rows, page, bootstrap]);
  const hrefFor = (row: Row) => `/crm/${ws}/${OBJECT_PATH[kind]}/${row.id}`;
  const bulkFields = visibleFields.filter(
    (f) => f.editable && f.field !== TITLE_FIELD[kind] && f.type !== "long_text"
  );
  const notice = page?.notice ?? current?.notice;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="flex-1 font-heading text-lg font-semibold">{title}</h1>
        {actions}
      </div>

      {setup.error && <p className="text-sm text-destructive">{setup.error}</p>}
      {!setup.data && !setup.error && <p className="text-sm text-muted-foreground">Loading…</p>}

      {setup.data && (
        <>
          {!lockedView && (
            <ViewSwitcher
              views={views}
              current={current}
              onSelect={select}
              kind={draft.kind}
              onKindChange={(k) =>
                k === "kanban" && kanbanFields.length === 0
                  ? undefined
                  : change({
                      kind: k,
                      config:
                        k === "kanban" && !draft.config.kanban_field
                          ? { ...draft.config, kanban_field: kanbanFields[0]?.field }
                          : draft.config,
                    })
              }
              canKanban={kanbanFields.length > 0}
              dirty={dirty}
              canShare={!viewer}
              pinned={Boolean(pin)}
              onSave={save}
              onSaveAs={saveAs}
              onMove={moveView}
              onDefault={makeDefault}
              onRemove={removeView}
              onPin={togglePin}
              onReset={() => setDraft(null)}
            />
          )}

          <div className="flex flex-wrap items-center gap-1.5">
            <ViewSearch value={q} onChange={setQ} label={`Search ${title.toLowerCase()}`} />
            <FilterBuilder
              value={draft.config.filters}
              onChange={(filters) => changeConfig({ filters })}
              fields={filterFields}
              bootstrap={bootstrap}
              members={members}
            />
            <SortMenu
              sorts={draft.config.sorts ?? []}
              onSortsChange={(sorts) => changeConfig({ sorts })}
              fields={visibleFields}
              groupBy={draft.kind === "kanban" ? kanbanField : (draft.config.group_by ?? null)}
              onGroupByChange={(field) =>
                draft.kind === "kanban"
                  ? field && changeConfig({ kanban_field: field })
                  : changeConfig({ group_by: field ?? undefined })
              }
              groupFields={draft.kind === "kanban" ? kanbanFields : undefined}
              groupLabel={draft.kind === "kanban" ? "Columns by" : undefined}
            />
            {draft.kind === "table" && (
              <ColumnsMenu
                columns={columns}
                onChange={(next) => changeConfig({ columns: next })}
                fields={visibleFields}
                titleField={TITLE_FIELD[kind]}
              />
            )}
            {lockedView && dirty && (
              <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>
                Reset
              </Button>
            )}
            {page && (
              <span className="ml-auto text-xs text-muted-foreground" aria-live="polite">
                {page.total === "10000+" ? "10,000+" : page.total} {page.total === 1 ? "record" : "records"}
              </span>
            )}
          </div>

          {notice && (
            <p role="status" className="rounded-md bg-muted p-2 text-sm" data-testid="view-notice">
              {notice}. Viewers do not see amounts, so this view opens without them.
            </p>
          )}
          {result.error && (
            <p
              role="alert"
              className="rounded-md bg-destructive/10 p-2 text-sm text-destructive"
              data-testid="view-error"
            >
              {result.error}
            </p>
          )}
          {!page && !result.error && <p className="text-sm text-muted-foreground">Loading…</p>}

          {page && draft.kind === "table" && (
            <DataTable
              kind={kind}
              rows={rows}
              columns={columns}
              onColumnsChange={(next) => changeConfig({ columns: next })}
              groupBy={page.group_by}
              groups={page.groups}
              aggregates={draft.config.aggregates ?? []}
              onAggregatesChange={(aggregates) => changeConfig({ aggregates })}
              totals={page.aggregates}
              total={page.total}
              selectable={!viewer}
              selected={selected}
              onSelectedChange={setSelected}
              canEditCell={canEditCell}
              onEdit={edit}
              onStage={kind === "opportunity" ? (row, stage) => void moveStage(row, stage) : undefined}
              hrefFor={hrefFor}
              bootstrap={bootstrap}
              members={members}
              timeZone={timeZone}
              numberFormat={numberFormat}
            />
          )}
          {page && draft.kind === "kanban" && kanbanField && (
            <Kanban
              kind={kind}
              rows={rows}
              field={kanbanField}
              columns={kanbanColumns}
              groups={page.groups}
              cardFields={draft.config.card_fields ?? columns.map((c) => c.field).slice(1, 4)}
              canMove={(row) => Boolean(kanbanMeta && canEditCell(row, kanbanMeta))}
              onMove={moveCard}
              hrefFor={hrefFor}
              timeZone={timeZone}
              numberFormat={numberFormat}
            />
          )}
          {next && (
            <Button variant="outline" onClick={more}>
              Load more
            </Button>
          )}

          {!viewer && (
            <BulkBar
              kind={kind}
              selected={[...selected]}
              rows={rows}
              fields={bulkFields}
              bootstrap={bootstrap}
              members={members}
              onClear={() => setSelected(new Set())}
              onDone={() => setQueryNonce((n) => n + 1)}
            />
          )}
        </>
      )}

      {moving && moving.stage !== "closed_lost" && (
        <StageDialog
          open
          onOpenChange={(open) => !open && endMove(null)}
          workspace={ws}
          deal={moving.deal}
          stage={moving.stage}
          stageLabel={stageLabel(moving.stage)}
          numberFormat={numberFormat}
          labs={tvaLinked}
          onMoved={(moved) => endMove(moved.opportunity as unknown as Row)}
        />
      )}
      {moving && moving.stage === "closed_lost" && (
        <LostDialog
          open
          onOpenChange={(open) => !open && endMove(null)}
          workspace={ws}
          deal={moving.deal}
          reasons={bootstrap.lists["opportunity.lost_reason"] ?? []}
          onMoved={(moved) => endMove(moved.opportunity as unknown as Row)}
        />
      )}
    </div>
  );
}
