"use client";

import { ExternalLink, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { addCrmDriveLink, removeCrmDriveLink } from "@/lib/api/crm";
import type { CrmDriveLink } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";

/**
 * Files — spec 007 FR-CRM-LINK-01 to -03, owner decision D4.
 *
 * A record's Google Drive links: an `https://` address and a label each. The
 * CRM never holds the file, shows no preview and grants no access: a link
 * opens in a new tab, and Drive's own sharing decides who can see it.
 */
export function DriveLinks({
  workspace,
  recordType,
  recordId,
  links,
  canEdit,
  onChange,
}: {
  workspace: string;
  recordType: CrmDriveLink["record_type"];
  recordId: string;
  links: CrmDriveLink[];
  canEdit: boolean;
  onChange: () => void;
}) {
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    if (!url.trim().toLowerCase().startsWith("https://")) {
      setError("A Drive link must be an https:// address.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await addCrmDriveLink(workspace, {
        record_type: recordType,
        record_id: recordId,
        url: url.trim(),
        label: label.trim(),
      });
      setUrl("");
      setLabel("");
      onChange();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setError(null);
    try {
      await removeCrmDriveLink(workspace, id);
      onChange();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="space-y-4">
      {links.length === 0 ? (
        <p className="text-sm text-muted-foreground">No Drive links yet.</p>
      ) : (
        <ul className="divide-y rounded-md border">
          {links.map((link) => (
            <li key={link.id} className="flex items-center gap-2 p-2.5">
              <a
                href={link.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex min-w-0 flex-1 items-center gap-2 text-sm hover:underline"
              >
                <ExternalLink className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="truncate">{link.label}</span>
              </a>
              {canEdit && (
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Remove ${link.label}`}
                  onClick={() => remove(link.id)}
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
          <p className="text-sm font-medium">Add a Drive link</p>
          <div className="grid gap-2 sm:grid-cols-[2fr_1fr_auto] sm:items-end">
            <div className="space-y-1">
              <Label htmlFor="drive-url">Link</Label>
              <Input
                id="drive-url"
                type="url"
                placeholder="https://drive.google.com/…"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="drive-label">Label</Label>
              <Input
                id="drive-label"
                placeholder="Signed SOW"
                maxLength={200}
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                required
              />
            </div>
            <Button type="submit" disabled={busy || !url.trim() || !label.trim()}>
              {busy ? "Adding…" : "Add"}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Only the address is kept. Who can open it is set in Google Drive.
          </p>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
        </form>
      )}
    </div>
  );
}
