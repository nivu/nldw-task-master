"use client";

import { createContext, useContext, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ArrowLeft,
  CalendarCheck,
  CircleHelp,
  LogOut,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  User,
} from "lucide-react";

import { WorkspaceSwitcher } from "@/components/crm/workspace-switcher";
import { ThemeToggle } from "@/components/shared/theme-toggle";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { CrmBootstrap, CrmWorkspace } from "@/lib/api/crm-types";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";

/**
 * The CRM's own shell — spec 007 FR-CRM-UI-01 to -04, D9; plan §5.9.
 *
 * A separate full-width area with its own sidebar and workspace switcher,
 * built the way `(portal)/layout.tsx` is: a collapsible sidebar on wide
 * screens, a bottom bar with a More sheet on a phone. A CRM-only seller sees
 * no TVA link anywhere; a staff member gets Back to TVA (FR-CRM-UI-03).
 *
 * The sidebar holds what exists so far: Today and, for admins, Settings.
 * Each later slice adds its own entry (Pipeline, Companies, People, …).
 */

const COLLAPSED_KEY = "crm.sidebar.collapsed";

interface CrmState {
  bootstrap: CrmBootstrap;
  workspaces: CrmWorkspace[];
  reload: () => void;
}

const CrmStateContext = createContext<CrmState | null>(null);

/** The workspace, role, lists and the viewer's preferences, for any page
 *  inside `/crm/[ws]`. */
export function useCrm(): CrmState {
  const state = useContext(CrmStateContext);
  if (!state) throw new Error("useCrm is used outside the CRM shell");
  return state;
}

export function CrmShell({
  bootstrap,
  workspaces,
  reload,
  children,
}: CrmState & { children: React.ReactNode }) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const ws = bootstrap.workspace.key;
  const staff = bootstrap.me.account_type === "staff";

  // A per-browser convenience; storage can be unavailable (private windows).
  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setCollapsed(window.localStorage.getItem(COLLAPSED_KEY) === "1");
    } catch {}
  }, []);

  function toggleCollapsed() {
    setCollapsed((c) => {
      try {
        window.localStorage.setItem(COLLAPSED_KEY, c ? "0" : "1");
      } catch {}
      return !c;
    });
  }

  async function signOut() {
    await createClient().auth.signOut();
    window.location.href = "/auth/login";
  }

  // Hiding a link is tidiness, not security: the server re-checks every route.
  const links = [
    { href: `/crm/${ws}`, label: "Today", icon: CalendarCheck, show: true },
    {
      href: `/crm/${ws}/settings`,
      label: "Settings",
      icon: Settings,
      show: bootstrap.role === "admin",
    },
  ].filter((link) => link.show);

  const footer = [
    { href: "/crm/account", label: "Account", icon: User, show: true },
    // FR-CRM-UI-03, -04: staff only. A CRM-only seller never sees TVA.
    { href: "/home", label: "Back to TVA", icon: ArrowLeft, show: staff },
  ].filter((link) => link.show);

  const navLink = (
    { href, label, icon: Icon }: (typeof links)[number],
    { onClick, compact = collapsed }: { onClick?: () => void; compact?: boolean } = {}
  ) => (
    <Link
      key={href}
      href={href}
      onClick={onClick}
      title={compact ? label : undefined}
      aria-label={compact ? label : undefined}
      className={cn(
        "flex items-center gap-3 rounded-md px-2.5 py-1.5 text-sm transition-colors",
        compact && "justify-center px-0",
        pathname === href
          ? "bg-muted font-medium text-foreground"
          : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
      )}
    >
      <Icon className="size-4 shrink-0" />
      {!compact && label}
    </Link>
  );

  const tools = (
    <>
      {/* /help is the TVA guide, inside the TVA flow: staff only (FR-CRM-UI-04). */}
      {staff && (
        <Button
          variant="ghost"
          size="icon"
          aria-label="Help"
          nativeButton={false}
          render={<Link href="/help" />}
        >
          <CircleHelp className="size-4" />
        </Button>
      )}
      <ThemeToggle />
      <Button variant="ghost" size="icon" onClick={signOut} aria-label="Sign out">
        <LogOut className="size-4" />
      </Button>
    </>
  );

  return (
    <CrmStateContext.Provider value={{ bootstrap, workspaces, reload }}>
      <div className="flex min-h-screen flex-col" data-testid="crm-shell">
        <aside
          className={cn(
            "fixed inset-y-0 left-0 z-40 hidden flex-col border-r bg-background md:flex",
            collapsed ? "w-16" : "w-60"
          )}
        >
          <div className={cn("flex items-center gap-2 px-3 py-4", collapsed && "justify-center px-0")}>
            {!collapsed && (
              <Link href={`/crm/${ws}`} className="flex-1 truncate font-heading text-sm font-semibold">
                Nunnari CRM
              </Link>
            )}
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleCollapsed}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-expanded={!collapsed}
            >
              {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
            </Button>
          </div>

          {!collapsed && (
            <div className="px-2 pb-3">
              <WorkspaceSwitcher current={ws} workspaces={workspaces} />
            </div>
          )}

          <nav className="flex-1 space-y-0.5 overflow-y-auto px-2">
            {links.map((link) => navLink(link))}
          </nav>

          <div className="space-y-1 border-t px-2 py-3">
            {footer.map((link) => navLink(link))}
            <div className={cn("flex items-center gap-1 px-1", collapsed && "flex-col")}>{tools}</div>
            {!collapsed && (
              <p className="truncate px-2.5 pt-1 text-xs text-muted-foreground">
                {bootstrap.me.display_name}
              </p>
            )}
          </div>
        </aside>

        {/* Phones: a slim header with the switcher; navigation is the bottom bar. */}
        <header className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur md:hidden">
          <div className="flex w-full items-center gap-2 px-4 py-3">
            <div className="min-w-0 flex-1">
              <WorkspaceSwitcher current={ws} workspaces={workspaces} />
            </div>
            <div className="flex items-center gap-1">{tools}</div>
          </div>
        </header>

        {/* Full width (D9): CRM tables need the room TVA's 5xl column lacks. */}
        <div className={cn("flex-1", collapsed ? "md:pl-16" : "md:pl-60")}>
          <main className="w-full px-4 py-6 pb-20 md:px-6 md:pb-6">{children}</main>
        </div>

        <nav className="fixed inset-x-0 bottom-0 z-40 flex border-t bg-background md:hidden">
          {links.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex flex-1 flex-col items-center gap-0.5 py-2 text-[11px]",
                pathname === href ? "text-foreground" : "text-muted-foreground"
              )}
            >
              <Icon className="size-5" />
              {label}
            </Link>
          ))}
          <button
            type="button"
            onClick={() => setMoreOpen(true)}
            aria-expanded={moreOpen}
            className="flex flex-1 flex-col items-center gap-0.5 py-2 text-[11px] text-muted-foreground"
          >
            <MoreHorizontal className="size-5" />
            More
          </button>
        </nav>

        <Sheet open={moreOpen} onOpenChange={setMoreOpen}>
          <SheetContent side="bottom">
            <SheetHeader>
              <SheetTitle>{bootstrap.me.display_name}</SheetTitle>
            </SheetHeader>
            <div className="space-y-1 px-4 pb-6">
              {footer.map((link) =>
                navLink(link, { onClick: () => setMoreOpen(false), compact: false })
              )}
            </div>
          </SheetContent>
        </Sheet>
      </div>
    </CrmStateContext.Provider>
  );
}
