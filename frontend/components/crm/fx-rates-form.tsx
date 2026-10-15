"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { setCrmFxRate } from "@/lib/api/crm";
import type { CrmFxRate } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";

/**
 * Monthly FX rates — spec 007 FR-CRM-FX-02; owner decision D7.
 *
 * INR per one unit of AUD or USD, for a month. Setting a rate never changes
 * an amount already saved: each keeps the INR value it was stored with
 * (Q-59). Rates are decimal strings end to end.
 */
export function FxRatesForm({
  workspace,
  rates,
  onChanged,
}: {
  workspace: string;
  rates: CrmFxRate[];
  onChanged: () => void;
}) {
  const [currency, setCurrency] = useState<CrmFxRate["currency"]>("USD");
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const [rate, setRate] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="space-y-4">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          setError(null);
          try {
            await setCrmFxRate(workspace, currency, month, rate.trim());
            setRate("");
            onChanged();
          } catch (err) {
            setError(errorMessage(err));
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="space-y-1.5">
          <Label htmlFor="fx-currency">Currency</Label>
          <select
            id="fx-currency"
            className="h-8 rounded-md border border-input bg-transparent px-2 text-sm"
            value={currency}
            onChange={(e) => setCurrency(e.target.value as CrmFxRate["currency"])}
          >
            <option value="USD">USD</option>
            <option value="AUD">AUD</option>
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="fx-month">Month</Label>
          <Input
            id="fx-month"
            type="month"
            value={month}
            onChange={(e) => setMonth(e.target.value)}
            className="h-8"
            required
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="fx-rate">INR per 1 {currency}</Label>
          <Input
            id="fx-rate"
            inputMode="decimal"
            pattern="[0-9]+(\.[0-9]{1,6})?"
            value={rate}
            onChange={(e) => setRate(e.target.value)}
            className="h-8 w-32"
            required
          />
        </div>
        <Button type="submit" disabled={busy || !rate.trim()}>
          {busy ? "Saving…" : "Set rate"}
        </Button>
      </form>
      {error && (
        <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}
      {rates.length === 0 ? (
        <p className="text-sm text-muted-foreground">No rates yet.</p>
      ) : (
        <table className="w-full max-w-md text-sm">
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th className="py-2 font-medium">Month</th>
              <th className="py-2 font-medium">Currency</th>
              <th className="py-2 text-right font-medium">INR per unit</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {rates.map((r) => (
              <tr key={`${r.currency}-${r.month}`}>
                <td className="py-2">{r.month}</td>
                <td className="py-2">{r.currency}</td>
                <td className="py-2 text-right tabular-nums">{r.rate}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
