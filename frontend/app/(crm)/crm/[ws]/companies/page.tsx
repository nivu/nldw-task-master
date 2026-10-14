"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { DuplicateWarning } from "@/components/crm/duplicate-warning";
import { FieldEditor } from "@/components/crm/field-editor";
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
import { createCrmRecord, getCrmMembers, listCrmRecords } from "@/lib/api/crm";
import type { CrmCompany, CrmDuplicates, CrmMember } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { formatDate } from "@/lib/crm/format";
import { useAsync } from "@/lib/use-async";

/**
 * Companies — spec 007 FR-CRM-CO-01, FR-CRM-DUP-01, -02, FR-CRM-UI-02, -06.
 *
 * The plain list, newest first, with a search and paging; saved views,
 * filters and the kanban arrive with S6. New companies are checked for names
 * alike before they are saved, and an exact domain clash is refused naming
 * the company that has it.
 */
export default function CompaniesPage() {
  const { bootstrap } = useCrm();
  const ws = bootstrap.workspace.key;
  const tz = bootstrap.me.timezone;
  const canEdit = bootstrap.role !== "viewer";
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [extra, setExtra] = useState<CrmCompany[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const { data, error } = useAsync(async () => {
    const page = await listCrmRecords<CrmCompany>(ws, "companies", { q: search, limit: 50 });
    setExtra([]);
    setNext(page.next);
    return page.rows;
  }, [ws, search]);
  const rows = [...(data ?? []), ...extra];

  async function more() {
    if (!next) return;
    const page = await listCrmRecords<CrmCompany>(ws, "companies", { q: search, limit: 50, after: next });
    setExtra((current) => [...current, ...page.rows]);
    setNext(page.next);
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="flex-1 font-heading text-lg font-semibold">Companies</h1>
        {canEdit && <Button onClick={() => setCreating(true)}>New company</Button>}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setSearch(q.trim());
        }}
        className="flex gap-2"
      >
        <Input
          aria-label="Search companies"
          placeholder="Search by name"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="max-w-sm"
        />
        <Button type="submit" variant="outline">
          Search
        </Button>
      </form>

      {error && <p className="text-sm text-destructive">{error}</p>}
      {!error && !data && <p className="text-sm text-muted-foreground">Loading…</p>}
      {data && rows.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {search ? "No company matches that." : "No companies yet."}
        </p>
      )}

      {rows.length > 0 && (
        <>
          {/* Phones: one card per company (FR-CRM-UI-06). */}
          <ul className="divide-y rounded-md border md:hidden">
            {rows.map((c) => (
              <li key={c.id} className="p-3">
                <Link href={`/crm/${ws}/companies/${c.id}`} className="font-medium hover:underline">
                  {c.name}
                </Link>
                <p className="text-xs text-muted-foreground">
                  {[c.lead_stage_label, c.domain, c.owner_name].filter(Boolean).join(" · ")}
                </p>
              </li>
            ))}
          </ul>
          <div className="hidden overflow-x-auto rounded-md border md:block">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="p-2 font-medium">Name</th>
                  <th className="p-2 font-medium">Domain</th>
                  <th className="p-2 font-medium">Lead stage</th>
                  <th className="p-2 font-medium">Account type</th>
                  <th className="p-2 font-medium">Owner</th>
                  <th className="p-2 font-medium">Updated</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {rows.map((c) => (
                  <tr key={c.id} className="hover:bg-muted/40">
                    <td className="p-2">
                      <Link href={`/crm/${ws}/companies/${c.id}`} className="font-medium hover:underline">
                        {c.name}
                      </Link>
                    </td>
                    <td className="p-2 text-muted-foreground">{c.domain ?? "—"}</td>
                    <td className="p-2">{c.lead_stage_label}</td>
                    <td className="p-2">{c.account_type_label ?? "—"}</td>
                    <td className="p-2">{c.owner_name ?? "—"}</td>
                    <td className="whitespace-nowrap p-2 text-muted-foreground">
                      {formatDate(c.updated_at, tz)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {next && (
            <Button variant="outline" onClick={more}>
              Load more
            </Button>
          )}
        </>
      )}

      {canEdit && (
        <NewCompanyDialog open={creating} onOpenChange={setCreating} workspace={ws} me={bootstrap.me.id} />
      )}
    </div>
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
