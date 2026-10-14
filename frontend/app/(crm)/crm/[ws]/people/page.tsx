"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { DuplicateWarning } from "@/components/crm/duplicate-warning";
import { FieldEditor, type RelationSearch } from "@/components/crm/field-editor";
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
import { createCrmRecord, searchCompanyChoices } from "@/lib/api/crm";
import type { CrmDuplicates, CrmPerson } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";

/**
 * People — spec 007 FR-CRM-PER-01, FR-CRM-DUP-01, -02, FR-CRM-VIEW-01 to -09,
 * FR-CRM-UI-02, -06.
 *
 * The people list as a table on saved views (none is seeded for people, so
 * it starts newest first). A new person is checked for names alike before
 * saving; an email another person already has is refused, naming them.
 */
export default function PeoplePage() {
  const { bootstrap } = useCrm();
  const canEdit = bootstrap.role !== "viewer";
  const [creating, setCreating] = useState(false);

  return (
    <>
      <ObjectList
        kind="person"
        title="People"
        actions={canEdit && <Button onClick={() => setCreating(true)}>New person</Button>}
      />
      {canEdit && (
        <NewPersonDialog open={creating} onOpenChange={setCreating} workspace={bootstrap.workspace.key} />
      )}
    </>
  );
}

function NewPersonDialog({
  open,
  onOpenChange,
  workspace,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspace: string;
}) {
  const router = useRouter();
  const searchCompanies = useCallback<RelationSearch>(
    (q, after) => searchCompanyChoices(workspace, q, after),
    [workspace]
  );
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState<string | null>(null);
  const [duplicates, setDuplicates] = useState<CrmDuplicates | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setFirst("");
    setLast("");
    setEmail("");
    setCompany(null);
    setDuplicates(null);
    setError(null);
  }

  async function create(checkDuplicates: boolean) {
    setBusy(true);
    setError(null);
    try {
      const made = await createCrmRecord<CrmPerson>(
        workspace,
        "people",
        {
          first_name: first.trim(),
          ...(last.trim() ? { last_name: last.trim() } : {}),
          ...(email.trim() ? { primary_email: email.trim() } : {}),
          ...(company ? { company_id: company } : {}),
        },
        checkDuplicates
      );
      if ("created" in made) {
        setDuplicates(made.duplicates);
        return;
      }
      router.push(`/crm/${workspace}/people/${made.id}`);
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
          <DialogTitle>New person</DialogTitle>
          <DialogDescription>Add phones, LinkedIn and the rest on their page.</DialogDescription>
        </DialogHeader>
        {duplicates ? (
          <DuplicateWarning
            workspace={workspace}
            object="people"
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
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="person-first">First name</Label>
                <Input
                  id="person-first"
                  value={first}
                  maxLength={100}
                  onChange={(e) => setFirst(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="person-last">Last name</Label>
                <Input id="person-last" value={last} maxLength={100} onChange={(e) => setLast(e.target.value)} />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="person-email">Email</Label>
              <Input id="person-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="person-company">Company</Label>
              <FieldEditor
                id="person-company"
                spec={{
                  name: "company_id",
                  label: "Company",
                  type: "relation",
                  search: searchCompanies,
                }}
                value={company}
                onChange={(value) => setCompany(value as string | null)}
              />
            </div>
            <DialogFooter>
              <Button type="submit" disabled={busy || !first.trim()}>
                {busy ? "Checking…" : "Create"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
