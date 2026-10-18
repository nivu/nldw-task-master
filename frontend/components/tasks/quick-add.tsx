"use client";

import { useEffect, useRef, useState } from "react";
import { Plus } from "lucide-react";

import { PersonPicker } from "@/components/tasks/person-picker";
import { FOCUS_CLASS } from "@/components/tasks/task-row";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { QuickAddIn, QuickAddPreview, TaskOut } from "@/lib/api/task-types";
import { errorMessage } from "@/lib/api/portal";
import { createTask, previewQuickAdd } from "@/lib/api/tasks";
import { BUCKET_LABEL, dueText } from "@/lib/tasks/labels";
import { cn } from "@/lib/utils";

/**
 * The quick-add box — spec 008 FR-TASK-ADD-01 to -04, -06, NFR-TASK-06;
 * plan §5.6.
 *
 * One line, previewed by the backend's fixed rules (no AI) 250 ms after
 * typing stops and after each answer: title, due date with its weekday,
 * bucket, tags, assignee and who will see it. A question (an ambiguous
 * date, an `@name` that matches no one or several) is answered by a choice;
 * Save stays disabled while one is open. Enter saves. `q` anywhere outside a
 * text field puts the cursor here. A saved task is announced as a
 * `tasks:created` window event, so an open My Tasks page shows it at once.
 *
 * In a collapsed sidebar the box is a button: it, or `q`, expands the
 * sidebar (`onExpand`) and puts the cursor in the box.
 */

export const TASK_CREATED_EVENT = "tasks:created";

/** Focus is in something a person types into, where `q` is a letter. */
function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
    target.getAttribute("role") === "textbox" ||
    target.getAttribute("role") === "combobox"
  );
}

