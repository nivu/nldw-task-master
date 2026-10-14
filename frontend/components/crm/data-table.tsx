"use client";

import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type ColumnSizingState,
  type Updater,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, Columns3, Pencil } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { cleanValue, FieldEditor } from "@/components/crm/field-editor";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type {
  AggregateFn,
  CrmBootstrap,
  CrmMember,
  CrmQueryGroup,
  CrmTotals,
  ViewAggregate,
  ViewColumn,
  ViewObjectKind,
} from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { formatAmount, type CrmTimezone, type NumberFormat } from "@/lib/crm/format";
import {
  cellText,
  choicesFor,
  editorSpec,
  editorValue,
  fieldLabel,
  fieldMeta,
  groupKeyOf,
  NATIVE_SELECT,
  relationSearch,
  TITLE_FIELD,
  type ViewFieldMeta,
} from "@/lib/crm/view-fields";
import { cn } from "@/lib/utils";

/**
 * The table view — spec 007 FR-CRM-VIEW-01, -03, -04, -06, -08, -09; NFR-CRM-06.
 *
 * One server page of rows (`POST /crm/query`) in the view's columns, which
 * the person chooses, reorders and resizes (drag a header's edge, or focus
 * it and use the arrow keys). Cells edit in place through `field-editor`,
 * each save the same PATCH a record page sends, so permissions and pipeline
 * rules hold; a deal's stage opens the stage form instead. Rows can be
 * selected for the bulk bar. Grouped rows arrive in group order with every
 * group's count and totals from the server; groups collapse. The footer
 * shows the count and the totals chosen per column — never an amount for a
 * Viewer, whose columns have none.
 *
 * Keyboard: Up and Down move between rows, Enter opens the record, Space
 * selects it, Escape clears the selection or closes an open editor.
 */

type Row = Record<string, unknown> & { id: string; updated_at: string };

const DEFAULT_WIDTH = 160;
const MIN_WIDTH = 40;
const MAX_WIDTH = 800;
const clamp = (n: number) => Math.round(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, n)));

const FN_LABEL: Record<AggregateFn, string> = {
  count: "Count",
  sum: "Sum",
  avg: "Average",
  min: "Min",
  max: "Max",
};

/** A total as it reads: INR amounts in the viewer's format, numbers plain. */
export function totalText(
  kind: ViewObjectKind,
  key: string,
  value: string | number | null | undefined,
  numberFormat: NumberFormat
): string {
  if (value === null || value === undefined) return "—";
  const field = key.slice(key.indexOf("_") + 1);
  const meta = key === "count" ? undefined : fieldMeta(kind, field);
  if (meta?.type === "currency" && field !== "amount") return formatAmount(String(value), "INR", numberFormat);
  const n = Number(value);
  return Number.isFinite(n) ? new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(n) : String(value);
}

export interface DataTableProps {
  kind: ViewObjectKind;
  rows: Row[];
  columns: ViewColumn[];
  onColumnsChange: (columns: ViewColumn[]) => void;
  groupBy?: string | null;
  groups?: CrmQueryGroup[];
  aggregates: ViewAggregate[];
  onAggregatesChange: (aggregates: ViewAggregate[]) => void;
  totals?: CrmTotals;
  total: number | "10000+";
  selectable: boolean;
  selected: Set<string>;
  onSelectedChange: (selected: Set<string>) => void;
  canEditCell: (row: Row, meta: ViewFieldMeta) => boolean;
  onEdit: (row: Row, meta: ViewFieldMeta, value: unknown) => Promise<void>;
  /** A deal's stage moves through the stage form (contracts §2.6). */
  onStage?: (row: Row, stage: string) => void;
  hrefFor: (row: Row) => string;
  bootstrap: CrmBootstrap;
  members: CrmMember[];
  timeZone: CrmTimezone;
  numberFormat: NumberFormat;
}

