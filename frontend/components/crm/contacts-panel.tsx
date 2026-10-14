"use client";

import Link from "next/link";
import { Trash2 } from "lucide-react";
import { useState } from "react";

import { FieldEditor } from "@/components/crm/field-editor";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { addCrmContact, removeCrmContact, searchPersonChoices } from "@/lib/api/crm";
import type { CrmContact, CrmOption } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";

/**
 * A deal's other contacts — spec 007 §5.8 "Other contacts … Role in deal".
 *
 * Each person with their role on this deal, which lives on the link only, so
 * the same person can be the champion of one deal and procurement on another.
 */
export function ContactsPanel({
  workspace,
  dealId,
  contacts,
  roles,
  canEdit,
  onChange,
}: {
  workspace: string;
  dealId: string;
  contacts: CrmContact[];
  roles: CrmOption[];
  canEdit: boolean;
  onChange: () => void;
}) {
  const [person, setPerson] = useState<string | null>(null);
  const [role, setRole] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const offered = roles.filter((r) => r.is_active);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    if (!person) return;
    setBusy(true);
    setError(null);
    try {
      await addCrmContact(workspace, dealId, { person_id: person, role_id: role || null });
      setPerson(null);
      setRole("");
      onChange();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(personId: string) {
    setError(null);
    try {
      await removeCrmContact(workspace, dealId, personId);
      onChange();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">Other contacts ({contacts.length})</h3>
      {contacts.length === 0 ? (
        <p className="text-sm text-muted-foreground">No other contacts on this deal.</p>
      ) : (
        <ul className="divide-y rounded-md border">
          {contacts.map((c) => (
            <li key={c.id} className="flex items-center gap-2 p-2.5">
              <div className="min-w-0 flex-1">
                <Link href={`/crm/${workspace}/people/${c.person_id}`} className="text-sm font-medium hover:underline">
                  {c.person_name ?? "Someone"}
                </Link>
                <p className="truncate text-xs text-muted-foreground">
                  {[c.role_label, c.job_title].filter(Boolean).join(" · ") || "No role set"}
                </p>
              </div>
              {canEdit && (
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Remove ${c.person_name ?? "contact"}`}
                  onClick={() => remove(c.person_id)}
                >
                  <Trash2 className="size-4" />
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canEdit && (
        <form onSubmit={add} className="space-y-2 rounded-md border p-3">
          <p className="text-sm font-medium">Add a contact</p>
          <div className="grid gap-2 sm:grid-cols-[2fr_1fr_auto] sm:items-end">
            <div className="space-y-1">
              <Label htmlFor="contact-person">Person</Label>
              <FieldEditor
                id="contact-person"
                spec={{
                  name: "person_id",
                  label: "Person",
                  type: "relation",
                  choices: [],
                  search: (q, after) => searchPersonChoices(workspace, q, after),
                }}
                value={person}
                onChange={(value) => setPerson((value as string | null) ?? null)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="contact-role">Role in deal</Label>
              <Select
                items={offered.map((o) => ({ value: o.id, label: o.label }))}
                value={role}
                onValueChange={(next) => setRole(next ? String(next) : "")}
              >
                <SelectTrigger id="contact-role" className="h-8 w-full">
                  <SelectValue placeholder="None" />
                </SelectTrigger>
                <SelectContent>
                  {offered.map((o) => (
                    <SelectItem key={o.id} value={o.id}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button type="submit" disabled={busy || !person}>
              {busy ? "Adding…" : "Add"}
            </Button>
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
        </form>
      )}
    </section>
  );
}
