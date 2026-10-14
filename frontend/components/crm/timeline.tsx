"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { getCrmTimeline } from "@/lib/api/crm";
import type { CrmObject, CrmTimelineEntry, CrmTimelineType } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { formatDate, type CrmTimezone } from "@/lib/crm/format";
import { useAsync } from "@/lib/use-async";
import { cn } from "@/lib/utils";

/**
 * A record's timeline — spec 007 FR-CRM-REC-03, -04, FR-CRM-AUD-05.
 *
 * Everything that happened to the record, newest first, filterable by type.
 * Every entry names who did it and whether it came through the web app or
 * Claude. For a Viewer the server has already removed every amount.
 */

const TYPES: { key: CrmTimelineType; label: string }[] = [
  { key: "create", label: "Created" },
  { key: "field", label: "Field changes" },
  { key: "stage", label: "Stage" },
  { key: "link", label: "Drive links" },
  { key: "delete", label: "Trash" },
];

function shown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "empty";
  if (Array.isArray(value)) return value.length ? value.map(shown).join(", ") : "empty";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function Entry({ entry, timeZone }: { entry: CrmTimelineEntry; timeZone: CrmTimezone }) {
  const time = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone }).format(
    new Date(entry.at)
  );
  return (
    <li className="relative border-l pl-4">
      <span className="absolute -left-1 top-1.5 size-2 rounded-full bg-muted-foreground/60" />
      <p className="text-sm">{entry.summary}</p>
      {entry.changes.length > 0 && (
        <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
          {entry.changes.map((change) => (
            <li key={change.field} className="break-words">
              {change.label}: {shown(change.before)} → {shown(change.after)}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-0.5 text-xs text-muted-foreground">
        {entry.actor_name}
        {entry.channel === "mcp" ? " via Claude" : entry.channel === "web" ? " on the web" : ""} ·{" "}
        {formatDate(entry.at, timeZone)}, {time}
      </p>
    </li>
  );
}

export function Timeline({
  workspace,
  object,
  recordId,
  initial,
  timeZone,
  version,
}: {
  workspace: string;
  object: CrmObject;
  recordId: string;
  /** The record page's first ten, shown until a filter or "Older" asks for more. */
  initial: CrmTimelineEntry[];
  timeZone: CrmTimezone;
  /** Bumped by the page after a change, so the timeline reloads. */
  version: number;
}) {
  const [types, setTypes] = useState<CrmTimelineType[]>([]);
  const [older, setOlder] = useState<CrmTimelineEntry[]>([]);
  const [more, setMore] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const filtered = types.length > 0 || version > 0;
  const { data } = useAsync(
    () =>
      filtered
        ? getCrmTimeline(workspace, object, recordId, { types: types.join(",") || undefined, limit: 50 })
        : Promise.resolve(initial),
    [workspace, object, recordId, types.join(","), version]
  );
  const entries = [...(data ?? initial), ...older];

  function toggle(type: CrmTimelineType) {
    setOlder([]);
    setMore(true);
    setTypes((current) =>
      current.includes(type) ? current.filter((t) => t !== type) : [...current, type]
    );
  }

  async function loadOlder() {
    const last = entries[entries.length - 1];
    if (!last) return;
    try {
      const page = await getCrmTimeline(workspace, object, recordId, {
        types: types.join(",") || undefined,
        before: last.id,
        limit: 50,
      });
      setOlder((current) => [...current, ...page]);
      setMore(page.length === 50);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Show only">
        {TYPES.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            aria-pressed={types.includes(key)}
            onClick={() => toggle(key)}
            className={cn(
              "rounded-full border px-2.5 py-0.5 text-xs",
              types.includes(key) ? "border-foreground bg-foreground text-background" : "text-muted-foreground"
            )}
          >
            {label}
          </button>
        ))}
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing yet.</p>
      ) : (
        <ol className="space-y-3">
          {entries.map((entry) => (
            <Entry key={entry.id} entry={entry} timeZone={timeZone} />
          ))}
        </ol>
      )}
      {entries.length >= 10 && more && (
        <Button variant="outline" size="sm" onClick={loadOlder}>
          Older
        </Button>
      )}
    </div>
  );
}
