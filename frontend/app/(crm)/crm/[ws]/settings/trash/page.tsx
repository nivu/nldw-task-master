"use client";

import Link from "next/link";
import { useState } from "react";

import { useCrm } from "@/components/crm/shell";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { getCrmTrash, purgeCrmRecord, restoreCrmRecord } from "@/lib/api/crm";
import type { CrmObject, CrmTrashRow } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";
import { formatDate } from "@/lib/crm/format";
import { useAsync } from "@/lib/use-async";

/**
 * Trash — spec 007 FR-CRM-DEL-01, -02.
 *
 * Admins only. Deleted records wait here for 90 days, then the nightly job
 * removes them for good. Restoring a record brings back everything deleted
 * with it (a company and the people deleted alongside it). Deleting forever
 * also removes the record's Drive links.
 */
export default function TrashPage() {
  const { bootstrap } = useCrm();
  const ws = bootstrap.workspace.key;
  const tz = bootstrap.me.timezone;
  const [object, setObject] = useState<CrmObject>("companies");
  const [message, setMessage] = useState<string | null>(null);
  const admin = bootstrap.role === "admin";
  const { data, error, reload } = useAsync(
    () => (admin ? getCrmTrash(ws, object) : Promise.resolve(null)),
    [ws, object, admin]
  );

  if (!admin) {
    return <p className="text-sm text-muted-foreground">Only a workspace admin can see the Trash.</p>;
  }

  async function act(row: CrmTrashRow, action: "restore" | "purge") {
    setMessage(null);
    if (action === "purge" && !window.confirm(`Delete ${row.name} for good? This cannot be undone.`)) {
      return;
    }
    try {
      if (action === "restore") {
        await restoreCrmRecord(ws, object, row.id);
        setMessage(`${row.name} is back.`);
      } else {
        await purgeCrmRecord(ws, object, row.id);
        setMessage(`${row.name} was deleted for good.`);
      }
      reload();
    } catch (err) {
      setMessage(errorMessage(err));
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="flex-1 font-heading text-lg font-semibold">Trash</h1>
        <Link href={`/crm/${ws}/settings`} className="text-sm text-muted-foreground hover:underline">
          Settings
        </Link>
      </div>
      <p className="text-sm text-muted-foreground">
        Deleted records stay here for 90 days. Restoring one brings back everything deleted with it.
      </p>
      <Tabs value={object} onValueChange={(value) => setObject(value as CrmObject)}>
        <TabsList className="max-w-full overflow-x-auto">
          <TabsTrigger value="companies">Companies</TabsTrigger>
          <TabsTrigger value="people">People</TabsTrigger>
          <TabsTrigger value="opportunities">Deals</TabsTrigger>
          <TabsTrigger value="tasks">Tasks</TabsTrigger>
          <TabsTrigger value="notes">Notes</TabsTrigger>
        </TabsList>
      </Tabs>
      {message && (
        <p role="status" className="text-sm">
          {message}
        </p>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
      {data && data.rows.length === 0 && <p className="text-sm text-muted-foreground">The Trash is empty.</p>}
      {data && data.rows.length > 0 && (
        <ul className="divide-y rounded-md border">
          {data.rows.map((row) => (
            <li key={row.id} className="flex flex-wrap items-center gap-2 p-3">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{row.name}</p>
                <p className="text-xs text-muted-foreground">
                  Deleted {formatDate(row.deleted_at, tz)}
                  {row.deleted_by_name ? ` by ${row.deleted_by_name}` : ""} · {row.days_left}{" "}
                  {row.days_left === 1 ? "day" : "days"} left
                </p>
              </div>
              <div className="flex gap-1.5">
                <Button size="sm" variant="outline" onClick={() => act(row, "restore")}>
                  Restore
                </Button>
                <Button size="sm" variant="destructive" onClick={() => act(row, "purge")}>
                  Delete forever
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