export function DataTable(props: DataTableProps) {
  const {
    kind,
    rows,
    columns,
    onColumnsChange,
    groupBy,
    groups,
    selectable,
    selected,
    onSelectedChange,
    hrefFor,
  } = props;
  const router = useRouter();
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [active, setActive] = useState<string | null>(null);

  const sizing: ColumnSizingState = useMemo(
    () => Object.fromEntries(columns.map((c) => [c.field, c.width ?? DEFAULT_WIDTH])),
    [columns]
  );

  const defs = useMemo<ColumnDef<Row>[]>(
    () =>
      columns.map((c) => ({
        id: c.field,
        header: fieldLabel(kind, c.field),
        size: c.width ?? DEFAULT_WIDTH,
        minSize: MIN_WIDTH,
        maxSize: MAX_WIDTH,
      })),
    [columns, kind]
  );

  // TanStack Table hands back functions the React Compiler cannot memoize,
  // so the compiler leaves this component as written; nothing memoized
  // receives the table.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: rows,
    columns: defs,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => row.id,
    columnResizeMode: "onEnd",
    state: { columnSizing: sizing },
    onColumnSizingChange: (updater: Updater<ColumnSizingState>) => {
      const next = typeof updater === "function" ? updater(sizing) : updater;
      onColumnsChange(columns.map((c) => ({ ...c, width: clamp(next[c.field] ?? c.width ?? DEFAULT_WIDTH) })));
    },
  });

  // Rows in the order shown: by group (the server sends them so), the
  // collapsed groups' rows left out.
  const sections = useMemo(() => {
    if (!groupBy || !groups) return [{ group: null as CrmQueryGroup | null, rows }];
    const byKey = new Map<string, Row[]>();
    for (const row of rows) {
      const key = groupKeyOf(row, groupBy);
      byKey.set(key, [...(byKey.get(key) ?? []), row]);
    }
    const out: { group: CrmQueryGroup | null; rows: Row[] }[] = groups.map((group) => {
      const key = group.key === null ? "" : String(group.key);
      const found = byKey.get(key) ?? [];
      byKey.delete(key);
      return { group, rows: found };
    });
    for (const [, rest] of byKey) out.push({ group: null, rows: rest });
    return out;
  }, [rows, groupBy, groups]);

  const visible = sections.flatMap((s) =>
    s.group && collapsed.has(String(s.group.key)) ? [] : s.rows
  );
  const focusId = active && visible.some((r) => r.id === active) ? active : visible[0]?.id;

  function focusRow(id: string | undefined) {
    if (!id) return;
    setActive(id);
    document.querySelector<HTMLElement>(`[data-row-id="${CSS.escape(id)}"]`)?.focus();
  }

  function onRowKey(e: React.KeyboardEvent<HTMLTableRowElement>, row: Row) {
    if (e.target !== e.currentTarget) return;
    const i = visible.findIndex((r) => r.id === row.id);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      focusRow(visible[i + 1]?.id);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      focusRow(visible[i - 1]?.id);
    } else if (e.key === "Enter") {
      e.preventDefault();
      router.push(hrefFor(row));
    } else if (e.key === " " && selectable) {
      e.preventDefault();
      toggle(row.id);
    } else if (e.key === "Escape" && selected.size > 0) {
      onSelectedChange(new Set());
    }
  }

  function toggle(id: string) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onSelectedChange(next);
  }

  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.id));
  const width = columns.reduce((n, c) => n + (c.width ?? DEFAULT_WIDTH), selectable ? 40 : 0);
  const span = columns.length + (selectable ? 1 : 0);

  return (
    <div className="relative overflow-x-auto rounded-md border" data-testid="crm-table">
      <table className="table-fixed text-sm" style={{ width, minWidth: "100%" }}>
        <colgroup>
          {selectable && <col style={{ width: 40 }} />}
          {table.getVisibleLeafColumns().map((col) => (
            <col key={col.id} style={{ width: col.getSize() }} />
          ))}
        </colgroup>
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id} className="border-b text-left text-xs text-muted-foreground">
              {selectable && (
                <th className="p-2">
                  <Checkbox
                    aria-label="Select all rows shown"
                    checked={allSelected}
                    onCheckedChange={(checked) =>
                      onSelectedChange(checked ? new Set(rows.map((r) => r.id)) : new Set())
                    }
                  />
                </th>
              )}
              {hg.headers.map((header) => (
                <th key={header.id} className="relative truncate p-2 font-medium">
                  {flexRender(header.column.columnDef.header, header.getContext())}
                  <span
                    role="separator"
                    aria-orientation="vertical"
                    aria-label={`Resize ${fieldLabel(kind, header.id)}`}
                    aria-valuenow={header.getSize()}
                    aria-valuemin={MIN_WIDTH}
                    aria-valuemax={MAX_WIDTH}
                    tabIndex={0}
                    onMouseDown={header.getResizeHandler()}
                    onTouchStart={header.getResizeHandler()}
                    onKeyDown={(e) => {
                      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
                      e.preventDefault();
                      const by = e.key === "ArrowLeft" ? -20 : 20;
                      onColumnsChange(
                        columns.map((c) =>
                          c.field === header.id ? { ...c, width: clamp(header.getSize() + by) } : c
                        )
                      );
                    }}
                    className={cn(
                      "absolute top-0 right-0 h-full w-1.5 cursor-col-resize touch-none select-none bg-transparent hover:bg-border focus-visible:bg-ring focus-visible:outline-none",
                      header.column.getIsResizing() && "bg-ring"
                    )}
                    style={
                      header.column.getIsResizing()
                        ? { transform: `translateX(${table.getState().columnSizingInfo.deltaOffset ?? 0}px)` }
                        : undefined
                    }
                  />
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={span} className="p-4 text-center text-muted-foreground">
                Nothing matches this view.
              </td>
            </tr>
          )}
          {sections.map((section, s) => {
            const key = section.group ? String(section.group.key) : `rest-${s}`;
            const shut = section.group !== null && collapsed.has(key);
            return (
              <SectionRows
                key={key}
                {...props}
                group={section.group}
                rows={shut ? [] : section.rows}
                shut={shut}
                onToggleGroup={() => {
                  const next = new Set(collapsed);
                  if (next.has(key)) next.delete(key);
                  else next.add(key);
                  setCollapsed(next);
                }}
                span={span}
                focusId={focusId}
                onRowKey={onRowKey}
                onRowFocus={setActive}
                onToggleRow={toggle}
              />
            );
          })}
        </tbody>
        <Footer {...props} />
      </table>
    </div>
  );
}