export function QuickAdd({
  className,
  collapsed = false,
  onExpand,
}: {
  className?: string;
  collapsed?: boolean;
  onExpand?: () => void;
}) {
  const [text, setText] = useState("");
  const [answers, setAnswers] = useState<NonNullable<QuickAddIn["answers"]>>({});
  const [preview, setPreview] = useState<{ key: string; out: QuickAddPreview } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [otherDate, setOtherDate] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const expand = useRef(onExpand);
  const focusOnExpand = useRef(false);
  const line = text.trim();
  const key = JSON.stringify({ line, answers });

  useEffect(() => {
    expand.current = onExpand;
  }, [onExpand]);

  useEffect(() => {
    if (collapsed || !focusOnExpand.current) return;
    focusOnExpand.current = false;
    input.current?.focus();
  }, [collapsed]);

  function open() {
    focusOnExpand.current = true;
    expand.current?.();
  }

  // NFR-TASK-06 — the shortcut.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "q" || e.metaKey || e.ctrlKey || e.altKey || typing(e.target)) return;
      if (input.current && input.current.offsetParent !== null) {
        e.preventDefault();
        input.current.focus();
      } else if (opener.current && opener.current.offsetParent !== null) {
        e.preventDefault();
        focusOnExpand.current = true;
        expand.current?.();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!line) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      const body: QuickAddIn = { text: line };
      if (answers.date || answers.assignee_id) body.answers = answers;
      previewQuickAdd(body)
        .then((out) => {
          if (cancelled) return;
          setPreview({ key, out });
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
  }, [line, answers, key]);

  const current = line && preview?.key === key ? preview.out : null;
  const canSave =
    current !== null && current.questions.length === 0 && current.draft.title.length > 0 && !saving;

  async function save() {
    if (!current || !canSave) return;
    const { draft } = current;
    setSaving(true);
    setError(null);
    try {
      const task = await createTask({
        title: draft.title,
        due_on: draft.due_on,
        bucket: draft.bucket,
        tags: draft.tags,
        assignee_id: draft.assignee.id,
      });
      window.dispatchEvent(new CustomEvent<TaskOut>(TASK_CREATED_EVENT, { detail: task }));
      setText("");
      setAnswers({});
      setPreview(null);
      setOtherDate("");
      setSaved(`Added “${task.title}”.`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  if (collapsed) {
    return (
      <div className={className} data-testid="quick-add">
        <Button
          ref={opener}
          variant="ghost"
          size="icon"
          aria-label="Quick add a task (shortcut q)"
          aria-keyshortcuts="q"
          title="Add a task (q)"
          onClick={open}
        >
          <Plus className="size-4" />
        </Button>
      </div>
    );
  }

  return (
    <div className={cn("relative", className)} data-testid="quick-add">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save();
        }}
        className="flex items-center gap-1"
      >
        <Input
          ref={input}
          aria-label="Quick add a task (shortcut q)"
          aria-keyshortcuts="q"
          placeholder="Add a task… (q)"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setAnswers({});
            setSaved(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              setText("");
              setAnswers({});
              input.current?.blur();
            }
          }}
        />
        <Button type="submit" size="icon" aria-label="Save task" disabled={!canSave}>
          <Plus className="size-4" />
        </Button>
      </form>

      <p className="sr-only" aria-live="polite">
        {saved ?? ""}
      </p>

      {line && (current || error) && (
        <div
          className="absolute left-0 top-full z-50 mt-1 w-80 max-w-[calc(100vw-2rem)] space-y-2 rounded-lg border bg-background p-3 text-sm shadow-lg"
          data-testid="quick-add-preview"
        >
          {error && (
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
          )}
          {current && (
            <>
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                <dt className="text-muted-foreground">Title</dt>
                <dd>{current.draft.title || "—"}</dd>
                <dt className="text-muted-foreground">Due</dt>
                <dd>
                  {current.draft.due_on
                    ? `${current.draft.due_weekday} ${dueText(current.draft.due_on).slice(4)}`
                    : "No date"}
                </dd>
                <dt className="text-muted-foreground">Bucket</dt>
                <dd>{BUCKET_LABEL[current.draft.bucket]}</dd>
                <dt className="text-muted-foreground">Tags</dt>
                <dd>{current.draft.tags.length ? current.draft.tags.map((t) => `#${t}`).join(" ") : "None"}</dd>
                <dt className="text-muted-foreground">For</dt>
                <dd>{current.draft.assignee.name}</dd>
              </dl>
              <p className="text-xs text-muted-foreground" data-testid="quick-add-visible-to">
                {current.visible_to}
              </p>

              {current.questions.map((q) => (
                <fieldset key={`${q.kind}-${q.prompt}`} className="space-y-1.5 rounded-md border p-2">
                  <legend className="px-1 text-xs font-medium">{q.prompt}</legend>
                  <div className="flex flex-wrap gap-1">
                    {q.options.map((o) => (
                      <Button
                        key={o.value}
                        type="button"
                        variant="outline"
                        size="xs"
                        onClick={() =>
                          setAnswers((a) =>
                            q.kind === "date" ? { ...a, date: o.value } : { ...a, assignee_id: o.value }
                          )
                        }
                      >
                        {o.label}
                      </Button>
                    ))}
                  </div>
                  {q.kind === "date" && (
                    <div className="flex items-center gap-1">
                      <Input
                        type="date"
                        aria-label="Another date"
                        className="h-7 text-xs"
                        value={otherDate}
                        onChange={(e) => setOtherDate(e.target.value)}
                      />
                      <Button
                        type="button"
                        size="xs"
                        variant="outline"
                        disabled={!otherDate}
                        onClick={() => setAnswers((a) => ({ ...a, date: otherDate }))}
                      >
                        Use
                      </Button>
                    </div>
                  )}
                  {q.kind === "assignee" && q.options.length === 0 && (
                    <PersonPicker
                      label="Who is it for"
                      onPick={(p) => setAnswers((a) => ({ ...a, assignee_id: p.id }))}
                    />
                  )}
                </fieldset>
              ))}

              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-muted-foreground">
                  {current.questions.length > 0 ? "Answer the question to save." : "Enter saves."}
                </span>
                <Button size="sm" disabled={!canSave} onClick={save} className={FOCUS_CLASS}>
                  Save
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
