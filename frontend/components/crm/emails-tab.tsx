"use client";

import { ArrowDownLeft, ArrowUpRight } from "lucide-react";
import { useState } from "react";

import { ThreadSummary } from "@/components/crm/thread-summary";
import { Button } from "@/components/ui/button";
import { getCrmEmails } from "@/lib/api/crm";
import type { CrmEmailThread } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { type CrmTimezone, formatDate } from "@/lib/crm/format";
import { useAsync } from "@/lib/use-async";

/**
 * Emails — spec 007 FR-CRM-REC-03, FR-CRM-SYNC-06, -10.
 *
 * The threads Gmail label sync stored for a record: a company shows all its
 * people's, a deal its company's and its contacts'. Subject, participants,
 * direction and date only; message bodies are never stored (Q-29), so the
 * thread is read in Gmail itself. Newest first, older ones on request.
 *
 * With 009 phase 1 on, a summarised thread's row expands to its AI summary
 * (009 FR-CRMAI-SUM-08); the summary is generated, the email is still not
 * stored.
 */
export function EmailsTab({
  workspace,
  object,
  recordId,
  timeZone,
  syncOn,
}: {
  workspace: string;
  object: "companies" | "people" | "opportunities";
  recordId: string;
  timeZone: CrmTimezone;
  /** Whether sync is live in the workspace (FR-CRM-TODAY-02's wording). */
  syncOn: boolean;
}) {
  const { data, error } = useAsync(
    () => getCrmEmails(workspace, object, recordId),
    [workspace, object, recordId]
  );
  const [older, setOlder] = useState<CrmEmailThread[]>([]);
  const [next, setNext] = useState<string | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);

  if (error) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error}
      </p>
    );
  }
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const threads = [...data.threads, ...older];
  const cursor = next === undefined ? data.next : next;

  async function more() {
    if (!cursor) return;
    setBusy(true);
    setMoreError(null);
    try {
      const page = await getCrmEmails(workspace, object, recordId, cursor);
      setOlder((rows) => [...rows, ...page.threads]);
      setNext(page.next);
    } catch (err) {
      setMoreError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (threads.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {syncOn
          ? "No labelled email with this record yet."
          : "Email sync is off. Once an admin sets up a mailbox, labelled Gmail threads show here."}
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <ul className="divide-y rounded-md border">
        {threads.map((thread) => {
          const inbound = thread.direction === "inbound";
          const Icon = inbound ? ArrowDownLeft : ArrowUpRight;
          return (
            <li key={`${thread.mailbox}:${thread.thread_id}`} className="space-y-1 p-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <p className="min-w-0 flex-1 break-words text-sm font-medium">
                  {thread.subject || "(no subject)"}
                </p>
                <span className="shrink-0 text-xs text-muted-foreground">
                  {formatDate(thread.last_at, timeZone)}
                </span>
              </div>
              <p className="flex items-center gap-1 text-xs text-muted-foreground">
                <Icon className="size-3.5" aria-hidden />
                <span>{inbound ? "Last: theirs" : "Last: ours"}</span>
                <span aria-hidden>·</span>
                <span>
                  {thread.messages} message{thread.messages === 1 ? "" : "s"}
                </span>
                {thread.mailbox && (
                  <>
                    <span aria-hidden>·</span>
                    <span className="truncate">{thread.mailbox}</span>
                  </>
                )}
              </p>
              <p className="break-words text-xs">
                {thread.participants.map((p) => p.name || p.email).join(", ")}
              </p>
              {thread.summary && (
                <ThreadSummary summary={thread.summary} subject={thread.subject} timeZone={timeZone} />
              )}
            </li>
          );
        })}
      </ul>
      {moreError && (
        <p role="alert" className="text-sm text-destructive">
          {moreError}
        </p>
      )}
      {cursor && (
        <Button variant="outline" size="sm" onClick={more} disabled={busy}>
          {busy ? "Loading…" : "Older threads"}
        </Button>
      )}
    </div>
  );
}
