/**
 * The view registry as the screens need it — spec 007 FR-CRM-VIEW-01 to -09,
 * FR-CRM-ACC-11; data-model §3.15.
 *
 * Mirrored by hand from `backend/app/domain/crm/objects.py` (`VIEW_FIELDS`,
 * the field labels and the select lists) and `domain/crm/views.py`
 * (`OPERATORS`), as `crm-types.ts` mirrors the schemas. Change one, change
 * the other. The server checks every field, operator and value again; this
 * file only decides what the filter builder, sort menu, columns, footers and
 * inline editors offer, and how a row's value reads in a cell.
 */

import type { Choice, FieldSpec, FieldType, RelationSearch } from "@/components/crm/field-editor";
import { searchCompanyChoices, searchPersonChoices } from "@/lib/api/crm";
import type {
  CrmBootstrap,
  CrmLists,
  CrmMember,
  FilterOperator,
  ViewFieldType,
  ViewObjectKind,
} from "@/lib/api/crm-types";
import { formatAmount, formatDate, type Currency, type CrmTimezone, type NumberFormat } from "@/lib/crm/format";

export interface ViewFieldMeta {
  field: string;
  label: string;
  type: ViewFieldType;
  /** Omitted for a Viewer everywhere (FR-CRM-ACC-11). */
  amount?: boolean;
  filterable: boolean;
  sortable: boolean;
  groupable: boolean;
  aggregatable: boolean;
  scope: "all" | "tva_linked" | "not_tva_linked";
  /** The select list its options come from. */
  listKey?: string;
  /** Options that are not a workspace list. */
  fixed?: Choice[];
  /** Changed by a PATCH from a cell or card (system-written fields are not). */
  editable?: boolean;
}

const ENTITIES: Choice[] = [
  { value: "nunnari_labs", label: "Nunnari Labs" },
  { value: "deepweaver", label: "DeepWeaver" },
];
const CATEGORIES: Choice[] = [
  { value: "client", label: "Client" },
  { value: "poc", label: "POC" },
  { value: "product", label: "Product" },
];
const CURRENCIES: Choice[] = ["INR", "AUD", "USD"].map((c) => ({ value: c, label: c }));
const NOTE_KINDS: Choice[] = [
  { value: "note", label: "Note" },
  { value: "call", label: "Call" },
  { value: "meeting", label: "Meeting" },
  { value: "whatsapp", label: "WhatsApp" },
  { value: "other", label: "Other" },
];

/** `_v` in objects.py: option, person and relation fields group a list;
 *  money and numbers total. */
function v(
  field: string,
  label: string,
  type: ViewFieldType,
  extra: Partial<Omit<ViewFieldMeta, "field" | "label" | "type">> = {}
): ViewFieldMeta {
  return {
    field,
    label,
    type,
    filterable: true,
    sortable: true,
    groupable: ["select", "fixed_select", "person", "relation", "boolean", "flag"].includes(type),
    aggregatable: type === "number" || type === "currency",
    scope: "all",
    ...extra,
  };
}

const STAMPS = [
  v("created_at", "Created", "datetime"),
  v("updated_at", "Updated", "datetime"),
];
const POSITION = v("position", "Position", "number", { filterable: false, aggregatable: false });
const E = { editable: true };

