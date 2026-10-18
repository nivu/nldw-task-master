"use client";

import { useEffect, useState } from "react";

import { FOCUS_CLASS } from "@/components/tasks/task-row";
import { Input } from "@/components/ui/input";
import type { TaskPerson } from "@/lib/api/task-types";
import { errorMessage } from "@/lib/api/portal";
import { getTaskPeople } from "@/lib/api/tasks";
import { cn } from "@/lib/utils";

/**
 * Choose a person for a task — spec 008 FR-TASK-DATA-07, FR-TASK-ROLL-04;
 * contracts §1 `GET /tasks/people`.
 *
 * Offers only the people the caller may assign for this draft: the
 * workspace's members for a CRM task, staff who can see the project for a
 * project task, else everyone with My Tasks switched on.
 */
export function PersonPicker({
  label,
  workspaceId,
  projectId,
  onPick,
}: {
  label: string;
  workspaceId?: string | null;
  projectId?: string | null;
  onPick: (person: TaskPerson) => void;
}) {
  const [q, setQ] = useState("");
  const [people, setPeople] = useState<TaskPerson[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      getTaskPeople({ q: q.trim() || undefined, workspace_id: workspaceId, project_id: projectId })
        .then((found) => {
          if (cancelled) return;
          setPeople(found);
          setError(null);
        })
        .catch((err) => {
          if (!cancelled) setError(errorMessage(err));
        });
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [q, workspaceId, projectId]);

  return (
    <div className="space-y-1">
      <Input
        aria-label={label}
        placeholder="Type a name or email"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {error && <p className="text-xs text-destructive">{error}</p>}
      {people && (
        <ul className="max-h-48 overflow-y-auto rounded-md border" aria-label={`${label}: choices`}>
          {people.length === 0 && <li className="p-2 text-xs text-muted-foreground">No one found.</li>}
          {people.slice(0, 20).map((p) => (
            <li key={p.id}>
              <button
                type="button"
                className={cn("w-full px-2 py-1.5 text-left text-sm hover:bg-muted", FOCUS_CLASS)}
                onClick={() => onPick(p)}
              >
                {p.display_name}
                <span className="block text-xs text-muted-foreground">{p.email}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
