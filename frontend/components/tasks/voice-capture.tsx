"use client";

import { useEffect, useRef, useState } from "react";
import { Mic, Square } from "lucide-react";

import { INBOX_ADDED_EVENT } from "@/components/tasks/inbox";
import { TASK_CREATED_EVENT } from "@/components/tasks/quick-add";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { InboxIn, TaskIn, VoiceCandidate, VoiceNote } from "@/lib/api/task-types";
import { errorMessage } from "@/lib/api/portal";
import { confirmVoiceCandidate, dropVoiceCandidate, getVoiceNote, uploadVoiceNote } from "@/lib/api/tasks";
import { dueText } from "@/lib/tasks/labels";

const MAX_BYTES = 4 * 1024 * 1024;
const POLL_MS = 2000;
const POLL_FOR_MS = 60_000;
const INBOX_KIND = { idea: "Idea", link: "Link", note: "Note" } as const;

type Edit = { title: string; due_on: string; assignee_id?: string };

/**
 * Voice capture — spec 008 FR-TASK-VOICE-01 to -05, Journey 4.10; plan
 * §5.10.
 *
 * Record a note in the browser (`MediaRecorder`) or upload an audio file of
 * at most 4 MB; it goes up through the proxy and is transcribed in the
 * background. The page asks for it every 2 seconds for at most a minute,
 * then shows its candidates, split by fixed rules: each task candidate with
 * its quick-add draft, each inbox candidate with its kind. Each candidate is
 * confirmed with one tap, edited first, or dropped: a task candidate becomes
 * a task, an inbox candidate an item of the reader's Ideas and Reading inbox
 * (S4b). Nothing is saved before a confirm. A candidate with an open question (a date that has passed, an
 * ambiguous weekday) is answered or edited before it can be confirmed.
 */
