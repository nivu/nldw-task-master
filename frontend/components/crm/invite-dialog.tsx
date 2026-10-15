"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type {
  CrmRole,
  InvitationResult,
  TvaInvitationIn,
} from "@/lib/api/crm-types";
import type { MePreferences } from "@/lib/api/types";
import { errorMessage } from "@/lib/api/portal";

const ROLES: { value: CrmRole; label: string }[] = [
  { value: "member", label: "Member" },
  { value: "viewer", label: "Viewer" },
  { value: "admin", label: "Admin" },
];

/**
 * Invite someone — spec 007 FR-CRM-ACC-04 to -08, -15.
 *
 * There is no pending invitation: this adds the membership, creating a
 * CRM-only account first if the address has none. Afterwards the dialog
 * always shows the sign-in link to share, says whether a message reached the
 * person, and repeats that sign-in is Google only (FR-CRM-ACC-07, -08).
 *
 * `allowBypass` is the TVA admin panel's: the allow-list may be bypassed for
 * an existing staff account (FR-CRM-ACC-05).
 */
export function InviteDialog({
  open,
  onOpenChange,
  workspaceName,
  allowedDomains,
  allowBypass = false,
  onInvite,
  onDone,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspaceName: string;
  allowedDomains: string[];
  allowBypass?: boolean;
  onInvite: (invitation: TvaInvitationIn) => Promise<InvitationResult>;
  onDone: () => void;
}) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<CrmRole>("member");
  const [timezone, setTimezone] = useState<"" | MePreferences["timezone"]>("");
  const [bypass, setBypass] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<InvitationResult | null>(null);
  const [copied, setCopied] = useState(false);

  function reset() {
    setEmail("");
    setName("");
    setRole("member");
    setTimezone("");
    setBypass(false);
    setError(null);
    setResult(null);
    setCopied(false);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const invitation: TvaInvitationIn = {
        email: email.trim(),
        display_name: name.trim(),
        role,
        ...(timezone ? { timezone } : {}),
        ...(allowBypass && bypass ? { bypass_allow_list: true } : {}),
      };
      setResult(await onInvite(invitation));
      onDone();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      // The link is on screen regardless.
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Invite to {workspaceName}</DialogTitle>
          <DialogDescription>
            {allowedDomains.length > 0
              ? `Addresses at ${allowedDomains.join(", ")}.`
              : "This workspace has no allowed domains yet."}
          </DialogDescription>
        </DialogHeader>

        {result ? (
          <div className="space-y-3 text-sm" data-testid="invite-result">
            <p className="font-medium">
              {result.created_account
                ? "Account created and added."
                : result.reactivated
                  ? "Account reactivated and added."
                  : "Added to the workspace."}
            </p>
            <p className="text-muted-foreground">
              {result.delivered_via.length > 0
                ? `A message was sent by ${result.delivered_via.join(" and ")}.`
                : "No message could be sent. Share the link below yourself."}
            </p>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 overflow-x-auto rounded bg-muted px-2 py-1 font-mono text-xs">
                {result.sign_in_url}
              </code>
              <Button size="sm" variant="outline" onClick={() => copy(result.sign_in_url)}>
                {copied ? "Copied" : "Copy"}
              </Button>
            </div>
            <p className="rounded-md bg-muted p-2 text-xs text-muted-foreground">{result.note}</p>
            <DialogFooter>
              <Button variant="outline" onClick={reset}>
                Invite someone else
              </Button>
              <Button onClick={() => onOpenChange(false)}>Done</Button>
            </DialogFooter>
          </div>
        ) : (
          <form className="space-y-3" onSubmit={submit}>
            {error && (
              <div role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
                {error}
              </div>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="invite-email">Email</Label>
              <Input
                id="invite-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="invite-name">Name</Label>
              <Input
                id="invite-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={120}
                required
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="invite-role">Role</Label>
                <select
                  id="invite-role"
                  className="h-8 w-full rounded-md border border-input bg-transparent px-2 text-sm"
                  value={role}
                  onChange={(e) => setRole(e.target.value as CrmRole)}
                >
                  {ROLES.map((r) => (
                    <option key={r.value} value={r.value}>
                      {r.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="invite-timezone">Time zone</Label>
                <select
                  id="invite-timezone"
                  className="h-8 w-full rounded-md border border-input bg-transparent px-2 text-sm"
                  value={timezone}
                  onChange={(e) => setTimezone(e.target.value as typeof timezone)}
                >
                  <option value="">By their domain</option>
                  <option value="Asia/Kolkata">India</option>
                  <option value="Australia/Sydney">Sydney</option>
                </select>
              </div>
            </div>
            {allowBypass && (
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="h-4 w-4"
                  checked={bypass}
                  onChange={(e) => setBypass(e.target.checked)}
                />
                Existing staff account outside the allowed domains
              </label>
            )}
            <p className="text-xs text-muted-foreground">
              They sign in with Google. An address that is not a Google account cannot sign in.
            </p>
            <DialogFooter>
              <Button type="submit" disabled={busy || !email.trim() || !name.trim()}>
                {busy ? "Inviting…" : "Invite"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
