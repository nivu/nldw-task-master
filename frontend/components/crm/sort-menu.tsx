"use client";

import { ArrowDown, ArrowUp, ArrowUpDown, Plus, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { ViewSort } from "@/lib/api/crm-types";
import { NATIVE_SELECT, type ViewFieldMeta } from "@/lib/crm/view-fields";

/**
 * Sort and group — spec 007 FR-CRM-VIEW-03.
 *
 * Several sorts in order of precedence (at most ten), each ascending or
 * descending; a table may also be grouped by a select, person or record
 * field, its groups collapsible with their counts. A stage, lead stage or
 * bucket sorts in the pipeline's own order, a person by name: the server
 * decides, so a page boundary never splits the order.
 */

const MAX_SORTS = 10;

export function SortMenu({
  sorts,
  onSortsChange,
  fields,
  groupBy,
  onGroupByChange,
  groupFields,
  groupLabel = "Group rows by",
}: {
  sorts: ViewSort[];
  onSortsChange: (sorts: ViewSort[]) => void;
  fields: ViewFieldMeta[];
  /** Absent on a kanban, which groups by its own field. */
  groupBy?: string | null;
  onGroupByChange?: (field: string | null) => void;
  /** What may group; a kanban takes select fields only. */
  groupFields?: ViewFieldMeta[];
  groupLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const sortable = fields.filter((f) => f.sortable);
  const groupable = groupFields ?? fields.filter((f) => f.groupable);
  const unused = sortable.filter((f) => !sorts.some((s) => s.field === f.field));
  const label = (field: string) => fields.find((f) => f.field === field)?.label ?? field;

  const set = (i: number, sort: ViewSort) => onSortsChange(sorts.map((s, j) => (j === i ? sort : s)));
  const move = (i: number, by: number) => {
    const next = [...sorts];
    const [taken] = next.splice(i, 1);
    next.splice(i + by, 0, taken);
    onSortsChange(next);
  };

  const summary = [
    sorts.length ? `Sort · ${sorts.length}` : "Sort",
    onGroupByChange && groupBy ? `Group · ${label(groupBy)}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={<Button variant="outline" size="sm" />}>
        <ArrowUpDown className="size-3.5" />
        {summary}
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(28rem,calc(100vw-2rem))] gap-3">
        <section className="space-y-2" aria-label="Sorts">
          <h3 className="text-xs font-medium text-muted-foreground">Sort by</h3>
          {sorts.length === 0 && <p className="text-sm text-muted-foreground">Newest changes first.</p>}
          {sorts.map((sort, i) => (
            <div key={sort.field} className="flex items-center gap-1.5" data-testid="sort-row">
              <select
                aria-label={`Sort ${i + 1} field`}
                className={`${NATIVE_SELECT} min-w-0 flex-1`}
                value={sort.field}
                onChange={(e) => set(i, { ...sort, field: e.target.value })}
              >
                {[sortable.find((f) => f.field === sort.field), ...unused]
                  .filter((f): f is ViewFieldMeta => Boolean(f))
                  .map((f) => (
                    <option key={f.field} value={f.field}>
                      {f.label}
                    </option>
                  ))}
                {!sortable.some((f) => f.field === sort.field) && (
                  <option value={sort.field}>{label(sort.field)}</option>
                )}
              </select>
              <select
                aria-label={`Sort ${i + 1} direction`}
                className={NATIVE_SELECT}
                value={sort.dir}
                onChange={(e) => set(i, { ...sort, dir: e.target.value as "asc" | "desc" })}
              >
                <option value="asc">Ascending</option>
                <option value="desc">Descending</option>
              </select>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Move sort ${i + 1} up`}
                disabled={i === 0}
                onClick={() => move(i, -1)}
              >
                <ArrowUp className="size-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Move sort ${i + 1} down`}
                disabled={i === sorts.length - 1}
                onClick={() => move(i, 1)}
              >
                <ArrowDown className="size-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Remove sort ${i + 1}`}
                onClick={() => onSortsChange(sorts.filter((_, j) => j !== i))}
              >
                <X className="size-4" />
              </Button>
            </div>
          ))}
          <Button
            variant="outline"
            size="sm"
            disabled={unused.length === 0 || sorts.length >= MAX_SORTS}
            onClick={() => onSortsChange([...sorts, { field: unused[0].field, dir: "asc" }])}
          >
            <Plus className="size-3.5" /> Add sort
          </Button>
        </section>

        {onGroupByChange && (
          <section className="space-y-1.5 border-t pt-3">
            <label htmlFor="view-group-by" className="text-xs font-medium text-muted-foreground">
              {groupLabel}
            </label>
            <select
              id="view-group-by"
              className={`${NATIVE_SELECT} w-full`}
              value={groupBy ?? ""}
              onChange={(e) => onGroupByChange(e.target.value || null)}
            >
              {!groupFields && <option value="">No grouping</option>}
              {groupable.map((f) => (
                <option key={f.field} value={f.field}>
                  {f.label}
                </option>
              ))}
            </select>
          </section>
        )}
      </PopoverContent>
    </Popover>
  );
}
