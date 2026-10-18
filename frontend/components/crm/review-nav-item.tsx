"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Inbox } from "lucide-react";

import { useCrm } from "@/components/crm/shell";
import { cn } from "@/lib/utils";

/**
 * The sidebar's Review entry — spec 009 FR-CRMAI-REV-03, FR-CRMAI-FLAG-04;
 * plan §5.9, §6 rule 3a (b).
 *
 * Shown while the queue is visible (any phase on, or the person has open
 * items), with the open count from bootstrap; never to a Viewer, who has no
 * queue. Its own file, so the CRM shell gains one line for it. Hiding it is
 * tidiness, not security: the server re-checks every route.
 */
export function ReviewNavItem({ collapsed }: { collapsed: boolean }) {
  const { bootstrap } = useCrm();
  const pathname = usePathname();
  if (!bootstrap.ai.review_visible || bootstrap.role === "viewer") return null;
  const href = `/crm/${bootstrap.workspace.key}/review`;
  const count = bootstrap.ai.open_review_count;
  const label = count > 0 ? `Review, ${count} open` : "Review";
  const active = pathname === href || pathname.startsWith(`${href}/`);
  return (
    <Link
      href={href}
      title={collapsed ? label : undefined}
      aria-label={collapsed ? label : undefined}
      className={cn(
        "flex items-center gap-3 rounded-md px-2.5 py-1.5 text-sm transition-colors",
        collapsed && "justify-center px-0",
        active
          ? "bg-muted font-medium text-foreground"
          : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
      )}
    >
      <Inbox className="size-4 shrink-0" />
      {!collapsed && <span className="flex-1">Review</span>}
      {!collapsed && count > 0 && (
        <span className="rounded-full bg-muted px-1.5 text-xs tabular-nums text-foreground">{count}</span>
      )}
    </Link>
  );
}
