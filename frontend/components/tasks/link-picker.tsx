"use client";

import { useEffect, useState } from "react";

import { FOCUS_CLASS } from "@/components/tasks/task-row";
import { Input } from "@/components/ui/input";
import type { TaskLinkIn } from "@/lib/api/task-types";
import { errorMessage } from "@/lib/api/portal";
import { getTaskLinkable } from "@/lib/api/tasks";
import { LINK_TYPE_LABEL } from "@/lib/tasks/labels";
import { cn } from "@/lib/utils";

/** A link chosen in the picker, with the name it was shown by. */
export type PickedLink = TaskLinkIn & { name: string };

/**
 * Choose a CRM record or a TVA project — spec 008 FR-TASK-DATA-06, -13;
 * contracts §1 `GET /tasks/linkable`.
 *
 * Offers CRM records only of workspaces where the caller is an Admin or
 * Member, and projects the caller can see that are not archived.
 */
export function LinkPicker({
  label,
  onPick,
}: {
  label: string;
  onPick: (link: PickedLink) => void;
}) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<PickedLink[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const text = q.trim();

  useEffect(() => {
    if (text.length < 2) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      getTaskLinkable(text)
        .then((out) => {
          if (cancelled) return;
          setFound([
            ...out.records.map((r) => ({
              record_type: r.record_type,
              record_id: r.record_id,
              name: r.name ?? "A record",
            })),
            ...out.projects.map((p) => ({
              record_type: "tva_project" as const,
              record_id: p.id,
              name: p.name,
            })),
          ]);
          setError(null);
        })
        .catch((err) => {
          if (!cancelled) setError(errorMessage(err));
        });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [text]);

  return (
    <div className="space-y-1">
      <Input
        aria-label={label}
        placeholder="Type at least two characters"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {error && <p className="text-xs text-destructive">{error}</p>}
      {found && text.length >= 2 && (
        <ul className="max-h-48 overflow-y-auto rounded-md border" aria-label={`${label}: choices`}>
          {found.length === 0 && <li className="p-2 text-xs text-muted-foreground">Nothing found.</li>}
          {found.map((link) => (
            <li key={`${link.record_type}:${link.record_id}`}>
              <button
                type="button"
                className={cn("w-full px-2 py-1.5 text-left text-sm hover:bg-muted", FOCUS_CLASS)}
                onClick={() => onPick(link)}
              >
                {link.name}
                <span className="ml-2 text-xs text-muted-foreground">
                  {LINK_TYPE_LABEL[link.record_type]}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
