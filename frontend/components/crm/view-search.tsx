"use client";

import { Search, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Input } from "@/components/ui/input";

/**
 * Search within a view — spec 007 FR-CRM-VIEW-09.
 *
 * The words go to the server as the query's `q`, matched literally (a "%" or
 * "_" is that character, not a wildcard), on top of the view's filters. The
 * search waits for a pause in typing; Escape clears it.
 */
export function ViewSearch({
  value,
  onChange,
  label,
}: {
  value: string;
  onChange: (q: string) => void;
  label: string;
}) {
  const [text, setText] = useState(value);

  useEffect(() => {
    const trimmed = text.trim();
    if (trimmed === value) return;
    const timer = setTimeout(() => onChange(trimmed), 300);
    return () => clearTimeout(timer);
  }, [text, value, onChange]);

  return (
    <div className="relative w-full sm:max-w-xs">
      <Search className="pointer-events-none absolute top-1/2 left-2 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        type="search"
        aria-label={label}
        placeholder="Search this view"
        value={text}
        maxLength={200}
        className="pl-8"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape" && text) {
            e.stopPropagation();
            setText("");
            onChange("");
          }
        }}
      />
      {text && (
        <button
          type="button"
          aria-label="Clear search"
          className="absolute top-1/2 right-2 -translate-y-1/2 rounded text-muted-foreground hover:text-foreground"
          onClick={() => {
            setText("");
            onChange("");
          }}
        >
          <X className="size-4" />
        </button>
      )}
    </div>
  );
}
