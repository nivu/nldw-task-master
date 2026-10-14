"use client";

import { useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { addCrmOption, updateCrmOption } from "@/lib/api/crm";
import type { CrmColor, CrmLists, CrmOption, OptionUpdate } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";

const COLORS: CrmColor[] = [
  "gray",
  "red",
  "orange",
  "amber",
  "green",
  "teal",
  "blue",
  "indigo",
  "purple",
  "pink",
];

/** "company.account_type" → "Company: account type". */
function listName(key: string): string {
  const [object, field] = key.includes(".") ? key.split(".", 2) : ["", key];
  const words = field.replace(/_/g, " ");
  return object ? `${object[0].toUpperCase()}${object.slice(1)}: ${words}` : words;
}

/**
 * Select lists — spec 007 FR-CRM-LIST-01, -02, -09.
 *
 * A fixed list (stages, lead stages, task status and bucket) can only be
 * renamed and recoloured; the rest also take new options, a new order, a
 * default and deactivation. Inactive options stay on old records and leave
 * the pickers. The server holds every rule; this screen offers only what it
 * allows.
 */
export function ListEditor({
  workspace,
  lists,
  onChanged,
}: {
  workspace: string;
  lists: CrmLists;
  onChanged: () => void;
}) {
  const [error, setError] = useState<string | null>(null);

  async function change(listKey: string, option: CrmOption, changes: OptionUpdate) {
    setError(null);
    try {
      await updateCrmOption(workspace, listKey, option.id, changes);
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="space-y-4">
      {error && (
        <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}
      {Object.entries(lists).map(([listKey, options]) => (
        <section key={listKey} className="rounded-md border">
          <header className="flex items-center gap-2 border-b px-3 py-2">
            <h3 className="text-sm font-medium">{listName(listKey)}</h3>
            {options.some((o) => o.is_fixed) && <Badge variant="outline">Fixed</Badge>}
          </header>
          <ul className="divide-y">
            {options.map((option, index) => (
              <li
                key={option.id}
                className={`flex flex-wrap items-center gap-2 px-3 py-2 ${option.is_active ? "" : "opacity-50"}`}
              >
                <Input
                  key={`${option.id}-${option.label}`}
                  aria-label={`Name of ${option.label}`}
                  defaultValue={option.label}
                  maxLength={60}
                  className="h-8 w-48"
                  onBlur={(e) => {
                    const label = e.target.value.trim();
                    if (label && label !== option.label) change(listKey, option, { label });
                  }}
                />
                <select
                  aria-label={`Colour of ${option.label}`}
                  className="h-8 rounded-md border border-input bg-transparent px-2 text-sm"
                  value={option.color ?? ""}
                  onChange={(e) =>
                    change(listKey, option, { color: (e.target.value || null) as CrmColor | null })
                  }
                >
                  <option value="">No colour</option>
                  {COLORS.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
                {!option.is_fixed && (
                  <>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Move ${option.label} up`}
                      disabled={index === 0}
                      onClick={() => change(listKey, option, { position: options[index - 1].position })}
                    >
                      <ArrowUp />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Move ${option.label} down`}
                      disabled={index === options.length - 1}
                      onClick={() => change(listKey, option, { position: options[index + 1].position })}
                    >
                      <ArrowDown />
                    </Button>
                    <label className="flex items-center gap-1 text-xs text-muted-foreground">
                      <input
                        type="radio"
                        name={`default-${listKey}`}
                        checked={option.is_default}
                        disabled={!option.is_active}
                        onChange={() => change(listKey, option, { is_default: true })}
                      />
                      Default
                    </label>
                    <label className="flex items-center gap-1 text-xs text-muted-foreground">
                      <input
                        type="checkbox"
                        checked={option.is_active}
                        disabled={option.system_key !== null && option.is_active}
                        onChange={(e) => change(listKey, option, { is_active: e.target.checked })}
                      />
                      Active
                    </label>
                  </>
                )}
                {option.is_fixed && option.is_default && <Badge variant="secondary">Default</Badge>}
              </li>
            ))}
          </ul>
          {!options.some((o) => o.is_fixed) && (
            <AddOption workspace={workspace} listKey={listKey} onAdded={onChanged} onError={setError} />
          )}
        </section>
      ))}
    </div>
  );
}

function AddOption({
  workspace,
  listKey,
  onAdded,
  onError,
}: {
  workspace: string;
  listKey: string;
  onAdded: () => void;
  onError: (message: string) => void;
}) {
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="flex items-center gap-2 border-t px-3 py-2"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        try {
          await addCrmOption(workspace, listKey, { label: label.trim() });
          setLabel("");
          onAdded();
        } catch (err) {
          onError(errorMessage(err));
        } finally {
          setBusy(false);
        }
      }}
    >
      <Input
        aria-label={`New option for ${listName(listKey)}`}
        placeholder="New option"
        value={label}
        maxLength={60}
        className="h-8 w-48"
        onChange={(e) => setLabel(e.target.value)}
      />
      <Button type="submit" size="sm" variant="outline" disabled={busy || !label.trim()}>
        Add
      </Button>
    </form>
  );
}