export function VoiceCapture() {
  const [uploadId, setUploadId] = useState<string | null>(null);
  const [note, setNote] = useState<VoiceNote | null>(null);
  const [busy, setBusy] = useState<"recording" | "uploading" | "waiting" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<number, Edit>>({});
  const [acting, setActing] = useState<number | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);

  // Ask for the note every 2 s until it is transcribed or failed, at most 60 s.
  useEffect(() => {
    if (!uploadId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const started = Date.now();
    async function poll() {
      try {
        const out = await getVoiceNote(uploadId as string);
        if (cancelled) return;
        if (out.status === "uploaded" || out.status === "transcribing") {
          if (Date.now() - started >= POLL_FOR_MS) {
            setBusy(null);
            setError("The voice note is taking longer than expected. Try again in a minute.");
            return;
          }
          timer = setTimeout(poll, POLL_MS);
          return;
        }
        setNote(out);
        setBusy(null);
      } catch (err) {
        if (cancelled) return;
        setBusy(null);
        setError(errorMessage(err));
      }
    }
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [uploadId]);

  async function send(file: File) {
    setError(null);
    setNote(null);
    setEdits({});
    setUploadId(null);
    if (file.size > MAX_BYTES) {
      setError("The voice note is larger than 4 MB.");
      return;
    }
    setBusy("uploading");
    try {
      const out = await uploadVoiceNote(file);
      setBusy("waiting");
      setUploadId(out.upload_id);
    } catch (err) {
      setBusy(null);
      setError(errorMessage(err));
    }
  }

  async function record() {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const type = (rec.mimeType || "audio/webm").split(";")[0];
        const ext = type === "audio/mp4" ? "m4a" : type === "audio/ogg" ? "ogg" : "webm";
        send(new File(chunks, `voice-note.${ext}`, { type }));
      };
      recorder.current = rec;
      rec.start();
      setBusy("recording");
    } catch {
      setError("The microphone could not be opened. Upload an audio file instead.");
    }
  }

  function stop() {
    recorder.current?.stop();
    recorder.current = null;
  }

  function handled(index: number) {
    setNote((n) =>
      n
        ? {
            ...n,
            candidates: n.candidates.map((c) => (c.index === index ? { ...c, handled: true } : c)),
          }
        : n
    );
  }

  async function confirm(c: VoiceCandidate) {
    if (!uploadId) return;
    const edit = edits[c.index];
    setActing(c.index);
    setError(null);
    try {
      let draft: TaskIn | InboxIn | undefined;
      if (edit && c.kind === "task") {
        draft = {
          title: edit.title,
          due_on: edit.due_on || null,
          ...(c.draft
            ? {
                tags: c.draft.tags,
                assignee_id: edit.assignee_id ?? c.draft.assignee.id,
                // The spoken bucket, unless the edit moved the date (the bucket follows it).
                ...(edit.due_on === (c.draft.due_on ?? "") ? { bucket: c.draft.bucket } : {}),
              }
            : {}),
        };
      } else if (edit && c.kind === "inbox") {
        draft = {
          kind: c.draft?.kind ?? "note",
          title: edit.title,
          url: c.draft?.url ?? null,
          tags: c.draft?.tags ?? [],
        };
      }
      const saved = await confirmVoiceCandidate(uploadId, c.index, draft);
      window.dispatchEvent(
        new CustomEvent(c.kind === "task" ? TASK_CREATED_EVENT : INBOX_ADDED_EVENT, { detail: saved })
      );
      handled(c.index);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setActing(null);
    }
  }

  async function drop(c: VoiceCandidate) {
    if (!uploadId) return;
    setActing(c.index);
    setError(null);
    try {
      await dropVoiceCandidate(uploadId, c.index);
      handled(c.index);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setActing(null);
    }
  }

  function startEdit(c: VoiceCandidate) {
    const title = c.draft?.title ?? c.text ?? "";
    const due = c.kind === "task" ? (c.draft?.due_on ?? "") : "";
    setEdits((e) => ({ ...e, [c.index]: { title, due_on: due } }));
  }

  const open = note?.candidates.filter((c) => !c.handled && c.text) ?? [];

  return (
    <section className="space-y-3 rounded-lg border p-3" data-testid="voice-capture">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-medium">Voice note</h2>
        {busy === "recording" ? (
          <Button size="sm" variant="outline" onClick={stop}>
            <Square className="size-4" /> Stop and send
          </Button>
        ) : (
          <Button size="sm" variant="outline" onClick={record} disabled={busy !== null}>
            <Mic className="size-4" /> Record
          </Button>
        )}
        <label className="text-sm">
          <span className="sr-only">Upload a voice note</span>
          <input
            type="file"
            accept="audio/*"
            disabled={busy !== null}
            className="block max-w-full text-sm file:mr-3 file:rounded-md file:border file:border-input file:bg-transparent file:px-2 file:py-1 file:text-sm"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) send(file);
            }}
          />
        </label>
        <span className="text-xs text-muted-foreground" aria-live="polite">
          {busy === "recording" && "Recording…"}
          {busy === "uploading" && "Uploading…"}
          {busy === "waiting" && "Transcribing…"}
        </span>
      </div>

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {note?.status === "failed" && note.error && (
        <p role="alert" className="text-sm text-destructive">
          {note.error}
        </p>
      )}
      {note && note.status !== "failed" && open.length === 0 && (
        <p className="text-sm text-muted-foreground">Every candidate is confirmed or dropped.</p>
      )}

      {open.length > 0 && (
        <ul className="space-y-2">
          {open.map((c) => {
            const edit = edits[c.index];
            const questions = c.kind === "task" ? (c.questions ?? []) : [];
            const base: Edit = edit ?? {
              title: c.draft?.title ?? c.text ?? "",
              due_on: c.kind === "task" ? (c.draft?.due_on ?? "") : "",
            };
            return (
              <li key={c.index} className="space-y-2 rounded-md border p-2 text-sm">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="rounded bg-muted px-1.5 py-0.5 text-xs">
                    {c.kind === "task" ? "Task" : c.draft ? INBOX_KIND[c.draft.kind] : "Inbox"}
                  </span>
                  <span className="min-w-0 break-words">{c.draft?.title || c.text}</span>
                  {c.kind === "task" && c.draft?.due_on && (
                    <span className="text-xs text-muted-foreground">Due {dueText(c.draft.due_on)}</span>
                  )}
                  {c.kind === "inbox" && c.draft?.url && (
                    <span className="min-w-0 break-all text-xs text-muted-foreground">{c.draft.url}</span>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">Heard: “{c.text}”</p>

                {questions.map((q) => (
                  <fieldset key={q.prompt} className="space-y-1.5 rounded-md border p-2">
                    <legend className="px-1 text-xs font-medium">{q.prompt}</legend>
                    <div className="flex flex-wrap gap-1">
                      {q.options.map((o) => (
                        <Button
                          key={o.value}
                          type="button"
                          variant="outline"
                          size="xs"
                          onClick={() =>
                            setEdits((e) => ({
                              ...e,
                              [c.index]:
                                q.kind === "date" ? { ...base, due_on: o.value } : { ...base, assignee_id: o.value },
                            }))
                          }
                        >
                          {o.label}
                        </Button>
                      ))}
                    </div>
                  </fieldset>
                ))}

                {edit && (
                  <div className="flex flex-wrap items-center gap-2">
                    <Input
                      aria-label="Title"
                      className="h-8 min-w-0 flex-1"
                      value={edit.title}
                      onChange={(e) =>
                        setEdits((all) => ({
                          ...all,
                          [c.index]: { ...edit, title: e.target.value },
                        }))
                      }
                    />
                    {c.kind === "task" && (
                      <Input
                        type="date"
                        aria-label="Due date"
                        className="h-8 w-40"
                        value={edit.due_on}
                        onChange={(e) =>
                          setEdits((all) => ({
                            ...all,
                            [c.index]: { ...edit, due_on: e.target.value },
                          }))
                        }
                      />
                    )}
                  </div>
                )}

                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    onClick={() => confirm(c)}
                    disabled={acting !== null || (!edit && questions.length > 0) || (edit && !edit.title.trim())}
                  >
                    Confirm
                  </Button>
                  {!edit && (
                    <Button size="sm" variant="outline" onClick={() => startEdit(c)} disabled={acting !== null}>
                      Edit
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => drop(c)} disabled={acting !== null}>
                    Drop
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
