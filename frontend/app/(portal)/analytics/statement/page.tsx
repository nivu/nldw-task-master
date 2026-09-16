"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getStatement, statementCsvPath } from "@/lib/api/portal";
import type { Statement } from "@/lib/api/types";
import { useAsync } from "@/lib/use-async";
import { createClient } from "@/lib/supabase/client";
import { isoMonth } from "@/lib/dates";

/**
 * A client-ready effort statement — spec 006 FR-STMT.
 *
 * Hours only. Printable as it stands (print styles hide the portal chrome),
 * and downloadable as CSV through the proxy with the session token.
 */
export default function StatementPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
      <StatementView />
    </Suspense>
  );
}

function StatementView() {
  const params = useSearchParams();
  const projectId = params.get("project") ?? "";
  const period = params.get("period") ?? isoMonth(new Date());
  const { data, error } = useAsync<Statement>(() => getStatement(projectId, period), [projectId, period]);

  async function downloadCsv() {
    const { data: session } = await createClient().auth.getSession();
    const token = session.session?.access_token;
    const response = await fetch(`/api/proxy${statementCsvPath(projectId, period)}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${data?.project.name ?? "statement"}-${period}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (error) return <div role="alert" className="rounded-md bg-destructive/10 p-4 text-sm text-destructive">{error}</div>;
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const month = new Date(`${period}-01T00:00:00`).toLocaleDateString("en-GB", { month: "long", year: "numeric" });
  const workDays = data.days.filter((d) => new Date(`${d}T00:00:00`).getDay() % 6 !== 0);

  return (
    <div className="space-y-4 print:text-black">
      <div className="flex flex-wrap items-start gap-2 print:hidden">
        <div className="flex-1">
          <h1 className="font-heading text-lg font-semibold">Effort statement</h1>
          <p className="text-sm text-muted-foreground">
            Hours only. Print this page or download the CSV to send with an invoice.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={downloadCsv}>Download CSV</Button>
        <Button size="sm" onClick={() => window.print()}>Print</Button>
      </div>

      <div className="space-y-1">
        <h2 className="text-xl font-semibold">{data.project.name}</h2>
        <p className="text-sm text-muted-foreground">
          {data.project.client ?? "Internal"} · {month} · {data.total_hours} hours
        </p>
        {data.unconfirmed && (
          <p className="text-xs text-amber-700 dark:text-amber-300 print:text-black">
            Some weeks are not yet confirmed by a lead; those are marked below.
          </p>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="p-1 font-medium">Person</th>
              {workDays.map((d) => (
                <th key={d} className="p-1 text-right font-medium tabular-nums">{d.slice(8)}</th>
              ))}
              <th className="p-1 text-right font-medium">Total</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {data.people.map((p) => (
              <tr key={p.user_id}>
                <td className="p-1 whitespace-nowrap">
                  {p.display_name}
                  {p.unconfirmed_weeks.length > 0 && (
                    <Badge variant="outline" className="ml-1 print:hidden">unconfirmed</Badge>
                  )}
                </td>
                {workDays.map((d) => (
                  <td key={d} className="p-1 text-right tabular-nums">{p.days[d] ?? ""}</td>
                ))}
                <td className="p-1 text-right font-medium tabular-nums">{p.total}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t font-medium">
              <td className="p-1">Total</td>
              <td colSpan={workDays.length} />
              <td className="p-1 text-right tabular-nums">{data.total_hours}</td>
            </tr>
          </tfoot>
        </table>
      </div>

      <div className="text-sm">
        <p className="font-medium">By phase</p>
        <ul className="mt-1 space-y-0.5">
          {data.by_phase.map((row) => (
            <li key={row.phase} className="flex gap-3">
              <span className="flex-1">{row.phase}</span>
              <span className="tabular-nums">{row.hours}h</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="space-y-2 text-sm">
        <p className="font-medium">Work notes</p>
        {data.people.map((p) =>
          p.notes.length ? (
            <div key={p.user_id}>
              <p className="text-xs font-medium text-muted-foreground">{p.display_name}</p>
              <ul className="space-y-0.5">
                {p.notes.map((n, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="w-12 shrink-0 tabular-nums text-muted-foreground">{n.date.slice(5)}</span>
                    <span>{n.note}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null
        )}
      </div>
    </div>
  );
}