export const VIEW_FIELDS: Record<ViewObjectKind, ViewFieldMeta[]> = {
  company: [
    v("name", "Name", "text", E),
    v("domain", "Domain", "text", E),
    v("entities", "Entity", "multi_select", { sortable: false, scope: "tva_linked", fixed: ENTITIES, ...E }),
    v("lead_stage", "Lead stage", "fixed_select", { listKey: "company.lead_stage", ...E }),
    v("account_type_id", "Account type", "select", { listKey: "company.account_type", ...E }),
    v("relationship_status_id", "Relationship status", "select", {
      listKey: "company.relationship_status",
      ...E,
    }),
    v("channel_ids", "Channels", "multi_select", { sortable: false, listKey: "channel", ...E }),
    v("owner_id", "Owner", "person", E),
    v("next_touch_on", "Next touch", "date", E),
    v("industry_id", "Industry", "select", { listKey: "company.industry", ...E }),
    v("institution_type_id", "Institution type", "select", {
      scope: "not_tva_linked",
      listKey: "company.institution_type",
      ...E,
    }),
    v("size_id", "Size", "select", { listKey: "company.size", ...E }),
    v("country", "Country", "text", E),
    v("city", "City", "text", E),
    v("description", "Description", "long_text", { sortable: false, ...E }),
    v("source_id", "Source", "select", { listKey: "source", ...E }),
    v("last_inbound_at", "Last inbound", "datetime"),
    v("last_outbound_at", "Last outbound", "datetime"),
    v("last_contact_at", "Last contact", "datetime"),
    ...STAMPS,
    POSITION,
  ],
  person: [
    v("name", "Name", "text"),
    v("first_name", "First name", "text", E),
    v("last_name", "Last name", "text", E),
    v("company_id", "Company", "relation", E),
    v("primary_email", "Email", "text", E),
    v("job_title", "Job title", "text", E),
    v("city", "City", "text", E),
    v("owner_id", "Owner", "person", E),
    v("do_not_contact", "Do not contact", "boolean", E),
    v("last_inbound_at", "Last inbound", "datetime"),
    v("last_outbound_at", "Last outbound", "datetime"),
    v("last_contact_at", "Last contact", "datetime"),
    ...STAMPS,
    POSITION,
  ],
  opportunity: [
    v("name", "Name", "text", E),
    v("company_id", "Company", "relation", E),
    v("point_of_contact_id", "Point of contact", "relation", E),
    v("entity", "Entity", "fixed_select", { scope: "tva_linked", fixed: ENTITIES, ...E }),
    // Moved through the stage form, never a plain PATCH (contracts §2.6).
    v("stage", "Stage", "fixed_select", { listKey: "opportunity.stage", ...E }),
    v("amount", "Amount", "currency", { amount: true, ...E }),
    v("currency", "Currency", "fixed_select", { fixed: CURRENCIES }),
    v("amount_in_inr", "Amount in INR", "currency", { amount: true }),
    v("weighted_amount", "Weighted (INR)", "currency", { amount: true }),
    v("fx_rate_used", "FX rate used", "number", { amount: true, aggregatable: false }),
    v("won_fx_rate", "FX rate at win", "number", { amount: true, aggregatable: false }),
    v("probability", "Probability (%)", "number", E),
    v("expected_close_on", "Expected close date", "date", E),
    v("next_step", "Next step", "text", E),
    v("next_step_on", "Next step date", "date", E),
    v("owner_id", "Owner", "person", E),
    v("channel_id", "Channel", "select", { listKey: "channel", ...E }),
    v("deal_type_id", "Deal type", "select", { listKey: "opportunity.deal_type", ...E }),
    v("project_category", "Project category", "fixed_select", {
      scope: "tva_linked",
      fixed: CATEGORIES,
      ...E,
    }),
    v("engagement_model_id", "Engagement model", "select", {
      listKey: "opportunity.engagement_model",
      ...E,
    }),
    v("programme_id", "Programme", "select", {
      scope: "not_tva_linked",
      listKey: "opportunity.programme",
      ...E,
    }),
    v("proposal_sent_on", "Proposal sent on", "date", E),
    v("lost_reason_id", "Lost reason", "select", { listKey: "opportunity.lost_reason" }),
    v("lost_notes", "Lost notes", "long_text", { sortable: false, ...E }),
    v("lost_from_stage", "Lost from stage", "fixed_select", { listKey: "opportunity.stage" }),
    v("stage_entered_at", "Stage entered", "datetime"),
    v("won_on", "Won on", "date"),
    v("lost_on", "Lost on", "date"),
    ...STAMPS,
    POSITION,
  ],
  task: [
    v("title", "Title", "text", E),
    v("body", "Body", "long_text", { sortable: false, ...E }),
    v("status", "Status", "fixed_select", { listKey: "task.status", ...E }),
    v("due_on", "Due date", "date", E),
    v("bucket", "Priority bucket", "fixed_select", { listKey: "task.bucket", ...E }),
    v("category_id", "Category", "select", { listKey: "task.category", ...E }),
    v("assignee_id", "Assignee", "person", E),
    v("completed_at", "Completed", "datetime"),
    ...STAMPS,
    POSITION,
  ],
  note: [
    v("body", "Text", "long_text", { sortable: false, ...E }),
    v("kind", "Kind", "fixed_select", { fixed: NOTE_KINDS, ...E }),
    v("occurred_on", "Date", "date", E),
    v("created_by", "Written by", "person"),
    ...STAMPS,
  ],
};

/** Each object's page under /crm/[ws]. */
export const OBJECT_PATH: Record<ViewObjectKind, string> = {
  company: "companies",
  person: "people",
  opportunity: "opportunities",
  task: "tasks",
  note: "notes",
};

/** The field a row is named by in a list. */
export const TITLE_FIELD: Record<ViewObjectKind, string> = {
  company: "name",
  person: "name",
  opportunity: "name",
  task: "title",
  note: "body",
};

/** A kanban groups by a select field (FR-CRM-VIEW-01). */
export const KANBAN_TYPES: ViewFieldType[] = ["select", "fixed_select", "flag"];

/**
 * The registry as this person sees it in this workspace: Entity and Project
 * category where TVA is linked, Institution type and Programme where it is
 * not (FR-CRM-WS-06), and for a Viewer nothing about amounts.
 */
