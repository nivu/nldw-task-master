"use client";

import { X } from "lucide-react";
import { useState } from "react";

import { Markdown } from "@/components/crm/field-editor";
import { LinkRefs } from "@/components/crm/task-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { createCrmRecord } from "@/lib/api/crm";
import type { CrmMember, CrmNote, NoteKind } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { formatDate, todayIn, type CrmTimezone } from "@/lib/crm/format";

/**
 * Notes and Log activity — spec 007 FR-CRM-NOTE-01 to -03; Q-27, Q-53, Q-63.
 *
 * A note is plain text with markdown (rendered with `marked`, as long text is
 * everywhere in the CRM). Logging a call, meeting, WhatsApp message or other
 * touch is a note with that kind and the day it happened. Only members of the
 * workspace can be mentioned; the picker offers nobody else.
 */

const KINDS: { value: NoteKind; label: string }[] = [
  { value: "note", label: "Note" },
  { value: "call", label: "Call" },
  { value: "meeting", label: "Meeting" },
  { value: "whatsapp", label: "WhatsApp" },
  { value: "other", label: "Other" },
];

export function NoteComposer({
  workspace,
  link,
  members,
  timeZone,
  onCreated,
}: {
  workspace: string;
  link: { record_type: "company" | "person" | "opportunity"; record_id: string };
  members: CrmMember[];
  timeZone: CrmTimezone;
  onCreated: (note: CrmNote) => void;
}) {
  const [body, setBody] = useState("");
  const [kind, setKind] = useState<NoteKind>("note");
  const [occurredOn, setOccurredOn] = useState(todayIn(timeZone));
  const [mentions, setMentions] = useState<string[]>([]);
  const [preview, setPreview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const name = (id: string) => members.find((m) => m.user_id === id)?.display_name ?? "Someone";
  const mentionable = members.filter((m) => !mentions.includes(m.user_id));

  function mention(userId: string) {
    if (!userId || mentions.includes(userId)) return;
    setMentions((current) => [...current, userId]);
    setBody((text) => `${text}${text && !text.endsWith(" ") ? " " : ""}@${name(userId)} `);
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const made = await createCrmRecord<CrmNote>(
        workspace,
        "notes",
        {
          body,
          kind,
          ...(kind !== "note" ? { occurred_on: occurredOn } : {}),
          links: [link],
          mention_ids: mentions,
        },
        false
      );
      setBody("");
      setKind("note");
      setOccurredOn(todayIn(timeZone));
      setMentions([]);
      setPreview(false);
      onCreated(made as CrmNote);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="space-y-2 rounded-md border p-3">
      <div className="flex flex-wrap items-end gap-2">
        <div className="w-36 space-y-1">
          <Label htmlFor="note-kind">Kind</Label>
          <Select
            items={KINDS}
            value={kind}
            onValueChange={(next) => setKind((next ?? "note") as NoteKind)}
          >
            <SelectTrigger id="note-kind" className="h-8 w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {KINDS.map((k) => (
                <SelectItem key={k.value} value={k.value}>
                  {k.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {kind !== "note" && (
          <div className="space-y-1">
            <Label htmlFor="note-date">When</Label>
            <Input
              id="note-date"
              type="date"
              value={occurredOn}
              onChange={(e) => setOccurredOn(e.target.value)}
              required
            />
          </div>
        )}
        <div className="min-w-40 flex-1 space-y-1">
          <Label htmlFor="note-mention">Mention</Label>
          <Select
            items={mentionable.map((m) => ({ value: m.user_id, label: m.display_name }))}
            value=""
            onValueChange={(next) => mention(next ? String(next) : "")}
          >
            <SelectTrigger id="note-mention" className="h-8 w-full">
              <SelectValue placeholder="@ a member" />
            </SelectTrigger>
            <SelectContent>
              {mentionable.map((m) => (
                <SelectItem key={m.user_id} value={m.user_id}>
                  {m.display_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      {mentions.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {mentions.map((id) => (
            <span key={id} className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs">
              @{name(id)}
              <button
                type="button"
                aria-label={`Remove mention of ${name(id)}`}
                onClick={() => setMentions((current) => current.filter((m) => m !== id))}
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <Label htmlFor="note-body">{kind === "note" ? "Note" : "What happened"}</Label>
          <button
            type="button"
            className="text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setPreview((p) => !p)}
          >
            {preview ? "Edit" : "Preview"}
          </button>
        </div>
        {preview ? (
          <div className="min-h-20 rounded-md border p-2">
            {body.trim() ? <Markdown text={body} /> : <span className="text-sm text-muted-foreground">Nothing to preview.</span>}
          </div>
        ) : (
          <Textarea
            id="note-body"
            rows={4}
            maxLength={20000}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
        )}
        <p className="text-xs text-muted-foreground">Markdown: **bold**, _italic_, lists, checklists and links.</p>
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex justify-end">
        <Button type="submit" size="sm" disabled={busy || !body.trim()}>
          {busy ? "Saving…" : kind === "note" ? "Add note" : "Log activity"}
        </Button>
      </div>
    </form>
  );
}

/** A record's notes, newest first. */
export function NoteList({
  workspace,
  notes,
  timeZone,
}: {
  workspace: string;
  notes: CrmNote[];
  timeZone: CrmTimezone;
}) {
  if (notes.length === 0) return <p className="text-sm text-muted-foreground">No notes yet.</p>;
  return (
    <ul className="space-y-3">
      {notes.map((note) => (
        <li key={note.id} className="space-y-1 rounded-md border p-3">
          <p className="flex flex-wrap gap-x-2 text-xs text-muted-foreground">
            {note.kind !== "note" && (
              <span className="font-medium text-foreground">
                {note.kind_label}
                {note.occurred_on ? ` · ${formatDate(note.occurred_on, timeZone)}` : ""}
              </span>
            )}
            <span>{note.created_by_name ?? "Someone"}</span>
            <span>{formatDate(note.created_at, timeZone)}</span>
          </p>
          <Markdown text={note.body} />
          {(note.links.length > 1 || note.mentions.length > 0) && (
            <div className="space-y-0.5 text-xs text-muted-foreground">
              {note.links.length > 1 && (
                <div className="flex gap-1">
                  <span>Also on:</span>
                  <LinkRefs workspace={workspace} links={note.links} />
                </div>
              )}
              {note.mentions.length > 0 && (
                <p>Mentions: {note.mentions.map((m) => `@${m.name ?? "someone"}`).join(", ")}</p>
              )}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

/** A record's Notes tab (FR-CRM-REC-03): the composer and the notes. */
export function NotesTab({
  workspace,
  canEdit,
  members,
  notes,
  link,
  timeZone,
  onChange,
}: {
  workspace: string;
  canEdit: boolean;
  members: CrmMember[];
  notes: CrmNote[];
  link: { record_type: "company" | "person" | "opportunity"; record_id: string };
  timeZone: CrmTimezone;
  onChange: () => void;
}) {
  return (
    <div className="space-y-3">
      {canEdit && <NoteComposer workspace={workspace} link={link} members={members} timeZone={timeZone} onCreated={onChange} />}
      <NoteList workspace={workspace} notes={notes} timeZone={timeZone} />
    </div>
  );
}
