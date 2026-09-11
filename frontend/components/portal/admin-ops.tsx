"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  addLocation,
  closeChecklist,
  errorMessage,
  getChecklists,
  runNudge,
  sendTestNotification,
  setChecklistTemplate,
  startChecklist,
  updateChecklistItem,
} from "@/lib/api/portal";
import type { Checklists, Location, PortalUser } from "@/lib/api/types";
import { useAsync } from "@/lib/use-async";

/** Spec 006 FR-LOC-01 — offices, so holidays can apply to one. */
export function LocationsPanel({ locations, onChanged, onError }: { locations: Location[]; onChanged: () => void; onError: (m: string) => void }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Locations</CardTitle>
        <CardDescription>
          A holiday can apply everywhere or to one location; each person belongs to one. New people
          join the default location until changed under People.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          {locations.map((l) => (
            <Badge key={l.id} variant={l.is_default ? "secondary" : "outline"}>
              {l.name}{l.is_default ? " · default" : ""}
            </Badge>
          ))}
        </div>
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try {
              await addLocation(name.trim());
              setName("");
              onChanged();
            } catch (err) {
              onError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="space-y-1.5">
            <Label className="text-xs" htmlFor="loc-name">New location</Label>
            <Input id="loc-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Bengaluru" required />
          </div>
          <Button type="submit" size="sm" variant="outline" disabled={busy || !name.trim()}>Add</Button>
        </form>
      </CardContent>
    </Card>
  );
}

