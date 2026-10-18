"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { createCrmAiField, updateCrmAiField } from "@/lib/api/crm";
import type { AiFieldOutputType, AiFieldRecordType, CrmAiField, CrmAiFieldPatch } from "@/lib/api/crm-types";
import { errorMessage } from "@/lib/api/portal";

const RECORD_TYPES: { key: AiFieldRecordType; label: string }[] = [
  { key: "company", label: "Companies" },
  { key: "opportunity", label: "Deals" },
];

const OUTPUT_TYPES: { key: AiFieldOutputType; label: string }[] = [
  { key: "text", label: "Text" },
  { key: "single_select", label: "Single select" },
  { key: "number", label: "Number 0–100" },
];

/** "On track, At risk" → ["On track", "At risk"]. */
function optionsOf(text: string): string[] {
  return text
    .split(",")
    .map((option) => option.trim())
    .filter(Boolean);
}

/**
 * Settings, AI fields — spec 009 FR-CRMAI-FIELD-01; contracts §2.5.
 *
 * The workspace's AI field definitions: name, record type, prompt, output
 * type (text, single select with its options, number 0–100) and enabled.
 * The seeded defaults are edited or disabled like any other; there is no
 * delete, a disabled field's values stay hidden. Record type and output type
 * are fixed once created. Web only (FR-CRMAI-MCP-04). Admins only; the
 * server holds every rule.
 */
export function AiFieldsEditor({
  workspace,
  fields,
  onChanged,
}: {
  workspace: string;
  fields: CrmAiField[];
  onChanged: () => void;
}) {
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="space-y-4">
      {error && (
        <div role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}
      {RECORD_TYPES.map(({ key, label }) => (
        <section key={key} className="rounded-md border">
          <header className="border-b px-3 py-2">
            <h3 className="text-sm font-medium">{label}</h3>
          </header>
          <ul className="divide-y">
            {fields
              .filter((field) => field.record_type === key)
              .map((field) => (
                <FieldRow
                  key={`${field.id}-${field.name}-${field.prompt}-${field.options.join(",")}-${field.enabled}`}
                  workspace={workspace}
                  field={field}
                  onSaved={onChanged}
                  onError={setError}
                />
              ))}
          </ul>
        </section>
      ))}
      <AddField workspace={workspace} onAdded={onChanged} onError={setError} />
    </div>
  );
}

function FieldRow({
  workspace,
  field,
  onSaved,
  onError,
}: {
  workspace: string;
  field: CrmAiField;
  onSaved: () => void;
  onError: (message: string) => void;
}) {
  const [name, setName] = useState(field.name);
  const [prompt, setPrompt] = useState(field.prompt);
  const [options, setOptions] = useState(field.options.join(", "));
  const [enabled, setEnabled] = useState(field.enabled);
  const [busy, setBusy] = useState(false);

  const changes: CrmAiFieldPatch = {};
  if (name.trim() !== field.name) changes.name = name.trim();
  if (prompt.trim() !== field.prompt) changes.prompt = prompt.trim();
  if (field.output_type === "single_select" && optionsOf(options).join(",") !== field.options.join(",")) {
    changes.options = optionsOf(options);
  }
  if (enabled !== field.enabled) changes.enabled = enabled;
  const dirty = Object.keys(changes).length > 0;

  return (
    <li className={`space-y-2 px-3 py-3 ${field.enabled ? "" : "opacity-60"}`}>
      <form
        className="space-y-2"
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          try {
            await updateCrmAiField(workspace, field.id, changes);
            onSaved();
          } catch (err) {
            onError(errorMessage(err));
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="flex flex-wrap items-center gap-2">
          <Input
            aria-label={`Name of ${field.name}`}
            value={name}
            maxLength={60}
            className="h-8 w-56"
            onChange={(e) => setName(e.target.value)}
            required
          />
          <Badge variant="outline">{OUTPUT_TYPES.find((o) => o.key === field.output_type)?.label}</Badge>
          <label className="flex items-center gap-1 text-xs text-muted-foreground">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            Enabled
          </label>
        </div>
        <Textarea
          aria-label={`Prompt of ${field.name}`}
          value={prompt}
          maxLength={2000}
          rows={2}
          onChange={(e) => setPrompt(e.target.value)}
          required
        />
        {field.output_type === "single_select" && (
          <Input
            aria-label={`Options of ${field.name}, separated by commas`}
            value={options}
            className="h-8"
            onChange={(e) => setOptions(e.target.value)}
          />
        )}
        <Button type="submit" size="sm" variant="outline" disabled={busy || !dirty}>
          {busy ? "Saving…" : "Save"}
        </Button>
      </form>
    </li>
  );
}

function AddField({
  workspace,
  onAdded,
  onError,
}: {
  workspace: string;
  onAdded: () => void;
  onError: (message: string) => void;
}) {
  const [name, setName] = useState("");
  const [recordType, setRecordType] = useState<AiFieldRecordType>("company");
  const [outputType, setOutputType] = useState<AiFieldOutputType>("text");
  const [prompt, setPrompt] = useState("");
  const [options, setOptions] = useState("");
  const [busy, setBusy] = useState(false);

  return (
    <form
      className="space-y-3 rounded-md border p-3"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        try {
          await createCrmAiField(workspace, {
            name: name.trim(),
            record_type: recordType,
            prompt: prompt.trim(),
            output_type: outputType,
            ...(outputType === "single_select" ? { options: optionsOf(options) } : {}),
          });
          setName("");
          setPrompt("");
          setOptions("");
          onAdded();
        } catch (err) {
          onError(errorMessage(err));
        } finally {
          setBusy(false);
        }
      }}
    >
      <h3 className="text-sm font-medium">New AI field</h3>
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="ai-field-name">Name</Label>
          <Input
            id="ai-field-name"
            value={name}
            maxLength={60}
            className="h-8 w-56"
            onChange={(e) => setName(e.target.value)}
            required
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ai-field-record-type">Record type</Label>
          <select
            id="ai-field-record-type"
            className="h-8 rounded-md border border-input bg-transparent px-2 text-sm"
            value={recordType}
            onChange={(e) => setRecordType(e.target.value as AiFieldRecordType)}
          >
            {RECORD_TYPES.map(({ key, label }) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ai-field-output-type">Output</Label>
          <select
            id="ai-field-output-type"
            className="h-8 rounded-md border border-input bg-transparent px-2 text-sm"
            value={outputType}
            onChange={(e) => setOutputType(e.target.value as AiFieldOutputType)}
          >
            {OUTPUT_TYPES.map(({ key, label }) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="ai-field-prompt">Prompt</Label>
        <Textarea
          id="ai-field-prompt"
          value={prompt}
          maxLength={2000}
          rows={2}
          onChange={(e) => setPrompt(e.target.value)}
          required
        />
      </div>
      {outputType === "single_select" && (
        <div className="space-y-1.5">
          <Label htmlFor="ai-field-options">Options, separated by commas (2 to 12)</Label>
          <Input
            id="ai-field-options"
            value={options}
            className="h-8"
            onChange={(e) => setOptions(e.target.value)}
            required
          />
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        Record type and output are fixed once the field is created.
      </p>
      <Button type="submit" size="sm" disabled={busy || !name.trim() || !prompt.trim()}>
        {busy ? "Adding…" : "Add field"}
      </Button>
    </form>
  );
}
