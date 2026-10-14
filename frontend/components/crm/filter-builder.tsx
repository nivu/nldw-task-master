"use client";

import { Filter, Plus, X } from "lucide-react";
import { useState } from "react";

import { FieldEditor } from "@/components/crm/field-editor";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type {
  CrmBootstrap,
  CrmMember,
  FilterCondition,
  FilterGroup,
  FilterNode,
  FilterOperator,
} from "@/lib/api/crm-types";
import {
  choicesFor,
  NATIVE_SELECT,
  NO_VALUE,
  OPERATOR_LABELS,
  OPERATORS,
  relationSearch,
  type ViewFieldMeta,
} from "@/lib/crm/view-fields";

/**
 * Filters — spec 007 FR-CRM-VIEW-02, FR-CRM-ACC-11.
 *
 * Conditions on any filterable field, with the operators its type takes,
 * joined by AND or OR in groups nested at most three deep, at most thirty
 * conditions. A Viewer is offered no amount field; were one sent anyway the
 * server would refuse it ("Viewers cannot filter, sort or total by amount.")
 * and that sentence is what the list shows. Nothing applies until Apply, so
 * a half-written condition never reaches the server.
 */

const MAX_DEPTH = 3;
const MAX_CONDITIONS = 30;

function isGroup(node: FilterNode): node is FilterGroup {
  return "items" in node;
}

export function countConditions(node: FilterNode | undefined): number {
  if (!node) return 0;
  return isGroup(node) ? node.items.reduce((n, item) => n + countConditions(item), 0) : 1;
}

/** Conditions still waiting for a value are left out; empty groups too. */
function complete(node: FilterNode): FilterNode | null {
  if (isGroup(node)) {
    const items = node.items.map(complete).filter((i): i is FilterNode => i !== null);
    return items.length ? { op: node.op, items } : null;
  }
  if (NO_VALUE.includes(node.operator)) {
    const { field, operator } = node;
    return { field, operator };
  }
  const value = node.value;
  if (value === undefined || value === null || value === "") return null;
  if (Array.isArray(value) && value.length === 0) return null;
  return node;
}

export function FilterBuilder({
  value,
  onChange,
  fields,
  bootstrap,
  members,
}: {
  value: FilterGroup | undefined;
  onChange: (filters: FilterGroup) => void;
  /** The filterable fields this person may use. */
  fields: ViewFieldMeta[];
  bootstrap: CrmBootstrap;
  members: CrmMember[];
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<FilterGroup>(value ?? { op: "and", items: [] });
  const count = countConditions(value);
  const offered = fields.filter((f) => f.filterable);

  function apply() {
    onChange((complete(draft) as FilterGroup | null) ?? { op: "and", items: [] });
    setOpen(false);
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next) setDraft(value ?? { op: "and", items: [] });
        setOpen(next);
      }}
    >
      <PopoverTrigger render={<Button variant="outline" size="sm" />}>
        <Filter className="size-3.5" />
        Filter{count > 0 ? ` · ${count}` : ""}
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(40rem,calc(100vw-2rem))] gap-3">
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            apply();
          }}
        >
          <GroupEditor
            group={draft}
            depth={1}
            total={countConditions(draft)}
            fields={offered}
            bootstrap={bootstrap}
            members={members}
            onChange={setDraft}
          />
          <div className="flex justify-end gap-1.5">
            <Button type="button" variant="ghost" size="sm" onClick={() => setDraft({ op: "and", items: [] })}>
              Clear all
            </Button>
            <Button type="submit" size="sm">
              Apply
            </Button>
          </div>
        </form>
      </PopoverContent>
    </Popover>
  );
}

