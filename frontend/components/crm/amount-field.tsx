"use client";

import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { CrmCurrency } from "@/lib/api/crm-types";
import { formatAmount, type NumberFormat } from "@/lib/crm/format";

/**
 * A deal amount — spec 007 FR-CRM-FX-01, -06, owner decision D7.
 *
 * The amount in its own currency (INR, AUD or USD) with the INR figure the
 * deal stored beside it, in the viewer's amount format. The INR value is the
 * server's: it is converted at the month's rate on save (FR-CRM-FX-03), so
 * nothing is converted here.
 */

const CURRENCIES: CrmCurrency[] = ["INR", "AUD", "USD"];

export interface AmountValue {
  amount: string | null;
  currency: CrmCurrency;
}

export function AmountField({
  id,
  value,
  onChange,
  savedInr,
  numberFormat,
}: {
  id?: string;
  value: AmountValue;
  onChange: (value: AmountValue) => void;
  /** The INR the deal stored for its current amount, shown beside it. */
  savedInr?: string | null;
  numberFormat: NumberFormat;
}) {
  return (
    <div className="space-y-1">
      <div className="flex gap-1.5">
        <Input
          id={id}
          inputMode="decimal"
          placeholder="0"
          value={value.amount ?? ""}
          onChange={(e) => onChange({ ...value, amount: e.target.value.trim() || null })}
        />
        <div className="w-24 shrink-0">
          <Select
            items={CURRENCIES.map((c) => ({ value: c, label: c }))}
            value={value.currency}
            onValueChange={(next) => onChange({ ...value, currency: (next ?? "INR") as CrmCurrency })}
          >
            <SelectTrigger aria-label="Currency" className="h-8 w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CURRENCIES.map((c) => (
                <SelectItem key={c} value={c}>
                  {c}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      {value.currency !== "INR" && (
        <p className="text-xs text-muted-foreground">
          {savedInr
            ? `Saved as ${formatAmount(savedInr, "INR", numberFormat)} at this month's rate.`
            : "Converted to INR at this month's rate when saved."}
        </p>
      )}
    </div>
  );
}

/** An amount and its INR figure, read-only (FR-CRM-FX-06). */
export function AmountText({
  amount,
  currency,
  inr,
  numberFormat,
}: {
  amount?: string | null;
  currency: CrmCurrency | null;
  inr?: string | null;
  numberFormat: NumberFormat;
}) {
  if (!amount || !currency) return <span className="text-muted-foreground">—</span>;
  return (
    <span>
      {formatAmount(amount, currency, currency === "INR" ? numberFormat : undefined)}
      {currency !== "INR" && inr && (
        <span className="text-muted-foreground"> · {formatAmount(inr, "INR", numberFormat)}</span>
      )}
    </span>
  );
}
