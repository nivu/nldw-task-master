"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { CompoffQueue } from "@/components/portal/team-ops";
import { decideBooking, errorMessage, getApprovals, getMe } from "@/lib/api/portal";
import { useAsync } from "@/lib/use-async";
import type { Me, PendingApproval } from "@/lib/api/types";

/**
 * The approval queue — FR-APPR-02/03/04.
 *
 * This is the one screen where a reason is shown to somebody other than its
 * author (Q-06). A lead cannot reasonably approve a request whose reason they
 * are not allowed to read, and the API returns it only here.
 *
 * FR-APPR-03: a rejection captures a note. The Reject button is disabled until
 * one is written — a rejection with no explanation is the thing the email
 * workflow already did badly.
 *
 * FR-APPR-08: an admin's queue is every pending request in the organisation
 * except their own, so each request names its approver (the requester's lead,
 * or "No lead — admin"). Comp-off claims wait on the same people, so their
 * queue sits below the leave queue.
 */
export default function ApprovalsPage() {
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const { data: queue, error, setError, reload } = useAsync<PendingApproval[]>(
    () => getApprovals(),
    []
  );
  const { data: me } = useAsync<Me>(() => getMe(), []);
  const scope = me?.capabilities.admin_panel ? "waiting across the organisation" : "waiting on you";

  async function decide(id: string, approve: boolean) {
    setBusy(id);
    setError(null);
    try {
      await decideBooking(id, approve, notes[id]);
      reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  if (error) {
    return (
      <div role="alert" className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">
        {error}
      </div>
    );
  }
  if (!queue) return <p className="text-sm text-muted-foreground">Loading requests…</p>;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="font-heading text-lg font-semibold">Approvals</h1>
        <p className="text-sm text-muted-foreground">
          {queue.length === 0
            ? `Nothing ${scope}.`
            : `${queue.length} request${queue.length === 1 ? "" : "s"} ${scope}.`}
        </p>
      </div>

      {queue.map((request) => {
        const note = notes[request.id] ?? "";
        const pretty = new Date(`${request.date}T00:00:00`).toLocaleDateString("en-GB", {
          weekday: "short",
          day: "numeric",
          month: "short",
          year: "numeric",
        });

        return (
          <Card key={request.id}>
            <CardContent className="space-y-3 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{request.display_name}</span>
                <Badge variant="outline" className="text-muted-foreground">
                  {request.approver ? `Lead: ${request.approver}` : "No lead — admin"}
                </Badge>
                <Badge variant="secondary">{request.category_label}</Badge>
                <Badge variant="outline">
                  {request.duration === "0.5" ? "Half day" : "Full day"}
                </Badge>
                <span className="ml-auto text-sm text-muted-foreground">{pretty}</span>
              </div>

              {request.reason && (
                <p className="rounded-md bg-muted px-3 py-2 text-sm">{request.reason}</p>
              )}

              <Textarea
                value={note}
                onChange={(e) => setNotes({ ...notes, [request.id]: e.target.value })}
                placeholder="Note (required to reject)"
                rows={2}
                maxLength={500}
              />

              <div className="flex gap-2">
                <Button
                  onClick={() => decide(request.id, true)}
                  disabled={busy === request.id}
                  className="flex-1"
                >
                  Approve
                </Button>
                <Button
                  variant="outline"
                  onClick={() => decide(request.id, false)}
                  disabled={busy === request.id || note.trim().length === 0}
                  className="flex-1"
                >
                  Reject
                </Button>
              </div>
            </CardContent>
          </Card>
        );
      })}

      <CompoffQueue onError={setError} />
    </div>
  );
}
