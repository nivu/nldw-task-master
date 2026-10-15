"use client";

import { Marked, type Tokens } from "marked";
import { MessageCircle, Plus, Star, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import type { Address, Links, LinkValue, Phone } from "@/lib/api/crm-types";
import { formatAmount, formatDate, type CrmTimezone, type Currency } from "@/lib/crm/format";
import { cn } from "@/lib/utils";

/**
 * One editor, and one read-only view, per CRM field type — spec 007
 * FR-CRM-LIST-07: text, long text (markdown, with a preview), number,
 * currency, date, date and time, boolean, select, multi-select, rating,
 * emails, phones with a WhatsApp marker, links, domain, address, relation and
 * array of text.
 *
 * Editors hold the value in the shape the API sends and takes (contracts
 * §2.5.1); nothing is checked here that the server does not check again.
 */

export type FieldType =
  | "text"
  | "long_text"
  | "number"
  | "currency"
  | "date"
  | "datetime"
  | "boolean"
  | "select"
  | "multi_select"
  | "rating"
  | "email"
  | "emails"
  | "phones"
  | "links"
  | "domain"
  | "domains"
  | "address"
  | "relation"
  | "array";

export interface Choice {
  value: string;
  label: string;
  /** Inactive options stay on old records but are not offered. */
  inactive?: boolean;
}

export interface FieldSpec {
  name: string;
  label: string;
  type: FieldType;
  /** select, multi-select and relation; currency codes for currency. */
  choices?: Choice[];
  /** relation: asks the server as the person types, a page at a time, for a
   *  relation too large to offer whole (a person's company). `choices` then
   *  only needs to name the current value. */
  search?: RelationSearch;
  /** System-written fields are shown, never edited (FR-CRM-CO-03). */
  readOnly?: boolean;
  required?: boolean;
  placeholder?: string;
}

export type RelationSearch = (
  q: string,
  after: string | null
) => Promise<{ choices: Choice[]; next: string | null }>;

/** currency: an amount (decimal string) and its code. */
export interface Money {
  amount: string | null;
  currency: Currency;
}

const SELECT_CLASS = "h-8 w-full";

export function isEmpty(value: unknown): boolean {
  if (value === null || value === undefined || value === "") return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") {
    return Object.values(value as Record<string, unknown>).every(
      (v) => isEmpty(v) || (typeof v === "object" && v !== null && isEmpty(v))
    );
  }
  return false;
}

// ---------------------------------------------------------------------------
// Markdown — long text is plain text with markdown (Q-27). Raw HTML is shown
// as text, links open only http(s) and mailto addresses, and images are
// reduced to their alt text: what people wrote is data, never markup.
// ---------------------------------------------------------------------------

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const SAFE_HREF = /^(https?:\/\/|mailto:)/i;

const markdown = new Marked({
  gfm: true,
  breaks: true,
  renderer: {
    html({ text }: Tokens.HTML | Tokens.Tag) {
      return escapeHtml(text);
    },
    image({ text }: Tokens.Image) {
      return escapeHtml(text);
    },
    link(this: { parser: { parseInline: (tokens: Tokens.Link["tokens"]) => string } }, token: Tokens.Link) {
      const inner = this.parser.parseInline(token.tokens);
      if (!SAFE_HREF.test(token.href)) return inner;
      return `<a href="${escapeHtml(token.href)}" target="_blank" rel="noopener noreferrer">${inner}</a>`;
    },
  },
});

export function renderMarkdown(text: string): string {
  return markdown.parse(text, { async: false }) as string;
}

export function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div
      className={cn("help-prose max-w-none text-sm [&_p]:my-1", className)}
      dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }}
    />
  );
}

// ---------------------------------------------------------------------------
// Read-only view
// ---------------------------------------------------------------------------

function whatsappHref(phone: Phone): string {
  return `https://wa.me/${(phone.country_code + phone.number).replace(/[^0-9]/g, "")}`;
}