function GroupEditor({
  group,
  depth,
  total,
  fields,
  bootstrap,
  members,
  onChange,
  onRemove,
}: {
  group: FilterGroup;
  depth: number;
  total: number;
  fields: ViewFieldMeta[];
  bootstrap: CrmBootstrap;
  members: CrmMember[];
  onChange: (group: FilterGroup) => void;
  onRemove?: () => void;
}) {
  const full = total >= MAX_CONDITIONS;
  const first = fields[0];
  const set = (i: number, node: FilterNode) =>
    onChange({ ...group, items: group.items.map((item, j) => (j === i ? node : item)) });
  const remove = (i: number) => onChange({ ...group, items: group.items.filter((_, j) => j !== i) });
  const blank = (): FilterCondition => ({ field: first.field, operator: OPERATORS[first.type][0] });

  return (
    <fieldset className={depth > 1 ? "space-y-2 rounded-md border p-2" : "space-y-2"}>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <legend className="sr-only">Filter group</legend>
        <span>Match</span>
        <select
          aria-label="Match all or any"
          className={NATIVE_SELECT}
          value={group.op}
          onChange={(e) => onChange({ ...group, op: e.target.value as "and" | "or" })}
        >
          <option value="and">all of these (AND)</option>
          <option value="or">any of these (OR)</option>
        </select>
        {onRemove && (
          <Button type="button" variant="ghost" size="sm" className="ml-auto" onClick={onRemove}>
            Remove group
          </Button>
        )}
      </div>
      {group.items.length === 0 && <p className="text-sm text-muted-foreground">No conditions.</p>}
      {group.items.map((item, i) =>
        isGroup(item) ? (
          <GroupEditor
            key={i}
            group={item}
            depth={depth + 1}
            total={total}
            fields={fields}
            bootstrap={bootstrap}
            members={members}
            onChange={(next) => set(i, next)}
            onRemove={() => remove(i)}
          />
        ) : (
          <ConditionEditor
            key={i}
            index={i}
            condition={item}
            fields={fields}
            bootstrap={bootstrap}
            members={members}
            onChange={(next) => set(i, next)}
            onRemove={() => remove(i)}
          />
        )
      )}
      <div className="flex flex-wrap gap-1.5">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={full || !first}
          onClick={() => onChange({ ...group, items: [...group.items, blank()] })}
        >
          <Plus className="size-3.5" /> Condition
        </Button>
        {depth < MAX_DEPTH && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={full || !first}
            onClick={() =>
              onChange({
                ...group,
                items: [...group.items, { op: group.op === "and" ? "or" : "and", items: [blank()] }],
              })
            }
          >
            <Plus className="size-3.5" /> Group
          </Button>
        )}
      </div>
      {full && <p className="text-xs text-muted-foreground">At most {MAX_CONDITIONS} conditions.</p>}
    </fieldset>
  );
}

function ConditionEditor({
  index,
  condition,
  fields,
  bootstrap,
  members,
  onChange,
  onRemove,
}: {
  index: number;
  condition: FilterCondition;
  fields: ViewFieldMeta[];
  bootstrap: CrmBootstrap;
  members: CrmMember[];
  onChange: (condition: FilterCondition) => void;
  onRemove: () => void;
}) {
  // A saved condition on a field this person cannot use (an amount, for a
  // Viewer, never arrives: the server cleans it first) still shows by name.
  const meta = fields.find((f) => f.field === condition.field);
  const operators = meta ? OPERATORS[meta.type] : [condition.operator];

  return (
    <div className="flex flex-wrap items-start gap-1.5" data-testid="filter-condition">
      <select
        aria-label={`Condition ${index + 1} field`}
        className={NATIVE_SELECT}
        value={condition.field}
        onChange={(e) => {
          const next = fields.find((f) => f.field === e.target.value);
          if (next) onChange({ field: next.field, operator: OPERATORS[next.type][0] });
        }}
      >
        {!meta && <option value={condition.field}>{condition.field}</option>}
        {fields.map((f) => (
          <option key={f.field} value={f.field}>
            {f.label}
          </option>
        ))}
      </select>
      <select
        aria-label={`Condition ${index + 1} operator`}
        className={NATIVE_SELECT}
        value={condition.operator}
        onChange={(e) => {
          const operator = e.target.value as FilterOperator;
          const keep =
            !NO_VALUE.includes(operator) &&
            Array.isArray(condition.value) === (operator === "any_of" || operator === "none_of");
          onChange({ field: condition.field, operator, ...(keep ? { value: condition.value } : {}) });
        }}
      >
        {operators.map((op) => (
          <option key={op} value={op}>
            {OPERATOR_LABELS[op]}
          </option>
        ))}
      </select>
      {meta && !NO_VALUE.includes(condition.operator) && (
        <div className="min-w-40 flex-1">
          <ValueEditor
            index={index}
            meta={meta}
            condition={condition}
            bootstrap={bootstrap}
            members={members}
            onChange={(value) => onChange({ ...condition, value })}
          />
        </div>
      )}
      <Button type="button" variant="ghost" size="icon" aria-label={`Remove condition ${index + 1}`} onClick={onRemove}>
        <X className="size-4" />
      </Button>
    </div>
  );
}

