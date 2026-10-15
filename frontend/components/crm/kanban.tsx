"use client";

import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MeasuringStrategy,
  MouseSensor,
  pointerWithin,
  rectIntersection,
  TouchSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type Announcements,
  type CollisionDetection,
  type DragEndEvent,
  type KeyboardCoordinateGetter,
  type UniqueIdentifier,
} from "@dnd-kit/core";
import { GripVertical } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import type { CrmLists, CrmQueryGroup, ListKind } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { formatAmount, type CrmTimezone, type NumberFormat } from "@/lib/crm/format";
import { cellText, fieldLabel, groupKeyOf, TITLE_FIELD } from "@/lib/crm/view-fields";
import { cn } from "@/lib/utils";

/**
 * The kanban view — spec 007 FR-CRM-VIEW-01, -06, -08; NFR-CRM-01, -06.
 *
 * Cards grouped by a select field, one column per option in the list's
 * order (an empty column is still somewhere to drop). Dragging a card to
 * another column changes that field through the same rules as any edit: the
 * card moves at once, then the server confirms it or it goes back with the
 * server's sentence. A deal's stage drag that needs more opens the stage
 * form (or, Closed Won in Labs, the handoff) and cancelling it puts the card
 * back.
 *
 * Mouse, touch (press and hold, so a phone can still scroll the board) and
 * keyboard: focus a card, Space to pick it up, Left and Right to move it a
 * column at a time, Space to drop and Escape to cancel; each step is
 * announced. Cards keep the view's order within a column: a card's place in
 * its column is not something a person sets, so a drop only ever changes
 * the column. Column headers carry the count and, for deals and everyone but
 * a Viewer, the INR sum and the weighted sum from the server.
 */

type Row = Record<string, unknown> & { id: string; updated_at: string };

export interface KanbanColumn {
  /** The field's value; "" for records with none. */
  key: string;
  label: string;
}

const COLUMN_PREFIX = "column:";

/**
 * Left and Right carry a picked-up card to the middle of the next column
 * along; Up and Down do nothing. Columns are laid out left to right in the
 * board's order, so their measured positions give the order.
 */
const nextColumn: KeyboardCoordinateGetter = (event, { context }) => {
  const step = event.code === "ArrowRight" ? 1 : event.code === "ArrowLeft" ? -1 : 0;
  if (event.code === "ArrowUp" || event.code === "ArrowDown") event.preventDefault();
  const rect = context.collisionRect;
  if (!step || !rect) return undefined;
  event.preventDefault();
  const placed = [...context.droppableRects.entries()]
    .filter(([id]) => String(id).startsWith(COLUMN_PREFIX))
    .map(([, r]) => r)
    .sort((a, b) => a.left - b.left);
  const middle = rect.left + rect.width / 2;
  const here = placed.findIndex((r) => middle >= r.left && middle <= r.right);
  const target = placed[here + step];
  if (here < 0 || !target) return undefined;
  return { x: target.left + (target.width - rect.width) / 2, y: target.top + 48 };
};

/** The column under the pointer, else the one the card overlaps most. */
const lanes: CollisionDetection = (args) => {
  const within = pointerWithin(args);
  return within.length > 0 ? within : rectIntersection(args);
};

