"use client";

import { useEffect, useState } from "react";

import { TASK_CREATED_EVENT } from "@/components/tasks/quick-add";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { InboxItem, InboxKind } from "@/lib/api/task-types";
import { errorMessage } from "@/lib/api/portal";
import { addInboxItem, archiveInboxItem, getInbox, makeTaskFromInbox } from "@/lib/api/tasks";
import { FOCUS_CLASS } from "@/components/tasks/task-row";
import { cn } from "@/lib/utils";

/** Sent with the new item when a voice note's idea lands in the inbox. */
export const INBOX_ADDED_EVENT = "tasks:inbox-added";

const KINDS: { value: InboxKind; label: string }[] = [
  { value: "idea", label: "Idea" },
  { value: "link", label: "Link" },
  { value: "note", label: "Note" },
];

/**
 * The Ideas and Reading inbox — spec 008 FR-TASK-INBOX-01, -02; contracts
 * §2 inbox routes.
 *
 * The reader's own unprocessed ideas, links and notes, newest first; nobody
 * else, admins included, ever sees them. Each has **Make it a task** (a
 * private task from its title, tags, link and notes, recorded on the item)
 * and **Archive**. An item can be added here, by a voice note's idea, or by
 * Claude (`inbox_add`).
 */
export function Inbox() {
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  const [kind, setKind] = useState<InboxKind>("idea");
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getInbox()
      .then((out) => {
        if (!cancelled) setItems(out);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    function onAdded(e: Event) {
      const item = (e as CustomEvent<InboxItem>).detail;
      if (item) setItems((all) => (all ? [item, ...all.filter((i) => i.id !== item.id)] : all));
    }
    window.addEventListener(INBOX_ADDED_EVENT, onAdded);
    return () => window.removeEventListener(INBOX_ADDED_EVENT, onAdded);
  }, []);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const item = await addInboxItem({ kind, title: title.trim(), url: url.trim() || null });
      setItems((all) => [item, ...(all ?? [])]);
      setTitle("");
      setUrl("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function act(item: InboxItem, write: () => Promise<unknown>) {
    setActing(item.id);
    setError(null);
    try {
      await write();
      setItems((all) => (all ? all.filter((i) => i.id !== item.id) : all));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setActing(null);
    }
  }

  function makeTask(item: InboxItem) {
    return act(item, async () => {
      const out = await makeTaskFromInbox(item.id);
      window.dispatchEvent(new CustomEvent(TASK_CREATED_EVENT, { detail: out.task }));
    });
  }

  return (
    <section className="space-y-3" data-testid="inbox">
      <form onSubmit={add} className="flex flex-wrap items-center gap-2 rounded-lg border p-3">
        <label className="text-sm">
          <span className="sr-only">Kind</span>
          <select
            aria-label="Kind"
            className="h-8 rounded-md border border-input bg-transparent px-2 text-sm"
            value={kind}
            onChange={(e) => setKind(e.target.value as InboxKind)}
          >
            {KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <Input
          aria-label="Title"
          placeholder="An idea, something to read…"
          className="h-8 min-w-0 flex-1"
          maxLength={200}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <Input
          aria-label="Link"
          type="url"
          placeholder="https://… (optional)"
          className="h-8 min-w-0 sm:w-56"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <Button type="submit" size="sm" disabled={saving || !title.trim()}>
          Add
        </Button>
      </form>

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {!items && !error && <p className="text-sm text-muted-foreground">Loading…</p>}
      {items && items.length === 0 && <p className="text-sm text-muted-foreground">Your inbox is empty.</p>}
      {items && items.length > 0 && (
        <ul className="space-y-1.5">
          {items.map((item) => (
            <li key={item.id} className="space-y-1 rounded-md border bg-background px-2 py-2 text-sm">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="rounded bg-muted px-1.5 py-0.5 text-xs">{item.kind_label}</span>
                <span className="min-w-0 break-words font-medium">{item.title}</span>
              </div>
              {item.url && (
                <a
                  href={item.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={cn("block break-all text-xs text-muted-foreground underline", FOCUS_CLASS)}
                >
                  {item.url}
                </a>
              )}
              {item.notes && <p className="whitespace-pre-wrap break-words text-xs text-muted-foreground">{item.notes}</p>}
              {item.tags.length > 0 && <p className="text-xs text-muted-foreground">#{item.tags.join(" #")}</p>}
              <div className="flex flex-wrap gap-2 pt-1">
                <Button size="sm" onClick={() => makeTask(item)} disabled={acting !== null}>
                  Make it a task
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => act(item, () => archiveInboxItem(item.id))}
                  disabled={acting !== null}
                >
                  Archive
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
