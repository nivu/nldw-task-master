"use client";

import {
  DndContext,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  useDroppable,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
  type UniqueIdentifier,
} from "@dnd-kit/core";
import { useState } from "react";

import { FOCUS_CLASS, TaskRow } from "@/components/tasks/task-row";
import { Button } from "@/components/ui/button";
import type { TaskBucket, TaskOut } from "@/lib/api/task-types";
import { BUCKET_LABEL, SECTIONS } from "@/lib/tasks/labels";
import { cn } from "@/lib/utils";

/**
 * My Tasks' four sections and the closed list — spec 008 FR-TASK-LIST-01,
 * -02, -04, -05.
 *
 * Today, This week, This month and Later, by effective bucket (a task due
 * today or earlier is in Today whatever its bucket); a drag to another
 * section, or the section menu on the row, changes the bucket only. Done and
 * cancelled tasks are collapsed at the bottom, newest first, each with Undo.
 */

const SECTION_PREFIX = "section:";

function Section({
  bucket,
  tasks,
  children,
}: {
  bucket: TaskBucket;
  tasks: TaskOut[];
  children: React.ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: `${SECTION_PREFIX}${bucket}` });
  const heading = `tasks-section-${bucket}`;
  return (
    <section
      ref={setNodeRef}
      aria-labelledby={heading}
      data-testid={`section-${bucket}`}
      className={cn("rounded-lg border p-3", isOver && "border-ring bg-muted/40")}
    >
      <h2 id={heading} className="mb-2 flex items-baseline gap-2 text-sm font-semibold">
        {BUCKET_LABEL[bucket]}
        <span className="text-xs font-normal text-muted-foreground">{tasks.length}</span>
      </h2>
      {tasks.length === 0 ? (
        <p className="text-xs text-muted-foreground">Nothing here.</p>
      ) : (
        <ul className="space-y-1.5">{children}</ul>
      )}
    </section>
  );
}

export function TaskSections({
  sections,
  closed,
  hasMoreClosed,
  onOpen,
  onComplete,
  onReopen,
  onMove,
  onMoreClosed,
}: {
  sections: Record<TaskBucket, TaskOut[]>;
  closed: TaskOut[];
  hasMoreClosed: boolean;
  onOpen: (task: TaskOut) => void;
  onComplete: (task: TaskOut) => void;
  onReopen: (task: TaskOut) => void;
  onMove: (task: TaskOut, bucket: TaskBucket) => void;
  onMoreClosed: () => void;
}) {
  const [showClosed, setShowClosed] = useState(false);
  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 8 } }),
    useSensor(KeyboardSensor)
  );
  const all = SECTIONS.flatMap((s) => sections[s]);
  const nameOf = (id: UniqueIdentifier) => all.find((t) => t.id === id)?.title ?? "The task";
  const targetOf = (id: UniqueIdentifier | undefined): TaskBucket | null => {
    const text = String(id ?? "");
    return text.startsWith(SECTION_PREFIX) ? (text.slice(SECTION_PREFIX.length) as TaskBucket) : null;
  };
  const announcements: Announcements = {
    onDragStart: ({ active }) => `Picked up ${nameOf(active.id)}.`,
    onDragOver: ({ active, over }) => {
      const to = targetOf(over?.id);
      return to ? `${nameOf(active.id)} is over ${BUCKET_LABEL[to]}.` : `${nameOf(active.id)} is over no section.`;
    },
    onDragEnd: ({ active, over }) => {
      const to = targetOf(over?.id);
      return to ? `${nameOf(active.id)} was moved to ${BUCKET_LABEL[to]}.` : `${nameOf(active.id)} was put back.`;
    },
    onDragCancel: ({ active }) => `Moving ${nameOf(active.id)} was cancelled.`,
  };

  function onDragEnd({ active, over }: DragEndEvent) {
    const task = all.find((t) => t.id === active.id);
    const to = targetOf(over?.id);
    if (!task || !to || to === (task.effective_bucket ?? task.bucket)) return;
    onMove(task, to);
  }

  return (
    <div className="space-y-3">
      <DndContext sensors={sensors} onDragEnd={onDragEnd} accessibility={{ announcements }}>
        {SECTIONS.map((bucket) => (
          <Section key={bucket} bucket={bucket} tasks={sections[bucket]}>
            {sections[bucket].map((task) => (
              <TaskRow
                key={task.id}
                task={task}
                onOpen={onOpen}
                onComplete={onComplete}
                onMove={onMove}
              />
            ))}
          </Section>
        ))}
      </DndContext>

      <section aria-labelledby="tasks-section-closed" className="rounded-lg border p-3">
        <h2 id="tasks-section-closed" className="text-sm font-semibold">
          <button
            type="button"
            aria-expanded={showClosed}
            onClick={() => setShowClosed((s) => !s)}
            className={cn("flex items-baseline gap-2", FOCUS_CLASS)}
          >
            Done and cancelled
            <span className="text-xs font-normal text-muted-foreground">
              {showClosed ? "Hide" : "Show"}
            </span>
          </button>
        </h2>
        {showClosed && (
          <div className="mt-2 space-y-2">
            {closed.length === 0 ? (
              <p className="text-xs text-muted-foreground">Nothing finished yet.</p>
            ) : (
              <ul className="space-y-1.5">
                {closed.map((task) => (
                  <TaskRow key={task.id} task={task} onOpen={onOpen} onReopen={onReopen} />
                ))}
              </ul>
            )}
            {hasMoreClosed && (
              <Button variant="outline" size="sm" onClick={onMoreClosed}>
                Show more
              </Button>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
