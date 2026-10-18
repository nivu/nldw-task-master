"use client";

import { TriangleAlert } from "lucide-react";

/**
 * The AI budget is reached — spec 009 FR-CRMAI-LLM-08.
 *
 * Shown to Admins on Settings, AI and Settings, Jobs. While it shows, every
 * AI job run is recorded `skipped` "AI budget reached"; the skipped work is
 * picked up by the next tick after the budget is raised or the month turns.
 * Email sync and the rest of the CRM keep running.
 */
export function AiBudgetBanner({
  reached,
  onShowSettings,
}: {
  reached: boolean;
  /** From the Jobs tab: switches to the AI tab. */
  onShowSettings?: () => void;
}) {
  if (!reached) return null;
  return (
    <div
      role="status"
      className="flex flex-wrap items-start gap-2 rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-sm"
    >
      <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-amber-600" />
      <div className="min-w-0 flex-1">
        <p className="font-medium">This month&apos;s AI budget is reached</p>
        <p className="text-muted-foreground">
          AI jobs are skipped until the budget is raised or the month turns; then they pick up
          where they stopped. Email sync and everything else keep running.
        </p>
      </div>
      {onShowSettings && (
        <button type="button" onClick={onShowSettings} className="text-sm underline-offset-2 hover:underline">
          See the AI settings
        </button>
      )}
    </div>
  );
}