function ExternalLink({ link }: { link: LinkValue }) {
  if (!/^https?:\/\//i.test(link.url)) return <span>{link.label || link.url}</span>;
  return (
    <a
      href={link.url}
      target="_blank"
      rel="noopener noreferrer"
      className="break-all text-primary underline-offset-4 hover:underline"
    >
      {link.label || link.url}
    </a>
  );
}

export function FieldValue({
  spec,
  value,
  timeZone,
}: {
  spec: FieldSpec;
  value: unknown;
  timeZone: CrmTimezone;
}) {
  if (isEmpty(value)) return <span className="text-muted-foreground">—</span>;
  const label = (v: string) => spec.choices?.find((c) => c.value === v)?.label ?? v;

  switch (spec.type) {
    case "long_text":
      return <Markdown text={String(value)} />;
    case "boolean":
      return <span>{value ? "Yes" : "No"}</span>;
    case "date":
      return <span>{formatDate(String(value), timeZone)}</span>;
    case "datetime": {
      const when = new Date(String(value));
      const time = new Intl.DateTimeFormat("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
        timeZone,
      }).format(when);
      return (
        <span>
          {formatDate(String(value), timeZone)}, {time}
        </span>
      );
    }
    case "currency": {
      const money = value as Money;
      return <span>{money.amount ? formatAmount(money.amount, money.currency) : "—"}</span>;
    }
    case "rating":
      return <span aria-label={`${value} of 5`}>{"★".repeat(Number(value))}</span>;
    case "select":
    case "relation":
      return <span>{label(String(value))}</span>;
    case "multi_select":
      return (
        <span className="flex flex-wrap gap-1">
          {(value as string[]).map((v) => (
            <span key={v} className="rounded-full bg-muted px-2 py-0.5 text-xs">
              {label(v)}
            </span>
          ))}
        </span>
      );
    case "email":
      return (
        <a href={`mailto:${value}`} className="break-all text-primary hover:underline">
          {String(value)}
        </a>
      );
    case "emails":
    case "domains":
    case "array":
      return <span className="break-all">{(value as string[]).join(", ")}</span>;
    case "phones":
      return (
        <ul className="space-y-0.5">
          {(value as Phone[]).map((phone, i) => (
            <li key={i} className="flex items-center gap-2">
              <a href={`tel:${phone.country_code}${phone.number.replace(/\s/g, "")}`}>
                {phone.country_code} {phone.number}
              </a>
              {phone.whatsapp && (
                <a
                  href={whatsappHref(phone)}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label="Open in WhatsApp"
                  className="text-green-700 dark:text-green-400"
                >
                  <MessageCircle className="size-3.5" />
                </a>
              )}
            </li>
          ))}
        </ul>
      );
    case "links": {
      const links = value as Links;
      const all = [links.primary, ...(links.others ?? [])].filter(Boolean) as LinkValue[];
      return (
        <span className="flex flex-col gap-0.5">
          {all.map((link, i) => (
            <ExternalLink key={i} link={link} />
          ))}
        </span>
      );
    }
    case "address": {
      const a = value as Address;
      return (
        <span className="whitespace-pre-line">
          {[a.street, [a.city, a.state, a.postal_code].filter(Boolean).join(" "), a.country]
            .filter(Boolean)
            .join("\n")}
        </span>
      );
    }
    default:
      return <span className="break-words">{String(value)}</span>;
  }
}

// ---------------------------------------------------------------------------
// Editors
// ---------------------------------------------------------------------------