export function fieldsFor(
  kind: ViewObjectKind,
  { tvaLinked, viewer }: { tvaLinked: boolean; viewer: boolean }
): ViewFieldMeta[] {
  return VIEW_FIELDS[kind].filter(
    (f) =>
      (f.scope === "all" || (f.scope === "tva_linked") === tvaLinked) && !(viewer && f.amount)
  );
}

export function fieldMeta(kind: ViewObjectKind, field: string): ViewFieldMeta | undefined {
  return VIEW_FIELDS[kind].find((f) => f.field === field);
}

export function fieldLabel(kind: ViewObjectKind, field: string): string {
  return fieldMeta(kind, field)?.label ?? field.replace(/_/g, " ");
}

// ---------------------------------------------------------------------------
// Operators — FR-CRM-VIEW-02 (`OPERATORS` in domain/crm/views.py)
// ---------------------------------------------------------------------------

const TEXT: FilterOperator[] = ["is", "is_not", "contains", "not_contains", "is_empty", "is_not_empty"];
const OPTION: FilterOperator[] = ["is", "is_not", "any_of", "none_of", "is_empty", "is_not_empty"];
const DAYS: FilterOperator[] = [
  "on",
  "before",
  "after",
  "gte",
  "lte",
  "within_last_days",
  "within_next_days",
  "this_month",
  "this_quarter",
  "is_empty",
  "is_not_empty",
];
const NUMBER: FilterOperator[] = ["is", "is_not", "gt", "gte", "lt", "lte", "is_empty", "is_not_empty"];

export const OPERATORS: Record<ViewFieldType, FilterOperator[]> = {
  text: TEXT,
  long_text: ["contains", "not_contains", "is_empty", "is_not_empty"],
  number: NUMBER,
  currency: NUMBER,
  date: DAYS,
  datetime: DAYS,
  boolean: ["is", "is_not"],
  select: OPTION,
  fixed_select: OPTION,
  flag: OPTION,
  person: [...OPTION, "is_me"],
  relation: OPTION,
  multi_select: ["any_of", "none_of", "is_empty", "is_not_empty"],
};

export const OPERATOR_LABELS: Record<FilterOperator, string> = {
  is: "is",
  is_not: "is not",
  contains: "contains",
  not_contains: "does not contain",
  is_empty: "is empty",
  is_not_empty: "is not empty",
  any_of: "is any of",
  none_of: "is none of",
  is_me: "is me",
  on: "is on",
  before: "is before",
  after: "is after",
  gt: "is greater than",
  gte: "is at least",
  lt: "is less than",
  lte: "is at most",
  within_last_days: "is within the last … days",
  within_next_days: "is within the next … days",
  this_month: "is this month",
  this_quarter: "is this quarter",
};

/** Operators that take no value. */
export const NO_VALUE: FilterOperator[] = ["is_empty", "is_not_empty", "is_me", "this_month", "this_quarter"];

// ---------------------------------------------------------------------------
// Options, values and cells
// ---------------------------------------------------------------------------

/** A select field's options in list order: fixed keys by key, the rest by id. */
export function choicesFor(
  meta: ViewFieldMeta,
  bootstrap: CrmBootstrap,
  members: CrmMember[] = []
): Choice[] {
  if (meta.fixed) return meta.fixed;
  if (meta.type === "person") return members.map((m) => ({ value: m.user_id, label: m.display_name }));
  if (meta.type === "boolean") {
    return [
      { value: "true", label: "Yes" },
      { value: "false", label: "No" },
    ];
  }
  if (!meta.listKey) return [];
  return (bootstrap.lists[meta.listKey] ?? []).map((o) => ({
    value: meta.type === "fixed_select" ? (o.system_key ?? o.id) : o.id,
    label: o.label,
    inactive: !o.is_active,
  }));
}

type Row = Record<string, unknown>;

/** The key of the label the record routes send beside a field's value. */
function labelKey(field: string): string {
  if (field === "owner_id" || field === "assignee_id" || field === "created_by") {
    return field.replace(/_id$|_by$/, (m) => (m === "_by" ? "_by_name" : "_name"));
  }
  if (field === "company_id" || field === "point_of_contact_id") return field.replace(/_id$/, "_name");
  if (field === "channel_ids") return "channel_labels";
  if (field === "entities") return "entity_labels";
  if (field.endsWith("_id")) return field.replace(/_id$/, "_label");
  return `${field}_label`;
}

/** How a row's field reads in a cell or on a card. Display only. A select
 *  value the row sends no label for (Lost from stage) is named from `lists`. */
