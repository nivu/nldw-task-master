"use client";

import { useEffect, useState } from "react";

import { TASK_CREATED_EVENT } from "@/components/tasks/quick-add";
import { Button } from "@/components/ui/button";
import type { InboxItem, TaskBucket, TaskOut, TaskReview } from "@/lib/api/task-types";
import { errorMessage } from "@/lib/api/portal";
import { dismissTaskReview, getTaskReview, makeTaskFromInbox, updateTask } from "@/lib/api/tasks";
import { BUCKET_LABEL, dueText, SECTIONS } from "@/lib/tasks/labels";

function days(n: number): string {
  return n === 1 ? "1 day" : `${n} days`;
}

/** One tap per section the row is not in already. */
function Moves({
  label,
  current,
  disabled,
  onMove,
}: {
  label: string;
  current: TaskBucket | null;
  disabled: boolean;
  onMove: (bucket: TaskBucket) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1" role="group" aria-label={`Move ${label}`}>
      {SECTIONS.filter((s) => s !== current).map((s) => (
        <Button key={s} type="button" size="xs" variant="outline" disabled={disabled} onClick={() => onMove(s)}>
          {BUCKET_LABEL[s]}
        </Button>
      ))}
    </div>
  );
}

function Group({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <h3 className="flex items-baseline gap-2 text-xs font-semibold">
        {title}
        <span className="font-normal text-muted-foreground">{count}</span>
      </h3>
      {count === 0 ? (
        <p className="text-xs text-muted-foreground">Nothing here.</p>
      ) : (
        <ul className="space-y-1.5">{children}</ul>
      )}
    </div>
  );
}

/**
 * The weekly review — spec 008 FR-TASK-REVIEW-01; TP-08.
 *
 * On the reader's Monday, My Tasks opens with this panel: their This week
 * and This month tasks, waiting tasks older than 7 days and unprocessed
 * inbox items, each with one tap per section to move it (an inbox item
 * becomes a task in that section). Dismiss hides it until next Monday.
 * Shown here only, never sent.
 */
export function WeeklyReview({ onChanged }: { onChanged: () => void }) {
  const [review, setReview] = useState<TaskReview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getTaskReview()
      .then((out) => {
        if (!cancelled) setReview(out);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!review?.show) {
    return error ? (
      <p role="alert" className="text-sm text-destructive">
        {error}
      </p>
    ) : null;
  }

  async function run(id: string, write: () => Promise<void>) {
    setActing(id);
    setError(null);
    try {
      await write();
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setActing(null);
    }
  }

  function moveTask(task: TaskOut, bucket: TaskBucket) {
    return run(task.id, async () => {
      const moved = await updateTask(task.id, { bucket, expect_updated_at: task.updated_at });
      setReview((r) => {
        if (!r) return r;
        const without = (list: TaskOut[]) => list.filter((t) => t.id !== task.id);
        const swap = <T extends TaskOut>(list: T[]) =>
          list.map((t) => (t.id === task.id ? { ...t, ...moved } : t));
        return {
          ...r,
          this_week: bucket === "this_week" ? [...without(r.this_week), moved] : without(r.this_week),
          this_month: bucket === "this_month" ? [...without(r.this_month), moved] : without(r.this_month),
          waiting_over_7_days: swap(r.waiting_over_7_days),
        };
      });
    });
  }

  function moveItem(item: InboxItem, bucket: TaskBucket) {
    return run(item.id, async () => {
      const out = await makeTaskFromInbox(item.id, { title: item.title, bucket, tags: item.tags });
      window.dispatchEvent(new CustomEvent(TASK_CREATED_EVENT, { detail: out.task }));
      setReview((r) => (r ? { ...r, inbox: r.inbox.filter((i) => i.id !== item.id) } : r));
    });
  }

  async function dismiss() {
    setError(null);
    try {
      await dismissTaskReview();
      setReview((r) => (r ? { ...r, show: false } : r));
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const row = (task: TaskOut, meta?: string) => (
    <li key={task.id} className="space-y-1 rounded-md border bg-background px-2 py-1.5 text-sm">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="min-w-0 break-words font-medium">{task.title}</span>
        {meta && <span className="text-xs text-muted-foreground">{meta}</span>}
        {task.due_on && <span className="text-xs text-muted-foreground">Due {dueText(task.due_on)}</span>}
      </div>
      <Moves
        label={task.title}
        current={task.effective_bucket ?? task.bucket}
        disabled={acting !== null}
        onMove={(b) => moveTask(task, b)}
      />
    </li>
  );

  return (
    <section aria-labelledby="weekly-review" className="space-y-3 rounded-lg border p-3" data-testid="weekly-review">
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 id="weekly-review" className="text-sm font-semibold">
          Weekly review
        </h2>
        <span className="text-xs text-muted-foreground">Week of {dueText(review.week_start)}</span>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={dismiss}>
          Dismiss for this week
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <Group title="This week" count={review.this_week.length}>
        {review.this_week.map((t) => row(t))}
      </Group>
      <Group title="This month" count={review.this_month.length}>
        {review.this_month.map((t) => row(t))}
      </Group>
      <Group title="Waiting over 7 days" count={review.waiting_over_7_days.length}>
        {review.waiting_over_7_days.map((t) =>
          row(t, `Waiting on ${t.waiting_on?.label ?? "someone"}, ${days(t.age_days)} old`)
        )}
      </Group>
      <Group title="Inbox" count={review.inbox.length}>
        {review.inbox.map((item) => (
          <li key={item.id} className="space-y-1 rounded-md border bg-background px-2 py-1.5 text-sm">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="rounded bg-muted px-1.5 py-0.5 text-xs">{item.kind_label}</span>
              <span className="min-w-0 break-words font-medium">{item.title}</span>
            </div>
            <Moves label={item.title} current={null} disabled={acting !== null} onMove={(b) => moveItem(item, b)} />
          </li>
        ))}
      </Group>
    </section>
  );
}
