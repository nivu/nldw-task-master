"use client";

import { useEffect, useState } from "react";
import { Eye } from "lucide-react";

import type { TaskAudienceIn } from "@/lib/api/task-types";
import { errorMessage } from "@/lib/api/portal";
import { getTaskAudience } from "@/lib/api/tasks";

/**
 * Who will see a task — spec 008 FR-TASK-VIS-08, §3.2.
 *
 * With a `draft` (an assignee or link not yet saved), the sentence is asked
 * of `POST /tasks/audience` before anything is saved; otherwise the saved
 * task's own `text` is shown.
 */
export function VisibleTo({ text, draft }: { text?: string; draft?: TaskAudienceIn }) {
  const [asked, setAsked] = useState<{ key: string; text: string } | null>(null);
  const key = draft ? JSON.stringify(draft) : "";

  useEffect(() => {
    if (!key) return;
    let cancelled = false;
    getTaskAudience(JSON.parse(key) as TaskAudienceIn)
      .then((out) => {
        if (!cancelled) setAsked({ key, text: out.visible_to });
      })
      .catch((err) => {
        if (!cancelled) setAsked({ key, text: errorMessage(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [key]);

  const shown = key ? (asked?.key === key ? asked.text : "Checking who will see it…") : text;
  if (!shown) return null;
  return (
    <p className="flex items-center gap-1.5 text-xs text-muted-foreground" data-testid="visible-to">
      <Eye className="size-3.5 shrink-0" aria-hidden />
      <span aria-live="polite">{shown}</span>
    </p>
  );
}