export function Kanban({
  kind,
  rows,
  field,
  columns,
  groups,
  cardFields,
  canMove,
  onMove,
  hrefFor,
  detailFor,
  lists,
  timeZone,
  numberFormat,
}: {
  kind: ListKind;
  rows: Row[];
  /** The kanban field. */
  field: string;
  columns: KanbanColumn[];
  groups?: CrmQueryGroup[];
  cardFields: string[];
  canMove: (row: Row) => boolean;
  /**
   * Change `field` to the column's key. Resolves with the saved record once
   * the caller holds it, with null when the person cancelled (the card goes
   * back), or rejects with the server's refusal (the card goes back and the
   * sentence shows).
   */
  onMove: (row: Row, key: string) => Promise<Row | null>;
  /** A record's page; null when it has none to open. */
  hrefFor: (row: Row) => string | null;
  /** A line under the title (a task's linked records). */
  detailFor?: (row: Row) => React.ReactNode;
  /** The workspace's select lists, to name a value the row sends no label for. */
  lists: CrmLists;
  timeZone: CrmTimezone;
  numberFormat: NumberFormat;
}) {
  const [moved, setMoved] = useState<Record<string, string>>({});
  const [dragging, setDragging] = useState<Row | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: nextColumn })
  );

  const columnOf = (row: Row) => moved[row.id] ?? groupKeyOf(row, field);
  const byColumn = useMemo(() => {
    const out = new Map<string, Row[]>(columns.map((c) => [c.key, []]));
    for (const row of rows) {
      const key = moved[row.id] ?? groupKeyOf(row, field);
      if (!out.has(key)) out.set(key, []);
      out.get(key)!.push(row);
    }
    return out;
  }, [rows, columns, moved, field]);

  // Header counts and sums are the server's, for the whole view; a card in
  // flight counts where it now shows.
  const serverGroup = (key: string) => groups?.find((g) => (g.key === null ? "" : String(g.key)) === key);
  const delta = (key: string) =>
    rows.reduce((n, row) => {
      if (!(row.id in moved)) return n;
      const from = groupKeyOf(row, field);
      return n + (moved[row.id] === key ? 1 : 0) - (from === key ? 1 : 0);
    }, 0);

  const label = (key: string) => columns.find((c) => c.key === key)?.label ?? "None";
  const nameOf = (id: UniqueIdentifier) => {
    const row = rows.find((r) => r.id === id);
    return row ? String(row[TITLE_FIELD[kind]] ?? "The card") : "The card";
  };
  const targetOf = (over: { id: UniqueIdentifier } | null): string | null => {
    if (!over) return null;
    const id = String(over.id);
    return id.startsWith(COLUMN_PREFIX) ? id.slice(COLUMN_PREFIX.length) : null;
  };

  const announcements: Announcements = {
    onDragStart: ({ active }) => `Picked up ${nameOf(active.id)}. Use the arrow keys to choose a column.`,
    onDragOver: ({ active, over }) => {
      const to = targetOf(over);
      return to === null ? `${nameOf(active.id)} is over no column.` : `${nameOf(active.id)} is over ${label(to)}.`;
    },
    onDragEnd: ({ active, over }) => {
      const to = targetOf(over);
      return to === null ? `${nameOf(active.id)} was put back.` : `${nameOf(active.id)} was dropped in ${label(to)}.`;
    },
    onDragCancel: ({ active }) => `Moving ${nameOf(active.id)} was cancelled; it is back where it was.`,
  };

  async function onDragEnd({ active, over }: DragEndEvent) {
    setDragging(null);
    const row = rows.find((r) => r.id === active.id);
    const to = targetOf(over);
    if (!row || to === null || to === columnOf(row)) return;
    setError(null);
    // Feedback at once (NFR-CRM-01); the server has the last word.
    setMoved((m) => ({ ...m, [row.id]: to }));
    try {
      await onMove(row, to);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setMoved((m) => {
        const next = { ...m };
        delete next[row.id];
        return next;
      });
    }
  }

  return (
    <div className="space-y-2" data-testid="crm-kanban">
      {error && (
        <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive" data-testid="kanban-error">
          {error}
        </p>
      )}
      <DndContext
        sensors={sensors}
        collisionDetection={lanes}
        // Only the columns are drop targets: measured always, so the first
        // arrow key after picking a card up already knows where they are.
        measuring={{ droppable: { strategy: MeasuringStrategy.Always } }}
        accessibility={{
          announcements,
          screenReaderInstructions: {
            draggable:
              "To move this card, press Space. Use the arrow keys to choose a column, Space to drop it, or Escape to cancel.",
          },
        }}
        onDragStart={({ active }) => setDragging(rows.find((r) => r.id === active.id) ?? null)}
        onDragCancel={() => setDragging(null)}
        onDragEnd={onDragEnd}
      >
        {/* `relative` keeps the cards' screen-reader text inside the
            scroller, so the board never widens a phone's page. */}
        <div className="relative flex gap-3 overflow-x-auto pb-2">
          {[...byColumn.keys()].map((key) => {
            const cards = byColumn.get(key) ?? [];
            const group = serverGroup(key);
            return (
              <KanbanLane
                key={key || "none"}
                id={key}
                label={label(key)}
                count={(group?.count ?? 0) + delta(key)}
                sum={group?.aggregates?.sum_amount_in_inr}
                weighted={group?.aggregates?.sum_weighted_amount}
                numberFormat={numberFormat}
              >
                {cards.map((row) => (
                  <KanbanCard
                    key={row.id}
                    kind={kind}
                    row={row}
                    cardFields={cardFields}
                    disabled={!canMove(row)}
                    href={hrefFor(row) ?? undefined}
                    detail={detailFor?.(row)}
                    lists={lists}
                    timeZone={timeZone}
                    numberFormat={numberFormat}
                  />
                ))}
              </KanbanLane>
            );
          })}
        </div>
        <DragOverlay>
          {dragging && (
            <CardBody
              kind={kind}
              row={dragging}
              cardFields={cardFields}
              lists={lists}
              timeZone={timeZone}
              numberFormat={numberFormat}
              lifted
            />
          )}
        </DragOverlay>
      </DndContext>
      <p className="text-xs text-muted-foreground">
        Grouped by {fieldLabel(kind, field)}. Drag a card, or focus it and press Space, to move it.
      </p>
    </div>
  );
}

