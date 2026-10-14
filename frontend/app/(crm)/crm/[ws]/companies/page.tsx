"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { DuplicateWarning } from "@/components/crm/duplicate-warning";
import { FieldEditor } from "@/components/crm/field-editor";
import { ObjectList } from "@/components/crm/object-list";
import { useCrm } from "@/components/crm/shell";
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
import { createCrmRecord, getCrmMembers } from "@/lib/api/crm";
import type { CrmCompany, CrmDuplicates, CrmMember } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { useAsync } from "@/lib/use-async";

/**
 * Companies — spec 007 FR-CRM-CO-01, FR-CRM-DUP-01, -02, FR-CRM-VIEW-01 to
 * -09, -11, FR-CRM-UI-02, -06.
 *
 * The company list on its saved views (All companies, Lead funnel, Partners
 * and channels, My accounts, and any the person saves), as a table or a
 * kanban. New companies are checked for names alike before they are saved,
 * and an exact domain clash is refused naming the company that has it.
 */
export default function CompaniesPage() {
  const { bootstrap } = useCrm();
  const canEdit = bootstrap.role !== "viewer";
  const [creating, setCreating] = useState(false);

  return (
    <>
      <ObjectList
        kind="company"
        title="Companies"
        actions={canEdit && <Button onClick={() => setCreating(true)}>New company</Button>}
      />
      {canEdit && (
        <NewCompanyDialog
          open={creating}
          onOpenChange={setCreating}
          workspace={bootstrap.workspace.key}
          me={bootstrap.me.id}
        />
      )}
    </>
  );
}

function NewCompanyDialog({
  open,
  onOpenChange,
  workspace,
  me,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspace: string;
  me: string;
}) {
  const router = useRouter();
  const { data: members } = useAsync<CrmMember[]>(
    () => (open ? getCrmMembers(workspace) : Promise.resolve([])),
    [workspace, open]
  );
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [owner, setOwner] = useState<string | null>(me);
  const [duplicates, setDuplicates] = useState<CrmDuplicates | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setName("");
    setDomain("");
    setOwner(me);
    setDuplicates(null);
    setError(null);
  }

  async function create(checkDuplicates: boolean) {
    setBusy(true);
    setError(null);
    try {
      const made = await createCrmRecord<CrmCompany>(
        workspace,
        "companies",
        { name: name.trim(), ...(domain.trim() ? { domain: domain.trim() } : {}), owner_id: owner },
        checkDuplicates
      );
      if ("created" in made) {
        setDuplicates(made.duplicates);
        return;
      }
      router.push(`/crm/${workspace}/companies/${made.id}`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
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
          <DialogTitle>New company</DialogTitle>
          <DialogDescription>It starts at lead stage New. Add the other fields on its page.</DialogDescription>
        </DialogHeader>
        {duplicates ? (
          <DuplicateWarning
            workspace={workspace}
            object="companies"
            duplicates={duplicates}
            busy={busy}
            onBack={() => setDuplicates(null)}
            onContinue={() => create(false)}
          />
        ) : (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              create(true);
            }}
          >
            {error && (
              <div role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
                {error}
              </div>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="company-name">Name</Label>
              <Input
                id="company-name"
                value={name}
                maxLength={200}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="company-domain">Domain</Label>
              <Input
                id="company-domain"
                placeholder="acme.com"
                value={domain}
                onChange={(e) => setDomain(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="company-owner">Owner</Label>
              <FieldEditor
                id="company-owner"
                spec={{
                  name: "owner_id",
                  label: "Owner",
                  type: "relation",
                  required: true,
                  choices: (members ?? []).map((m) => ({ value: m.user_id, label: m.display_name })),
                }}
                value={owner}
                onChange={(value) => setOwner(value as string | null)}
              />
            </div>
            <DialogFooter>
              <Button type="submit" disabled={busy || !name.trim()}>
                {busy ? "Checking…" : "Create"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