function ChoiceSelect({
  id,
  value,
  choices,
  onChange,
  placeholder = "None",
  allowNone = true,
}: {
  id?: string;
  value: string | null;
  choices: Choice[];
  onChange: (value: string | null) => void;
  placeholder?: string;
  allowNone?: boolean;
}) {
  const offered = choices.filter((c) => !c.inactive || c.value === value);
  const items = [
    ...(allowNone ? [{ value: "", label: placeholder }] : []),
    ...offered.map((c) => ({ value: c.value, label: c.label })),
  ];
  return (
    <Select
      items={items}
      value={value ?? ""}
      onValueChange={(next) => onChange(next ? String(next) : null)}
    >
      <SelectTrigger id={id} className={SELECT_CLASS}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {items.map((item) => (
          <SelectItem key={item.value || "none"} value={item.value}>
            {item.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function SearchedRelation({
  id,
  value,
  known,
  search,
  allowNone,
  label,
  onChange,
}: {
  id?: string;
  value: string | null;
  known: Choice[];
  search: RelationSearch;
  allowNone: boolean;
  label: string;
  onChange: (value: string | null) => void;
}) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Choice[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [named, setNamed] = useState<Choice[]>(known);

  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      search(q.trim(), null)
        .then((page) => {
          if (!live) return;
          setFound(page.choices);
          setNext(page.next);
          setError(null);
        })
        .catch(() => live && setError("Could not search; try again."));
    }, 250);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [q, search]);

  async function more() {
    try {
      const page = await search(q.trim(), next);
      setFound((current) => [...current, ...page.choices]);
      setNext(page.next);
    } catch {
      setError("Could not search; try again.");
    }
  }

  function choose(choice: Choice | null) {
    if (choice) setNamed((current) => [...current, choice]);
    onChange(choice ? choice.value : null);
  }

  const current = value ? [...named, ...found].find((c) => c.value === value) : undefined;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2 text-sm">
        <span className={cn(!value && "text-muted-foreground")}>
          {value ? (current?.label ?? "Chosen") : "None"}
        </span>
        {value && allowNone && (
          <Button type="button" variant="ghost" size="sm" onClick={() => choose(null)}>
            Clear
          </Button>
        )}
      </div>
      <Input
        id={id}
        type="search"
        placeholder={`Search ${label.toLowerCase()}`}
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {error && <p className="text-xs text-destructive">{error}</p>}
      <ul className="max-h-48 overflow-y-auto rounded-md border text-sm">
        {found.length === 0 ? (
          <li className="p-2 text-muted-foreground">No matches.</li>
        ) : (
          found.map((choice) => (
            <li key={choice.value}>
              <button
                type="button"
                aria-pressed={choice.value === value}
                className={cn(
                  "w-full px-2 py-1 text-left hover:bg-muted",
                  choice.value === value && "bg-muted font-medium"
                )}
                onClick={() => choose(choice)}
              >
                {choice.label}
              </button>
            </li>
          ))
        )}
        {next && (
          <li>
            <button type="button" className="w-full px-2 py-1 text-left text-muted-foreground hover:bg-muted" onClick={more}>
              More…
            </button>
          </li>
        )}
      </ul>
    </div>
  );
}

function TextList({
  value,
  onChange,
  type = "text",
  placeholder,
}: {
  value: string[];
  onChange: (value: string[]) => void;
  type?: string;
  placeholder?: string;
}) {
  return (
    <div className="space-y-1.5">
      {value.map((item, i) => (
        <div key={i} className="flex gap-1.5">
          <Input
            type={type}
            value={item}
            placeholder={placeholder}
            onChange={(e) => onChange(value.map((v, j) => (j === i ? e.target.value : v)))}
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Remove"
            onClick={() => onChange(value.filter((_, j) => j !== i))}
          >
            <X className="size-4" />
          </Button>
        </div>
      ))}
      <Button type="button" variant="outline" size="sm" onClick={() => onChange([...value, ""])}>
        <Plus className="size-3.5" /> Add
      </Button>
    </div>
  );
}

function LinkRow({
  link,
  onChange,
  onRemove,
}: {
  link: LinkValue;
  onChange: (link: LinkValue) => void;
  onRemove?: () => void;
}) {
  return (
    <div className="flex flex-col gap-1.5 sm:flex-row">
      <Input
        type="url"
        placeholder="https://"
        value={link.url}
        onChange={(e) => onChange({ ...link, url: e.target.value })}
      />
      <div className="flex gap-1.5">
        <Input
          placeholder="Label"
          value={link.label ?? ""}
          onChange={(e) => onChange({ ...link, label: e.target.value || null })}
        />
        {onRemove && (
          <Button type="button" variant="ghost" size="icon" aria-label="Remove" onClick={onRemove}>
            <X className="size-4" />
          </Button>
        )}
      </div>
    </div>
  );
}

function LongText({ id, value, onChange }: { id?: string; value: string; onChange: (v: string) => void }) {
  const [preview, setPreview] = useState(false);
  return (
    <div className="space-y-1.5">
      <div className="flex gap-1 text-xs">
        <button
          type="button"
          className={cn("rounded px-2 py-0.5", !preview && "bg-muted font-medium")}
          onClick={() => setPreview(false)}
        >
          Write
        </button>
        <button
          type="button"
          className={cn("rounded px-2 py-0.5", preview && "bg-muted font-medium")}
          onClick={() => setPreview(true)}
        >
          Preview
        </button>
      </div>
      {preview ? (
        <div className="min-h-24 rounded-md border p-2">
          {value.trim() ? <Markdown text={value} /> : <span className="text-sm text-muted-foreground">Nothing to preview.</span>}
        </div>
      ) : (
        <Textarea id={id} rows={5} value={value} onChange={(e) => onChange(e.target.value)} />
      )}
      <p className="text-xs text-muted-foreground">Markdown: **bold**, _italic_, lists and links.</p>
    </div>
  );
}

/** `datetime-local` wants local wall time; the API wants an instant. */
function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function FieldEditor({
  spec,
  value,
  onChange,
  id,
}: {
  spec: FieldSpec;
  value: unknown;
  onChange: (value: unknown) => void;
  id?: string;
}) {
  const text = (value as string | null) ?? "";

  switch (spec.type) {
    case "long_text":
      return <LongText id={id} value={text} onChange={(v) => onChange(v || null)} />;
    case "number":
      return (
        <Input
          id={id}
          inputMode="decimal"
          value={text}
          onChange={(e) => onChange(e.target.value.trim() || null)}
        />
      );
    case "currency": {
      const money = (value as Money | null) ?? { amount: null, currency: "INR" as Currency };
      return (
        <div className="flex gap-1.5">
          <Input
            id={id}
            inputMode="decimal"
            value={money.amount ?? ""}
            onChange={(e) => onChange({ ...money, amount: e.target.value.trim() || null })}
          />
          <div className="w-28 shrink-0">
            <ChoiceSelect
              value={money.currency}
              allowNone={false}
              choices={(spec.choices ?? ["INR", "AUD", "USD"].map((c) => ({ value: c, label: c }))) as Choice[]}
              onChange={(c) => onChange({ ...money, currency: (c ?? "INR") as Currency })}
            />
          </div>
        </div>
      );
    }
    case "date":
      return <Input id={id} type="date" value={text} onChange={(e) => onChange(e.target.value || null)} />;
    case "datetime":
      return (
        <Input
          id={id}
          type="datetime-local"
          value={toLocalInput(value as string | null)}
          onChange={(e) => onChange(e.target.value ? new Date(e.target.value).toISOString() : null)}
        />
      );
    case "boolean":
      return (
        <label className="flex items-center gap-2 text-sm">
          <Checkbox id={id} checked={Boolean(value)} onCheckedChange={(checked) => onChange(Boolean(checked))} />
          {spec.label}
        </label>
      );
    case "relation":
      if (spec.search) {
        return (
          <SearchedRelation
            id={id}
            value={(value as string | null) ?? null}
            known={spec.choices ?? []}
            search={spec.search}
            allowNone={!spec.required}
            label={spec.label}
            onChange={onChange}
          />
        );
      }
      return (
        <ChoiceSelect
          id={id}
          value={(value as string | null) ?? null}
          choices={spec.choices ?? []}
          allowNone={!spec.required}
          onChange={onChange}
        />
      );
    case "select":
      return (
        <ChoiceSelect
          id={id}
          value={(value as string | null) ?? null}
          choices={spec.choices ?? []}
          allowNone={!spec.required}
          onChange={onChange}
        />
      );
    case "multi_select": {
      const chosen = (value as string[] | null) ?? [];
      const offered = (spec.choices ?? []).filter((c) => !c.inactive || chosen.includes(c.value));
      return (
        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
          {offered.map((choice) => (
            <label key={choice.value} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={chosen.includes(choice.value)}
                onCheckedChange={(checked) =>
                  onChange(
                    checked ? [...chosen, choice.value] : chosen.filter((v) => v !== choice.value)
                  )
                }
              />
              {choice.label}
            </label>
          ))}
        </div>
      );
    }
    case "rating": {
      const rating = Number(value) || 0;
      return (
        <div className="flex gap-0.5" role="radiogroup" aria-label={spec.label}>
          {[1, 2, 3, 4, 5].map((n) => (
            <button
              key={n}
              type="button"
              role="radio"
              aria-checked={rating === n}
              aria-label={`${n} of 5`}
              onClick={() => onChange(rating === n ? null : n)}
            >
              <Star className={cn("size-5", n <= rating ? "fill-amber-400 text-amber-500" : "text-muted-foreground")} />
            </button>
          ))}
        </div>
      );
    }
    case "emails":
      return <TextList type="email" value={(value as string[] | null) ?? []} onChange={onChange} />;
    case "domains":
      return <TextList value={(value as string[] | null) ?? []} placeholder="acme.com" onChange={onChange} />;
    case "array":
      return <TextList value={(value as string[] | null) ?? []} onChange={onChange} />;
    case "phones": {
      const phones = (value as Phone[] | null) ?? [];
      const set = (i: number, phone: Phone) => onChange(phones.map((p, j) => (j === i ? phone : p)));
      return (
        <div className="space-y-1.5">
          {phones.map((phone, i) => (
            <div key={i} className="flex flex-wrap items-center gap-1.5">
              <Input
                aria-label="Country code"
                className="w-20"
                value={phone.country_code}
                onChange={(e) => set(i, { ...phone, country_code: e.target.value })}
              />
              <Input
                aria-label="Number"
                className="min-w-0 flex-1"
                inputMode="tel"
                value={phone.number}
                onChange={(e) => set(i, { ...phone, number: e.target.value })}
              />
              <label className="flex items-center gap-1.5 text-xs">
                <Checkbox
                  checked={phone.whatsapp}
                  onCheckedChange={(checked) => set(i, { ...phone, whatsapp: Boolean(checked) })}
                />
                WhatsApp
              </label>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label="Remove"
                onClick={() => onChange(phones.filter((_, j) => j !== i))}
              >
                <X className="size-4" />
              </Button>
            </div>
          ))}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onChange([...phones, { country_code: "+91", number: "", whatsapp: false }])}
          >
            <Plus className="size-3.5" /> Add phone
          </Button>
        </div>
      );
    }
    case "links": {
      const links = (value as Links | null) ?? {};
      const others = links.others ?? [];
      return (
        <div className="space-y-1.5">
          <LinkRow
            link={links.primary ?? { url: "" }}
            onChange={(primary) => onChange({ ...links, primary: primary.url ? primary : null, others })}
          />
          {others.map((link, i) => (
            <LinkRow
              key={i}
              link={link}
              onChange={(next) => onChange({ ...links, others: others.map((o, j) => (j === i ? next : o)) })}
              onRemove={() => onChange({ ...links, others: others.filter((_, j) => j !== i) })}
            />
          ))}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onChange({ ...links, others: [...others, { url: "" }] })}
          >
            <Plus className="size-3.5" /> Another link
          </Button>
        </div>
      );
    }
    case "address": {
      const address = (value as Address | null) ?? {};
      const part = (key: keyof Address, label: string) => (
        <Input
          aria-label={label}
          placeholder={label}
          value={address[key] ?? ""}
          onChange={(e) => onChange({ ...address, [key]: e.target.value || null })}
        />
      );
      return (
        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
          <div className="sm:col-span-2">{part("street", "Street")}</div>
          {part("city", "City")}
          {part("state", "State")}
          {part("postal_code", "Postal code")}
          {part("country", "Country")}
        </div>
      );
    }
    case "email":
      return <Input id={id} type="email" value={text} onChange={(e) => onChange(e.target.value.trim() || null)} />;
    case "domain":
      return (
        <Input
          id={id}
          value={text}
          placeholder={spec.placeholder ?? "acme.com"}
          onChange={(e) => onChange(e.target.value.trim() || null)}
        />
      );
    default:
      return (
        <Input
          id={id}
          value={text}
          placeholder={spec.placeholder}
          onChange={(e) => onChange(e.target.value || null)}
        />
      );
  }
}

/** What an editor's value becomes on the wire: blanks dropped from lists
 *  and links, so an empty row is never saved. */
export function cleanValue(type: FieldType, value: unknown): unknown {
  if (type === "emails" || type === "domains" || type === "array") {
    return ((value as string[] | null) ?? []).map((v) => v.trim()).filter(Boolean);
  }
  if (type === "phones") {
    return ((value as Phone[] | null) ?? []).filter((p) => p.number.trim());
  }
  if (type === "links") {
    const links = (value as Links | null) ?? {};
    const others = (links.others ?? []).filter((l) => l.url.trim());
    if (!links.primary?.url && others.length === 0) return null;
    return { primary: links.primary?.url ? links.primary : null, others };
  }
  if (type === "address") return isEmpty(value) ? null : value;
  if (typeof value === "string") return value.trim() === "" ? null : value;
  return value;
}
