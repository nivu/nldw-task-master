"use client";

import { useEffect, useState } from "react";
import { Bell as BellIcon } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  getCrmNotifications,
  getCrmUnreadCount,
  markCrmNotificationsRead,
} from "@/lib/api/crm";
import type { CrmNotification } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { formatDate, type CrmTimezone } from "@/lib/crm/format";
import { OBJECT_PATH } from "@/lib/crm/view-fields";
import { cn } from "@/lib/utils";

/**
 * The bell — spec 007 FR-CRM-NOTIF-01, FR-CRM-ACC-10, FR-CRM-WS-02.
 *
 * The unread count is polled through the proxy every 60 seconds and when the
 * window regains focus: no Realtime and no browser grant. The list covers
 * every workspace the person belongs to now (the server leaves the others
 * out); opening one marks it read and goes to its record.
 */

const POLL_MS = 60_000;

/** Records with a page of their own. */
const RECORD_PAGES = new Set(["company", "person", "opportunity"]);

/** Where a notification opens. Companies, people and deals have pages; a
 *  task opens its list, and anything else (an unlinked note) the workspace. */
function hrefOf(n: CrmNotification): string {
  const ws = `/crm/${n.workspace_key}`;
  if (!n.record_type || !n.record_id) return ws;
  if (n.record_type === "task") return `${ws}/tasks`;
  if (!RECORD_PAGES.has(n.record_type)) return ws;
  return `${ws}/${OBJECT_PATH[n.record_type]}/${n.record_id}`;
}

/** The unread count, polled once for the whole shell (it shows the bell
 *  twice: in the sidebar and in the phone header). */
export function useUnreadCount(initial: number) {
  const [count, setCount] = useState(initial);
  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      getCrmUnreadCount()
        .then((r) => {
          if (!cancelled) setCount(r.count);
        })
        // A missed poll is tried again on the next one.
        .catch(() => {});
    };
    const timer = window.setInterval(poll, POLL_MS);
    window.addEventListener("focus", poll);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener("focus", poll);
    };
  }, []);
  return [count, setCount] as const;
}

export function Bell({
  count,
  onCount,
  timeZone,
}: {
  count: number;
  onCount: (update: (current: number) => number) => void;
  timeZone: CrmTimezone;
}) {
  const router = useRouter();
  const setCount = onCount;
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<CrmNotification[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  function load() {
    setProblem(null);
    getCrmNotifications()
      .then(setItems)
      .catch((err) => setProblem(errorMessage(err)));
  }

  async function openOne(n: CrmNotification) {
    setOpen(false);
    if (!n.read_at) {
      try {
        await markCrmNotificationsRead({ ids: [n.id] });
        setCount((c) => Math.max(0, c - 1));
      } catch {
        // Reading it matters more than marking it.
      }
    }
    router.push(hrefOf(n));
  }

  async function markAll() {
    try {
      await markCrmNotificationsRead({ all: true });
      setCount(() => 0);
      setItems((list) =>
        (list ?? []).map((n) => ({ ...n, read_at: n.read_at ?? new Date().toISOString() }))
      );
    } catch (err) {
      setProblem(errorMessage(err));
    }
  }

  const label = count > 0 ? `Notifications, ${count} unread` : "Notifications";

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) load();
      }}
    >
      <PopoverTrigger
        render={<Button variant="ghost" size="icon" aria-label={label} title={label} />}
        className="relative"
      >
        <BellIcon className="size-4" />
        {count > 0 && (
          <span
            aria-hidden
            className="absolute -top-0.5 -right-0.5 min-w-4 rounded-full bg-destructive px-1 text-center text-[10px] leading-4 font-medium text-white"
          >
            {count > 99 ? "99+" : count}
          </span>
        )}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(22rem,calc(100vw-2rem))] p-0">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <h2 className="text-sm font-medium">Notifications</h2>
          {count > 0 && (
            <button
              type="button"
              className="text-xs text-muted-foreground underline-offset-2 hover:underline"
              onClick={markAll}
            >
              Mark all read
            </button>
          )}
        </div>
        {problem && (
          <p role="alert" className="p-3 text-sm text-destructive">
            {problem}
          </p>
        )}
        {items === null && !problem && <p className="p-3 text-sm text-muted-foreground">Loading…</p>}
        {items?.length === 0 && <p className="p-3 text-sm text-muted-foreground">Nothing yet.</p>}
        <ul className="max-h-[60vh] divide-y overflow-y-auto">
          {items?.map((n) => (
            <li key={n.id}>
              <button
                type="button"
                onClick={() => openOne(n)}
                className={cn(
                  "flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left text-sm hover:bg-muted/60",
                  !n.read_at && "bg-muted/30"
                )}
              >
                <span className={cn(!n.read_at && "font-medium")}>
                  {!n.read_at && <span className="sr-only">Unread: </span>}
                  {n.title}
                </span>
                <span className="text-xs text-muted-foreground">{formatDate(n.created_at, timeZone)}</span>
              </button>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}