export function cellText(
  kind: ViewObjectKind,
  field: string,
  row: Row,
  {
    timeZone,
    numberFormat,
    lists,
  }: { timeZone: CrmTimezone; numberFormat: NumberFormat; lists?: CrmLists }
): string {
  const meta = fieldMeta(kind, field);
  const value = row[field];
  if (meta?.type === "currency") {
    if (value === undefined || value === null || value === "") return "—";
    const currency = (field === "amount" ? (row.currency as Currency | null) : "INR") ?? "INR";
    return formatAmount(String(value), currency, numberFormat);
  }
  if (value === undefined || value === null || value === "") return "—";
  if (Array.isArray(value)) {
    const labels = row[labelKey(field)];
    const shown = Array.isArray(labels) ? labels.map((l, i) => l ?? String(value[i])) : value;
    return shown.length ? shown.join(", ") : "—";
  }
  switch (meta?.type) {
    case "date":
    case "datetime":
      return formatDate(String(value), timeZone);
    case "boolean":
      return value ? "Yes" : "No";
    case "select":
    case "fixed_select":
    case "person":
    case "relation":
    case "flag": {
      const label = row[labelKey(field)];
      if (typeof label === "string" && label) return label;
      const listed =
        meta.listKey &&
        lists?.[meta.listKey]?.find((o) =>
          meta.type === "fixed_select" ? (o.system_key ?? o.id) === value : o.id === value
        );
      if (listed) return listed.label;
      return meta.fixed?.find((c) => c.value === value)?.label ?? String(value);
    }
    case "number":
      return field === "probability" ? `${value}%` : String(value);
    default:
      return String(value);
  }
}

/** The value a field's group (or kanban column) key is for this row. */
export function groupKeyOf(row: Row, field: string): string {
  const value = row[field];
  return value === undefined || value === null ? "" : String(value);
}

/** Spec §5.6–§5.10: a cell of these cannot be emptied. */
const REQUIRED: Record<ViewObjectKind, string[]> = {
  company: ["name", "owner_id"],
  person: ["first_name"],
  opportunity: ["name", "company_id", "owner_id"],
  task: ["title", "assignee_id"],
  note: ["body"],
};

const EDITOR_TYPE: Partial<Record<ViewFieldType, FieldType>> = {
  text: "text",
  long_text: "long_text",
  number: "number",
  currency: "currency",
  date: "date",
  boolean: "boolean",
  select: "select",
  fixed_select: "select",
  multi_select: "multi_select",
  person: "relation",
  relation: "relation",
};

/** The `field-editor` spec for a cell. Relations to records search the
 *  server (`search`), the current value named from the row. */
export function editorSpec(
  meta: ViewFieldMeta,
  row: Row,
  bootstrap: CrmBootstrap,
  members: CrmMember[],
  search?: FieldSpec["search"]
): FieldSpec {
  const current =
    meta.type === "relation" && row[meta.field]
      ? [{ value: String(row[meta.field]), label: String(row[labelKey(meta.field)] ?? "Current") }]
      : [];
  return {
    name: meta.field,
    label: meta.label,
    type: EDITOR_TYPE[meta.type] ?? "text",
    choices: meta.type === "relation" ? current : choicesFor(meta, bootstrap, members),
    search: meta.type === "relation" ? search : undefined,
    required: REQUIRED[(row.record_type as ViewObjectKind) ?? "company"]?.includes(meta.field) ?? false,
  };
}

/** A cell's current value in the editor's shape. */
export function editorValue(meta: ViewFieldMeta, row: Row): unknown {
  if (meta.type === "currency") {
    return { amount: (row.amount as string | null) ?? null, currency: (row.currency as Currency) ?? "INR" };
  }
  return row[meta.field] ?? null;
}

/** What a PATCH sends for an edited cell: an amount carries its currency. */
export function patchFor(meta: ViewFieldMeta, value: unknown): Record<string, unknown> {
  if (meta.type === "currency") {
    const money = value as { amount: string | null; currency: Currency };
    return { amount: money.amount, currency: money.amount ? money.currency : null };
  }
  return { [meta.field]: value };
}

const searches = new Map<string, RelationSearch>();

/**
 * A relation field's choices, searched on the server a page at a time. The
 * same function for the same field and workspace every time: the editor
 * searches again whenever it changes.
 */
export function relationSearch(field: string, workspace: string): RelationSearch | undefined {
  const key = `${workspace}:${field}`;
  if (!searches.has(key)) {
    if (field === "company_id") searches.set(key, (q, after) => searchCompanyChoices(workspace, q, after));
    else if (field === "point_of_contact_id")
      searches.set(key, (q, after) => searchPersonChoices(workspace, q, after));
    else return undefined;
  }
  return searches.get(key);
}

/** A native `<select>` that matches the inputs beside it. */
export const NATIVE_SELECT = "h-8 rounded-md border border-input bg-transparent px-2 text-sm";
