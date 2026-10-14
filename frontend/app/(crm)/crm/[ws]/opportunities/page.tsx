"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AmountField, AmountText, type AmountValue } from "@/components/crm/amount-field";
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
import { createCrmRecord, getCrmMembers, listCrmRecords, searchCompanyChoices } from "@/lib/api/crm";
import type { CrmMember, CrmOpportunity } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { formatDate, todayIn, type NumberFormat } from "@/lib/crm/format";
import { useAsync } from "@/lib/use-async";
import { cn } from "@/lib/utils";

/**
 * Opportunities — spec 007 FR-CRM-OPP-01, FR-CRM-PIPE-04, -10, FR-CRM-FX-06,
 * FR-CRM-UI-02, -06.
 *
 * The plain list, newest first, with a search and paging; saved views, the
 * kanban and the Pipeline arrive with S6. A deal is opened on a company at
 * Qualified or later, with its next step and date; the server refuses
 * anything else in one sentence, shown as it is. A Viewer sees no amounts.
 */
export default function OpportunitiesPage() {
  const { bootstrap } = useCrm();
  const ws = bootstrap.workspace.key;
  const tz = bootstrap.me.timezone;
  const numberFormat = bootstrap.me.number_format;
  const canEdit = bootstrap.role !== "viewer";
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [extra, setExtra] = useState<CrmOpportunity[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const { data, error } = useAsync(async () => {
    const page = await listCrmRecords<CrmOpportunity>(ws, "opportunities", { q: search, limit: 50 });
    setExtra([]);
    setNext(page.next);
    return page.rows;
  }, [ws, search]);
  const rows = [...(data ?? []), ...extra];
  const today = todayIn(tz);

  async function more() {
    if (!next) return;
    const page = await listCrmRecords<CrmOpportunity>(ws, "opportunities", { q: search, limit: 50, after: next });
    setExtra((current) => [...current, ...page.rows]);
    setNext(page.next);
  }

  const due = (deal: CrmOpportunity) =>
    deal.next_step_on && !deal.stage.startsWith("closed") && deal.next_step_on < today;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="flex-1 font-heading text-lg font-semibold">Opportunities</h1>
        {canEdit && <Button onClick={() => setCreating(true)}>New deal</Button>}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setSearch(q.trim());
        }}
        className="flex gap-2"
      >
        <Input
          aria-label="Search deals"
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
        <p className="text-sm text-muted-foreground">{search ? "No deal matches that." : "No deals yet."}</p>
      )}

      {rows.length > 0 && (
        <>
          {/* Phones: one card per deal (FR-CRM-UI-06). */}
          <ul className="divide-y rounded-md border md:hidden">
            {rows.map((d) => (
              <li key={d.id} className="space-y-0.5 p-3">
                <Link href={`/crm/${ws}/opportunities/${d.id}`} className="font-medium hover:underline">
                  {d.name}
                </Link>
                <p className="text-xs text-muted-foreground">
                  {[d.stage_label, d.company_name, d.owner_name].filter(Boolean).join(" · ")}
                </p>
                {d.amount !== undefined && (
                  <p className="text-sm">
                    <AmountText amount={d.amount} currency={d.currency} inr={d.amount_in_inr} numberFormat={numberFormat} />
                  </p>
                )}
              </li>
            ))}
          </ul>
          <div className="hidden overflow-x-auto rounded-md border md:block">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="p-2 font-medium">Deal</th>
                  <th className="p-2 font-medium">Company</th>
                  <th className="p-2 font-medium">Stage</th>
                  {bootstrap.role !== "viewer" && <th className="p-2 font-medium">Amount</th>}
                  <th className="p-2 font-medium">Next step</th>
                  <th className="p-2 font-medium">Owner</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {rows.map((d) => (
                  <tr key={d.id} className="hover:bg-muted/40">
                    <td className="p-2">
                      <Link href={`/crm/${ws}/opportunities/${d.id}`} className="font-medium hover:underline">
                        {d.name}
                      </Link>
                    </td>
                    <td className="p-2">
                      <Link href={`/crm/${ws}/companies/${d.company_id}`} className="hover:underline">
                        {d.company_name ?? "—"}
                      </Link>
                    </td>
                    <td className="p-2">{d.stage_label}</td>
                    {bootstrap.role !== "viewer" && (
                      <td className="whitespace-nowrap p-2">
                        <AmountText amount={d.amount} currency={d.currency} inr={d.amount_in_inr} numberFormat={numberFormat} />
                      </td>
                    )}
                    <td className="p-2">
                      {d.next_step ? (
                        <span className={cn(due(d) && "text-destructive")}>
                          {d.next_step}
                          {d.next_step_on ? ` · ${formatDate(d.next_step_on, tz)}` : ""}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="p-2">{d.owner_name ?? "—"}</td>
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
        <NewDealDialog
          open={creating}
          onOpenChange={setCreating}
          workspace={ws}
          me={bootstrap.me.id}
          numberFormat={numberFormat}
        />
      )}
    </div>
  );
}

/** A new deal starts at Discovery with its next step and date (FR-CRM-PIPE-07). */
function NewDealDialog({
  open,
  onOpenChange,
  workspace,
  me,
  numberFormat,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspace: string;
  me: string;
  numberFormat: NumberFormat;
}) {
  const router = useRouter();
  const { data: members } = useAsync<CrmMember[]>(
    () => (open ? getCrmMembers(workspace) : Promise.resolve([])),
    [workspace, open]
  );
  const [name, setName] = useState("");
  const [companyId, setCompanyId] = useState<string | null>(null);
  const [owner, setOwner] = useState<string | null>(me);
  const [nextStep, setNextStep] = useState("");
  const [nextStepOn, setNextStepOn] = useState("");
  const [money, setMoney] = useState<AmountValue>({ amount: null, currency: "INR" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setName("");
    setCompanyId(null);
    setOwner(me);
    setNextStep("");
    setNextStepOn("");
    setMoney({ amount: null, currency: "INR" });
    setError(null);
  }

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const made = await createCrmRecord<CrmOpportunity>(
        workspace,
        "opportunities",
        {
          name: name.trim(),
          company_id: companyId,
          owner_id: owner,
          next_step: nextStep.trim(),
          next_step_on: nextStepOn || null,
          ...(money.amount ? { amount: money.amount, currency: money.currency } : {}),
        },
        false
      );
      router.push(`/crm/${workspace}/opportunities/${(made as CrmOpportunity).id}`);
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
          <DialogTitle>New deal</DialogTitle>
          <DialogDescription>
            It starts at Discovery. The company must be at Qualified or later.
          </DialogDescription>
        </DialogHeader>
        <form
          id="deal-form"
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            create();
          }}
        >
          {error && (
            <div role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
              {error}
            </div>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="deal-name">Name</Label>
            <Input
              id="deal-name"
              placeholder="Client - what we sell"
              maxLength={200}
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="deal-company">Company</Label>
            <FieldEditor
              id="deal-company"
              spec={{
                name: "company_id",
                label: "Company",
                type: "relation",
                required: true,
                choices: [],
                search: (q, after) => searchCompanyChoices(workspace, q, after),
              }}
              value={companyId}
              onChange={(value) => setCompanyId((value as string | null) ?? null)}
            />
          </div>
          <div className="grid gap-2 sm:grid-cols-[2fr_1fr]">
            <div className="space-y-1.5">
              <Label htmlFor="deal-next">Next step</Label>
              <Input
                id="deal-next"
                maxLength={500}
                value={nextStep}
                onChange={(e) => setNextStep(e.target.value)}
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="deal-next-on">By</Label>
              <Input
                id="deal-next-on"
                type="date"
                value={nextStepOn}
                onChange={(e) => setNextStepOn(e.target.value)}
                required
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="deal-amount">Amount (optional)</Label>
            <AmountField id="deal-amount" value={money} onChange={setMoney} numberFormat={numberFormat} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="deal-owner">Owner</Label>
            <FieldEditor
              id="deal-owner"
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
        </form>
        <DialogFooter>
          <Button type="submit" form="deal-form" disabled={busy || !name.trim() || !companyId}>
            {busy ? "Creating…" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