function ValueEditor({
  index,
  meta,
  condition,
  bootstrap,
  members,
  onChange,
}: {
  index: number;
  meta: ViewFieldMeta;
  condition: FilterCondition;
  bootstrap: CrmBootstrap;
  members: CrmMember[];
  onChange: (value: unknown) => void;
}) {
  const label = `Condition ${index + 1} value`;
  const { operator, value } = condition;

  if (operator === "within_last_days" || operator === "within_next_days") {
    return (
      <div className="flex items-center gap-1.5">
        <Input
          aria-label={label}
          type="number"
          min={0}
          max={3650}
          className="w-24"
          value={value === undefined || value === null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        />
        <span className="text-sm text-muted-foreground">days</span>
      </div>
    );
  }
  if (meta.type === "date" || meta.type === "datetime") {
    return (
      <Input
        aria-label={label}
        type="date"
        value={(value as string | undefined) ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
      />
    );
  }
  if (meta.type === "number" || meta.type === "currency") {
    return (
      <Input
        aria-label={label}
        inputMode="decimal"
        value={(value as string | undefined) ?? ""}
        onChange={(e) => onChange(e.target.value.trim() || null)}
      />
    );
  }
  if (meta.type === "boolean") {
    return (
      <select
        aria-label={label}
        className={`${NATIVE_SELECT} w-full`}
        value={value === undefined || value === null ? "" : String(value)}
        onChange={(e) => onChange(e.target.value === "" ? null : e.target.value === "true")}
      >
        <option value="">Choose…</option>
        <option value="true">Yes</option>
        <option value="false">No</option>
      </select>
    );
  }
  if (meta.type === "relation") {
    const chosen = Array.isArray(value) ? (value as string[]) : [];
    const many = operator === "any_of" || operator === "none_of";
    return (
      <div className="space-y-1">
        {many && chosen.length > 0 && (
          <p className="text-xs text-muted-foreground">{chosen.length} chosen</p>
        )}
        <FieldEditor
          id={`filter-${index}-value`}
          spec={{
            name: meta.field,
            label: meta.label,
            type: "relation",
            choices: [],
            search: relationSearch(meta.field, bootstrap.workspace.key),
          }}
          value={many ? null : ((value as string | undefined) ?? null)}
          onChange={(picked) => {
            if (!many) return onChange(picked);
            if (picked && !chosen.includes(picked as string)) onChange([...chosen, picked as string]);
          }}
        />
      </div>
    );
  }
  if (["select", "fixed_select", "person", "flag", "multi_select"].includes(meta.type)) {
    const choices = choicesFor(meta, bootstrap, members);
    if (operator === "any_of" || operator === "none_of") {
      const chosen = Array.isArray(value) ? (value as string[]) : [];
      return (
        <fieldset className="grid max-h-40 grid-cols-1 gap-1 overflow-y-auto rounded-md border p-1.5 sm:grid-cols-2">
          <legend className="sr-only">{label}</legend>
          {choices.map((choice) => (
            <label key={choice.value} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={chosen.includes(choice.value)}
                onCheckedChange={(checked) =>
                  onChange(checked ? [...chosen, choice.value] : chosen.filter((c) => c !== choice.value))
                }
              />
              {choice.label}
            </label>
          ))}
        </fieldset>
      );
    }
    return (
      <select
        aria-label={label}
        className={`${NATIVE_SELECT} w-full`}
        value={(value as string | undefined) ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
      >
        <option value="">Choose…</option>
        {choices.map((choice) => (
          <option key={choice.value} value={choice.value}>
            {choice.label}
          </option>
        ))}
      </select>
    );
  }
  return (
    <Input
      aria-label={label}
      maxLength={200}
      value={(value as string | undefined) ?? ""}
      onChange={(e) => onChange(e.target.value || null)}
    />
  );
}
