"use client";

import { useRouter } from "next/navigation";
import { ChevronsUpDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { selectCrmWorkspace } from "@/lib/api/crm";
import type { CrmWorkspace } from "@/lib/api/crm-types";

/**
 * The workspace switcher — spec 007 FR-CRM-WS-03, -04, FR-CRM-UI-01.
 *
 * Lists only the person's own workspaces. Choosing one is remembered on the
 * server, so the next visit from any device, and an MCP read that names no
 * workspace (D13), opens it.
 */
export function WorkspaceSwitcher({
  current,
  workspaces,
}: {
  current: string;
  workspaces: CrmWorkspace[];
}) {
  const router = useRouter();
  const name = workspaces.find((w) => w.key === current)?.name ?? current;

  async function choose(key: string) {
    if (key === current) return;
    try {
      await selectCrmWorkspace(key);
    } catch {
      // Remembering is a convenience; the page itself still opens.
    }
    router.push(`/crm/${key}`);
  }

  if (workspaces.length < 2) {
    return <p className="truncate px-2.5 text-sm font-medium">{name}</p>;
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button variant="ghost" className="w-full justify-between px-2.5" aria-label="Switch workspace" />
        }
      >
        <span className="truncate">{name}</span>
        <ChevronsUpDown className="size-4 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent className="min-w-56">
        <DropdownMenuGroup>
          <DropdownMenuLabel>Workspaces</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={current} onValueChange={(key) => choose(String(key))}>
            {workspaces.map((w) => (
              <DropdownMenuRadioItem key={w.key} value={w.key}>
                {w.name}
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