function KanbanLane({
  id,
  label,
  count,
  sum,
  weighted,
  numberFormat,
  children,
}: {
  id: string;
  label: string;
  count: number;
  sum?: string | number | null;
  weighted?: string | number | null;
  numberFormat: NumberFormat;
  children: React.ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: `${COLUMN_PREFIX}${id}` });
  return (
    <section
      ref={setNodeRef}
      aria-label={`${label}, ${count} ${count === 1 ? "card" : "cards"}`}
      data-testid="kanban-column"
      data-column={id}
      className={cn(
        "flex w-72 shrink-0 flex-col gap-2 rounded-lg border bg-muted/30 p-2",
        isOver && "ring-2 ring-ring"
      )}
    >
      <header className="space-y-0.5 px-1">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="truncate text-sm font-medium">{label}</h2>
          <span className="text-xs text-muted-foreground">{count}</span>
        </div>
        {(sum !== undefined || weighted !== undefined) && (
          <p className="text-xs text-muted-foreground">
            {sum !== undefined && sum !== null && <>{formatAmount(String(sum), "INR", numberFormat)}</>}
            {weighted !== undefined && weighted !== null && (
              <> · weighted {formatAmount(String(weighted), "INR", numberFormat)}</>
            )}
          </p>
        )}
      </header>
      <div className="flex min-h-16 flex-col gap-2">{children}</div>
    </section>
  );
}

function KanbanCard({
  kind,
  row,
  cardFields,
  disabled,
  href,
  detail,
  lists,
  timeZone,
  numberFormat,
}: {
  kind: ListKind;
  row: Row;
  cardFields: string[];
  disabled: boolean;
  href?: string;
  detail?: React.ReactNode;
  lists: CrmLists;
  timeZone: CrmTimezone;
  numberFormat: NumberFormat;
}) {
  // The overlay follows the pointer; the card stays put, faded, until the
  // drop lands it in its new column.
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: row.id, disabled });
  return (
    <div
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      aria-label={`${String(row[TITLE_FIELD[kind]] ?? "Card")}${disabled ? "" : ", movable"}`}
      data-testid="kanban-card"
      data-card-id={row.id}
      className={cn(
        "rounded-md focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        !disabled && "cursor-grab touch-manipulation",
        isDragging && "opacity-40"
      )}
    >
      <CardBody
        kind={kind}
        row={row}
        cardFields={cardFields}
        href={href}
        detail={detail}
        lists={lists}
        timeZone={timeZone}
        numberFormat={numberFormat}
        movable={!disabled}
      />
    </div>
  );
}

function CardBody({
  kind,
  row,
  cardFields,
  href,
  detail,
  lists,
  timeZone,
  numberFormat,
  lifted,
  movable,
}: {
  kind: ListKind;
  row: Row;
  cardFields: string[];
  href?: string;
  detail?: React.ReactNode;
  lists: CrmLists;
  timeZone: CrmTimezone;
  numberFormat: NumberFormat;
  lifted?: boolean;
  movable?: boolean;
}) {
  const title = cellText(kind, TITLE_FIELD[kind], row, { timeZone, numberFormat, lists });
  return (
    <div className={cn("space-y-1 rounded-md border bg-background p-2.5 text-sm shadow-xs", lifted && "shadow-lg")}>
      <div className="flex items-start gap-1">
        {href ? (
          <Link href={href} className="min-w-0 flex-1 font-medium break-words hover:underline">
            {title}
          </Link>
        ) : (
          <span className="min-w-0 flex-1 font-medium break-words">{title}</span>
        )}
        {(movable || lifted) && <GripVertical aria-hidden className="size-4 shrink-0 text-muted-foreground" />}
      </div>
      {detail && <div className="text-xs text-muted-foreground">{detail}</div>}
      {cardFields
        .filter((f) => f !== TITLE_FIELD[kind])
        .map((f) => {
          const text = cellText(kind, f, row, { timeZone, numberFormat, lists });
          if (text === "—") return null;
          return (
            <p key={f} className="truncate text-xs text-muted-foreground">
              <span className="sr-only">{fieldLabel(kind, f)}: </span>
              {text}
            </p>
          );
        })}
    </div>
  );
}
