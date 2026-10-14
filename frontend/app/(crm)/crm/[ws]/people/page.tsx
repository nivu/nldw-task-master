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
import { createCrmRecord, listCrmRecords } from "@/lib/api/crm";
import type { CrmCompany, CrmDuplicates, CrmPerson } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { formatDate } from "@/lib/crm/format";
import { useAsync } from "@/lib/use-async";

/**
 * People — spec 007 FR-CRM-PER-01, FR-CRM-DUP-01, -02, FR-CRM-UI-02, -06.
 *
 * The plain list, newest first, with a search and paging. A new person is
 * checked for names alike before saving; an email another person already has
 * is refused, naming them.
 */
export default function PeoplePage() {
  const { bootstrap } = useCrm();
  const ws = bootstrap.workspace.key;
  const tz = bootstrap.me.timezone;
  const canEdit = bootstrap.role !== "viewer";
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [extra, setExtra] = useState<CrmPerson[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const { data, error } = useAsync(async () => {
    const page = await listCrmRecords<CrmPerson>(ws, "people", { q: search, limit: 50 });
    setExtra([]);
    setNext(page.next);
    return page.rows;
  }, [ws, search]);
  const rows = [...(data ?? []), ...extra];

  async function more() {
    if (!next) return;
    const page = await listCrmRecords<CrmPerson>(ws, "people", { q: search, limit: 50, after: next });
    setExtra((current) => [...current, ...page.rows]);
    setNext(page.next);
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="flex-1 font-heading text-lg font-semibold">People</h1>
        {canEdit && <Button onClick={() => setCreating(true)}>New person</Button>}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setSearch(q.trim());
        }}
        className="flex gap-2"
      >
        <Input
          aria-label="Search people"
          placeholder="Search by name or email"
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
          {search ? "Nobody matches that." : "No people yet."}
        </p>
      )}

      {rows.length > 0 && (
        <>
          <ul className="divide-y rounded-md border md:hidden">
            {rows.map((p) => (
              <li key={p.id} className="p-3">
                <Link href={`/crm/${ws}/people/${p.id}`} className="font-medium hover:underline">
                  {p.name}
                </Link>
                <p className="truncate text-xs text-muted-foreground">
                  {[p.job_title, p.company_name, p.primary_email].filter(Boolean).join(" · ")}
                </p>
              </li>
            ))}
          </ul>
          <div className="hidden overflow-x-auto rounded-md border md:block">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="p-2 font-medium">Name</th>
                  <th className="p-2 font-medium">Company</th>
                  <th className="p-2 font-medium">Job title</th>
                  <th className="p-2 font-medium">Email</th>
                  <th className="p-2 font-medium">Owner</th>
                  <th className="p-2 font-medium">Updated</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {rows.map((p) => (
                  <tr key={p.id} className="hover:bg-muted/40">
                    <td className="p-2">
                      <Link href={`/crm/${ws}/people/${p.id}`} className="font-medium hover:underline">
                        {p.name}
                      </Link>
                      {p.do_not_contact && (
                        <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-[10px]">Do not contact</span>
                      )}
                    </td>
                    <td className="p-2">
                      {p.company_id ? (
                        <Link href={`/crm/${ws}/companies/${p.company_id}`} className="hover:underline">
                          {p.company_name}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="p-2">{p.job_title ?? "—"}</td>
                    <td className="p-2 text-muted-foreground">{p.primary_email ?? "—"}</td>
                    <td className="p-2">{p.owner_name ?? "—"}</td>
                    <td className="whitespace-nowrap p-2 text-muted-foreground">
                      {formatDate(p.updated_at, tz)}
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

      {canEdit && <NewPersonDialog open={creating} onOpenChange={setCreating} workspace={ws} />}
    </div>
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
  const { data: companies } = useAsync(
    () =>
      open
        ? listCrmRecords<CrmCompany>(workspace, "companies", { limit: 200 }).then((p) => p.rows)
        : Promise.resolve([] as CrmCompany[]),
    [workspace, open]
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
                  choices: (companies ?? []).map((c) => ({ value: c.id, label: c.name })),
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