function SectionRows({
  group,
  rows,
  shut,
  onToggleGroup,
  span,
  focusId,
  onRowKey,
  onRowFocus,
  onToggleRow,
  ...props
}: DataTableProps & {
  group: CrmQueryGroup | null;
  shut: boolean;
  onToggleGroup: () => void;
  span: number;
  focusId: string | undefined;
  onRowKey: (e: React.KeyboardEvent<HTMLTableRowElement>, row: Row) => void;
  onRowFocus: (id: string) => void;
  onToggleRow: (id: string) => void;
}) {
  const { kind, columns, selectable, selected, aggregates, numberFormat } = props;
  const sums = aggregates.filter((a) => a.fn !== "count" && a.field);
  return (
    <>
      {group && (
        <tr className="border-b bg-muted/40">
          <td colSpan={span} className="p-0">
            <button
              type="button"
              aria-expanded={!shut}
              onClick={onToggleGroup}
              className="flex w-full flex-wrap items-center gap-x-3 gap-y-0.5 px-2 py-1.5 text-left text-xs"
            >
              {shut ? <ChevronRight className="size-3.5" /> : <ChevronDown className="size-3.5" />}
              <span className="font-medium text-foreground">{group.label ?? "None"}</span>
              <span className="text-muted-foreground">{group.count}</span>
              {sums.map((a) => {
                const key = `${a.fn}_${a.field}`;
                if (!group.aggregates || !(key in group.aggregates)) return null;
                return (
                  <span key={key} className="text-muted-foreground">
                    {FN_LABEL[a.fn]} {fieldLabel(kind, a.field!)}:{" "}
                    {totalText(kind, key, group.aggregates[key], numberFormat)}
                  </span>
                );
              })}
            </button>
          </td>
        </tr>
      )}
      {rows.map((row) => (
        <tr
          key={row.id}
          data-row-id={row.id}
          tabIndex={row.id === focusId ? 0 : -1}
          aria-selected={selectable ? selected.has(row.id) : undefined}
          onFocus={(e) => e.target === e.currentTarget && onRowFocus(row.id)}
          onKeyDown={(e) => onRowKey(e, row)}
          className={cn(
            "border-b last:border-b-0 hover:bg-muted/40 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring",
            selected.has(row.id) && "bg-muted/60"
          )}
        >
          {selectable && (
            <td className="p-2">
              <Checkbox
                aria-label={`Select ${String(row[TITLE_FIELD[kind]] ?? "row")}`}
                checked={selected.has(row.id)}
                onCheckedChange={() => onToggleRow(row.id)}
              />
            </td>
          )}
          {columns.map((c) => (
            <td key={c.field} className="truncate p-0 align-top">
              <Cell {...props} row={row} field={c.field} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

function Cell({
  kind,
  row,
  field,
  canEditCell,
  onEdit,
  onStage,
  hrefFor,
  bootstrap,
  members,
  timeZone,
  numberFormat,
}: Omit<DataTableProps, "rows"> & { row: Row; field: string }) {
  const meta = fieldMeta(kind, field);
  const text = cellText(kind, field, row, { timeZone, numberFormat });
  const editable = Boolean(meta?.editable && canEditCell(row, meta));

  if (field === TITLE_FIELD[kind]) {
    return (
      <div className="group flex items-center gap-1 p-2">
        <Link href={hrefFor(row)} tabIndex={-1} className="truncate font-medium hover:underline">
          {kind === "note" ? text.slice(0, 80) : text}
        </Link>
        {editable && meta && (
          <CellEditor {...{ kind, row, meta, onEdit, bootstrap, members }} label={text}>
            <Pencil className="size-3.5" />
          </CellEditor>
        )}
      </div>
    );
  }
  if (!editable || !meta) return <div className="truncate p-2">{text}</div>;
  if (kind === "opportunity" && field === "stage" && onStage) {
    return (
      <select
        aria-label={`Move ${String(row.name)} to stage`}
        className="h-full w-full cursor-pointer truncate bg-transparent p-2 text-sm hover:bg-muted/60"
        value={String(row.stage)}
        onChange={(e) => onStage(row, e.target.value)}
      >
        {choicesFor(meta, bootstrap).map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </select>
    );
  }
  return (
    <CellEditor {...{ kind, row, meta, onEdit, bootstrap, members }} label={text} wide>
      <span className="truncate">{text}</span>
    </CellEditor>
  );
}

/** A cell's editor in a popover; Escape or Cancel closes it unsaved. */
function CellEditor({
  kind,
  row,
  meta,
  onEdit,
  bootstrap,
  members,
  label,
  wide,
  children,
}: {
  kind: ViewObjectKind;
  row: Row;
  meta: ViewFieldMeta;
  onEdit: DataTableProps["onEdit"];
  bootstrap: CrmBootstrap;
  members: CrmMember[];
  label: string;
  wide?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const spec = editorSpec(meta, row, bootstrap, members, relationSearch(meta.field, bootstrap.workspace.key));
  const id = `cell-${row.id}-${meta.field}`;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await onEdit(row, meta, cleanValue(spec.type, draft));
      setOpen(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setDraft(editorValue(meta, row));
          setError(null);
        }
        setOpen(next);
      }}
    >
      <PopoverTrigger
        aria-label={`Edit ${meta.label} of ${String(row[TITLE_FIELD[kind]] ?? "this record").slice(0, 60)}`}
        title={wide ? label : undefined}
        className={cn(
          "text-left",
          wide
            ? "flex h-full w-full items-center p-2 hover:bg-muted/60 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
            : "shrink-0 rounded p-0.5 text-muted-foreground opacity-60 hover:opacity-100 focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100"
        )}
      >
        {children}
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(22rem,calc(100vw-2rem))]">
        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <label htmlFor={id} className="text-xs text-muted-foreground">
            {meta.label}
          </label>
          <FieldEditor id={id} spec={spec} value={draft} onChange={setDraft} />
          {error && (
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-1.5">
            <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={busy}>
              {busy ? "Saving…" : "Save"}
            </Button>
          </div>
        </form>
      </PopoverContent>
    </Popover>
  );
}

function Footer({
  kind,
  columns,
  selectable,
  aggregates,
  onAggregatesChange,
  totals,
  total,
  numberFormat,
}: DataTableProps) {
  const chosen = (field: string) => aggregates.find((a) => a.field === field && a.fn !== "count")?.fn ?? "";
  function choose(field: string, fn: string) {
    const rest = aggregates.filter((a) => a.field !== field || a.fn === "count");
    onAggregatesChange(fn ? [...rest, { fn: fn as AggregateFn, field }] : rest);
  }
  return (
    <tfoot>
      <tr className="border-t bg-muted/20 text-xs text-muted-foreground">
        {selectable && <td />}
        {columns.map((c, i) => {
          const meta = fieldMeta(kind, c.field);
          const fn = chosen(c.field);
          const key = `${fn}_${c.field}`;
          return (
            <td key={c.field} className="p-2 align-top">
              {i === 0 && (
                <p className="font-medium text-foreground" data-testid="table-count">
                  {total === "10000+" ? "10,000+" : total} {total === 1 ? "record" : "records"}
                </p>
              )}
              {meta?.aggregatable && (
                <div className="flex flex-wrap items-center gap-1">
                  <select
                    aria-label={`Total of ${meta.label}`}
                    className={cn(NATIVE_SELECT, "h-6 px-1 text-xs")}
                    value={fn}
                    onChange={(e) => choose(c.field, e.target.value)}
                  >
                    <option value="">No total</option>
                    {(["sum", "avg", "min", "max"] as const).map((f) => (
                      <option key={f} value={f}>
                        {FN_LABEL[f]}
                      </option>
                    ))}
                  </select>
                  {fn && totals && key in totals && (
                    <span className="font-medium text-foreground">
                      {totalText(kind, key, totals[key], numberFormat)}
                    </span>
                  )}
                </div>
              )}
            </td>
          );
        })}
      </tr>
    </tfoot>
  );
}

/**
 * Choose, reorder and size columns — FR-CRM-VIEW-04. Up and down buttons
 * reorder from the keyboard; widths change by dragging a header's edge.
 */
export function ColumnsMenu({
  columns,
  onChange,
  fields,
  titleField,
}: {
  columns: ViewColumn[];
  onChange: (columns: ViewColumn[]) => void;
  /** Every field this person may show; for a Viewer, none is an amount. */
  fields: ViewFieldMeta[];
  titleField: string;
}) {
  const shown = new Set(columns.map((c) => c.field));
  const hidden = fields.filter((f) => !shown.has(f.field) && f.field !== "position");
  const move = (i: number, by: number) => {
    const next = [...columns];
    const [taken] = next.splice(i, 1);
    next.splice(i + by, 0, taken);
    onChange(next);
  };
  return (
    <Popover>
      <PopoverTrigger render={<Button variant="outline" size="sm" />}>
        <Columns3 className="size-3.5" />
        Columns
      </PopoverTrigger>
      <PopoverContent align="start" className="max-h-[70vh] w-[min(20rem,calc(100vw-2rem))] overflow-y-auto">
        <h3 className="text-xs font-medium text-muted-foreground">Shown, in order</h3>
        <ul className="space-y-0.5">
          {columns.map((c, i) => {
            const label = fields.find((f) => f.field === c.field)?.label ?? c.field;
            return (
              <li key={c.field} className="flex items-center gap-1">
                <Checkbox
                  aria-label={`Show ${label}`}
                  checked
                  disabled={c.field === titleField}
                  onCheckedChange={() => onChange(columns.filter((x) => x.field !== c.field))}
                />
                <span className="flex-1 truncate text-sm">{label}</span>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Move ${label} earlier`}
                  disabled={i === 0}
                  onClick={() => move(i, -1)}
                >
                  <ArrowUp className="size-3.5" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Move ${label} later`}
                  disabled={i === columns.length - 1}
                  onClick={() => move(i, 1)}
                >
                  <ArrowDown className="size-3.5" />
                </Button>
              </li>
            );
          })}
        </ul>
        {hidden.length > 0 && (
          <>
            <h3 className="border-t pt-2 text-xs font-medium text-muted-foreground">Hidden</h3>
            <ul className="space-y-1">
              {hidden.map((f) => (
                <li key={f.field}>
                  <label className="flex items-center gap-2 text-sm">
                    <Checkbox
                      checked={false}
                      disabled={columns.length >= 60}
                      onCheckedChange={() => onChange([...columns, { field: f.field, width: DEFAULT_WIDTH }])}
                    />
                    {f.label}
                  </label>
                </li>
              ))}
            </ul>
          </>
        )}
      </PopoverContent>
    </Popover>
  );
}
