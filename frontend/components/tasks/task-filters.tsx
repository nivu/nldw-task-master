"use client";

import { useEffect, useState } from "react";

import { LinkPicker, type PickedLink } from "@/components/tasks/link-picker";
import { SELECT_CLASS } from "@/components/tasks/task-row";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { MyTasksFilters, TaskPriority } from "@/lib/api/task-types";
import { PRIORITIES, PRIORITY_LABEL } from "@/lib/tasks/labels";

/**
 * My Tasks' filters — spec 008 FR-TASK-LIST-07 (TP-15): tag, priority, link
 * and text. Text is matched once it has two characters.
 */
export function TaskFilters({
  value,
  onChange,
}: {
  value: MyTasksFilters;
  onChange: (filters: MyTasksFilters) => void;
}) {
  const [text, setText] = useState(value.q ?? "");
  const [tag, setTag] = useState(value.tag ?? "");
  const [link, setLink] = useState<PickedLink | null>(null);
  const [picking, setPicking] = useState(false);

  // Typing settles before the list is asked again.
  useEffect(() => {
    const q = text.trim();
    const t = tag.trim().replace(/^#/, "");
    const timer = window.setTimeout(() => {
      const nextQ = q.length >= 2 ? q : undefined;
      const nextTag = t || undefined;
      if (nextQ !== value.q || nextTag !== value.tag) onChange({ ...value, q: nextQ, tag: nextTag });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [text, tag, value, onChange]);

  return (
    <div className="flex flex-wrap items-end gap-2" role="search" aria-label="Filter my tasks">
      <Input
        aria-label="Search my tasks"
        placeholder="Search"
        className="w-44"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <Input
        aria-label="Tag"
        placeholder="#tag"
        className="w-28"
        value={tag}
        onChange={(e) => setTag(e.target.value)}
      />
      <select
        aria-label="Priority"
        className={SELECT_CLASS}
        value={value.priority ?? ""}
        onChange={(e) =>
          onChange({ ...value, priority: (e.target.value || undefined) as TaskPriority | undefined })
        }
      >
        <option value="">Any priority</option>
        {PRIORITIES.map((p) => (
          <option key={p} value={p}>
            {PRIORITY_LABEL[p]}
          </option>
        ))}
      </select>
      {link ? (
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setLink(null);
            onChange({ ...value, link_type: undefined, link_id: undefined });
          }}
        >
          Linked to {link.name} ✕
        </Button>
      ) : (
        <Button variant="outline" size="sm" aria-expanded={picking} onClick={() => setPicking((p) => !p)}>
          Linked to…
        </Button>
      )}
      {picking && !link && (
        <div className="w-full sm:w-72">
          <LinkPicker
            label="Linked to"
            onPick={(picked) => {
              setLink(picked);
              setPicking(false);
              onChange({ ...value, link_type: picked.record_type, link_id: picked.record_id });
            }}
          />
        </div>
      )}
    </div>
  );
}