/** Spec 006 FR-CHK — onboarding and offboarding, with owners and dates. */
export function ChecklistsPanel({ people, onError }: { people: PortalUser[]; onError: (m: string) => void }) {
  const { data, reload } = useAsync<Checklists>(() => getChecklists(), []);
  const [userId, setUserId] = useState("");
  const [kind, setKind] = useState<"onboarding" | "offboarding">("onboarding");
  const [editing, setEditing] = useState<"onboarding" | "offboarding" | null>(null);
  const [draft, setDraft] = useState("");

  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Start a checklist</CardTitle>
          <CardDescription>
            Copies the template for that kind. Starting an offboarding list does not deactivate
            anybody; that stays a separate, deliberate action under People.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={async (e) => {
              e.preventDefault();
              try {
                await startChecklist({ user_id: userId, kind });
                setUserId("");
                reload();
              } catch (err) {
                onError(errorMessage(err));
              }
            }}
          >
            <div className="space-y-1.5">
              <Label className="text-xs">Person</Label>
              <select className="h-9 rounded-md border border-input bg-transparent px-3 text-sm" value={userId} onChange={(e) => setUserId(e.target.value)} required>
                <option value="">Choose…</option>
                {people.map((p) => (
                  <option key={p.id} value={p.id}>{p.display_name}</option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Kind</Label>
              <select className="h-9 rounded-md border border-input bg-transparent px-3 text-sm" value={kind} onChange={(e) => setKind(e.target.value as "onboarding" | "offboarding")}>
                <option value="onboarding">Onboarding</option>
                <option value="offboarding">Offboarding</option>
              </select>
            </div>
            <Button type="submit" size="sm" disabled={!userId}>Start</Button>
          </form>
        </CardContent>
      </Card>

      {data.checklists.map((c) => (
        <Card key={c.id} className={c.closed_at ? "opacity-60" : undefined}>
          <CardHeader>
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="text-base">{c.display_name} · {c.kind}</CardTitle>
              <Badge variant="outline">{c.done}/{c.total}</Badge>
              {c.closed_at && <Badge variant="secondary">closed</Badge>}
              {!c.closed_at && c.done === c.total && (
                <Button size="sm" variant="ghost" className="ml-auto" onClick={async () => { try { await closeChecklist(c.id); reload(); } catch (err) { onError(errorMessage(err)); } }}>
                  Close
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent className="divide-y p-0">
            {c.items.map((item) => (
              <label key={item.id} className="flex flex-wrap items-center gap-3 p-3 text-sm">
                <input
                  type="checkbox"
                  className="size-4"
                  checked={Boolean(item.done_at)}
                  disabled={Boolean(c.closed_at)}
                  onChange={async (e) => {
                    try {
                      await updateChecklistItem(item.id, { done: e.target.checked });
                      reload();
                    } catch (err) {
                      onError(errorMessage(err));
                    }
                  }}
                />
                <span className={item.done_at ? "line-through text-muted-foreground" : ""}>{item.label}</span>
                <select
                  className="ml-auto h-7 rounded-md border border-input bg-transparent px-2 text-xs"
                  value={item.owner_id ?? ""}
                  disabled={Boolean(c.closed_at)}
                  onChange={async (e) => { try { await updateChecklistItem(item.id, { owner_id: e.target.value || null }); reload(); } catch (err) { onError(errorMessage(err)); } }}
                >
                  <option value="">owner…</option>
                  {people.map((p) => (
                    <option key={p.id} value={p.id}>{p.display_name}</option>
                  ))}
                </select>
                <Input
                  type="date"
                  className="h-7 w-36 text-xs"
                  value={item.due_on ?? ""}
                  disabled={Boolean(c.closed_at)}
                  onChange={async (e) => { try { await updateChecklistItem(item.id, { due_on: e.target.value || null }); reload(); } catch (err) { onError(errorMessage(err)); } }}
                />
              </label>
            ))}
          </CardContent>
        </Card>
      ))}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Templates</CardTitle>
          <CardDescription>One item per line. Changes apply to checklists started afterwards.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {(["onboarding", "offboarding"] as const).map((k) => (
            <div key={k} className="space-y-1">
              <div className="flex items-center gap-2">
                <p className="text-sm font-medium capitalize">{k}</p>
                <Button size="sm" variant="ghost" onClick={() => { setEditing(k); setDraft(data.templates[k].join("\n")); }}>Edit</Button>
              </div>
              {editing === k ? (
                <div className="space-y-2">
                  <Textarea rows={8} value={draft} onChange={(e) => setDraft(e.target.value)} />
                  <div className="flex gap-2">
                    <Button size="sm" onClick={async () => { try { await setChecklistTemplate(k, draft.split("\n")); setEditing(null); reload(); } catch (err) { onError(errorMessage(err)); } }}>Save</Button>
                    <Button size="sm" variant="outline" onClick={() => setEditing(null)}>Cancel</Button>
                  </div>
                </div>
              ) : (
                <ol className="list-decimal pl-5 text-sm text-muted-foreground">
                  {data.templates[k].map((label, i) => <li key={i}>{label}</li>)}
                </ol>
              )}
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

/** Spec 006 FR-NUDGE-04, FR-DIGEST-02 — prove the token, run things now. */
export function NotificationsPanel({ onError }: { onError: (m: string) => void }) {
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function run(fn: () => Promise<unknown>, label: string) {
    setBusy(true);
    setResult(null);
    try {
      const r = await fn();
      setResult(`${label}: ${JSON.stringify(r)}`);
    } catch (err) {
      onError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Slack and schedules</CardTitle>
        <CardDescription>
          Nudges go to people directly, never to a channel. The morning post goes to the channel named
          in Policy → slack_out_channel. Nothing sends until a Slack bot token is set on the server.
          Each button runs the scheduled job now, for real.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={busy} onClick={() => run(sendTestNotification, "Test message to you")}>Send me a test</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => runNudge("today"), "Nothing-logged nudge")}>Run today&apos;s nudge</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => runNudge("weekly_gaps"), "Weekly gaps to leads")}>Run weekly gaps</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => runNudge("over_allocation"), "Over-allocation to managers")}>Run over-allocation</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => runNudge("morning_post"), "Morning post")}>Post who is out</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => runNudge("digest"), "Leadership digest")}>Send the digest</Button>
        </div>
        {result && <pre className="overflow-x-auto rounded bg-muted p-2 font-mono text-xs">{result}</pre>}
        <p className="text-xs text-muted-foreground">
          Schedule (Asia/Kolkata): nudge at the configured hour on weekdays · gaps Friday 17:00 ·
          over-allocation Monday 09:00 · morning post 09:00 weekdays · digest Monday 09:05 ·
          auto-confirm 00:15 · comp-off lapse 00:20.
        </p>
      </CardContent>
    </Card>
  );
}
