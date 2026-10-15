"use client";

import { createContext, useContext, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ArrowLeft,
  Briefcase,
  Building2,
  CalendarCheck,
  CheckSquare,
  CircleHelp,
  Kanban,
  LayoutDashboard,
  LogOut,
  Megaphone,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  Star,
  User,
  Users,
} from "lucide-react";

import { Bell, useUnreadCount } from "@/components/crm/bell";
import { StaleBanner } from "@/components/crm/stale-banner";
import { ViewIcon } from "@/components/crm/view-switcher";
import { WorkspaceSwitcher } from "@/components/crm/workspace-switcher";
import { ThemeToggle } from "@/components/shared/theme-toggle";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { listCrmFavourites, listCrmViews, selectCrmWorkspace } from "@/lib/api/crm";
import type { CrmBootstrap, CrmFavourite, CrmSavedView, CrmWorkspace } from "@/lib/api/crm-types";
import { OBJECT_PATH } from "@/lib/crm/view-fields";
import { createClient } from "@/lib/supabase/client";
import { useAsync } from "@/lib/use-async";
import { cn } from "@/lib/utils";

/**
 * The CRM's own shell — spec 007 FR-CRM-UI-01 to -04, D9; plan §5.9.
 *
 * A separate full-width area with its own sidebar and workspace switcher,
 * built the way `(portal)/layout.tsx` is: a collapsible sidebar on wide
 * screens, a bottom bar with a More sheet on a phone. A CRM-only seller sees
 * no TVA link anywhere; a staff member gets Back to TVA (FR-CRM-UI-03).
 *
 * The sidebar holds what exists so far: Today, Pipeline, Companies, People,
 * Opportunities, Tasks, (1b) Campaigns and Dashboard, the person's favourites (pinned
 * views and records, FR-CRM-UI-02) and, for admins, Settings. Each later
 * slice adds its own entry. On a phone the bottom bar has room for four; the
 * rest, favourites included, are under More.
 *
 * From 1b the tools hold the bell (FR-CRM-NOTIF-01), and an Admin sees a
 * banner above every page while a background job is late (FR-CRM-JOB-03).
 */

const COLLAPSED_KEY = "crm.sidebar.collapsed";
/** How many sections fit the phone's bottom bar beside More. */
const PHONE_BAR = 4;

interface CrmState {
  bootstrap: CrmBootstrap;
  workspaces: CrmWorkspace[];
  reload: () => void;
}

interface CrmFavourites {
  /** The person's pins in this workspace; empty until loaded. */
  favourites: CrmFavourite[];
  reloadFavourites: () => void;
}

const CrmStateContext = createContext<(CrmState & CrmFavourites) | null>(null);

/** The workspace, role, lists and the viewer's preferences, for any page
 *  inside `/crm/[ws]`, and the person's favourites. */
export function useCrm(): CrmState & CrmFavourites {
  const state = useContext(CrmStateContext);
  if (!state) throw new Error("useCrm is used outside the CRM shell");
  return state;
}

/** Where a favourite opens: a view on its object's list, a record's page. */
function favouriteHref(ws: string, fav: CrmFavourite, views: CrmSavedView[]): string | null {
  if (fav.target_type === "record" && fav.record_type) {
    return `/crm/${ws}/${OBJECT_PATH[fav.record_type]}/${fav.target_id}`;
  }
  const view = views.find((v) => v.id === fav.target_id);
  if (!view) return null;
  if (view.system_key === "pipeline") return `/crm/${ws}/pipeline`;
  // Members by status opens on a campaign's page.
  if (view.object === "campaign_member") return `/crm/${ws}/campaigns`;
  return `/crm/${ws}/${OBJECT_PATH[view.object]}?view=${view.id}`;
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
  const [unread, setUnread] = useUnreadCount(bootstrap.unread ?? 0);
  const ws = bootstrap.workspace.key;
  const staff = bootstrap.me.account_type === "staff";
  // The views map a pinned view to its list; a failure leaves the sidebar
  // without favourites rather than without navigation.
  const pins = useAsync(
    () => Promise.all([listCrmFavourites(ws), listCrmViews(ws)]),
    [ws]
  );
  const favourites = pins.data?.[0] ?? [];
  const pinned = favourites
    .map((fav) => ({ fav, href: favouriteHref(ws, fav, pins.data?.[1] ?? []) }))
    .filter((p): p is { fav: CrmFavourite; href: string } => p.href !== null);

  // FR-CRM-WS-04, D13: opening a workspace makes it the last used, not only
  // choosing it in the switcher, so an MCP read that names no workspace
  // follows the one the person actually works in. Any role may record it;
  // remembering is a convenience, so a failure is ignored.
  useEffect(() => {
    selectCrmWorkspace(ws).catch(() => {});
  }, [ws]);

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
    { href: `/crm/${ws}/pipeline`, label: "Pipeline", icon: Kanban, show: true },
    { href: `/crm/${ws}/companies`, label: "Companies", icon: Building2, show: true },
    { href: `/crm/${ws}/people`, label: "People", icon: Users, show: true },
    { href: `/crm/${ws}/opportunities`, label: "Opportunities", icon: Briefcase, show: true },
    { href: `/crm/${ws}/tasks`, label: "Tasks", icon: CheckSquare, show: true },
    // 1b (FR-CRM-UI-02, FR-CRM-CAMP).
    { href: `/crm/${ws}/campaigns`, label: "Campaigns", icon: Megaphone, show: true },
    // FR-CRM-DASH-01: Admins and Members (their own numbers); Viewers have none.
    {
      href: `/crm/${ws}/dashboard`,
      label: "Dashboard",
      icon: LayoutDashboard,
      show: bootstrap.role !== "viewer",
    },
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

  // A record page (/companies/<id>) keeps its section lit; Today is the root.
  const isActive = (href: string) =>
    pathname === href || (href !== `/crm/${ws}` && pathname.startsWith(`${href}/`));

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
        isActive(href)
          ? "bg-muted font-medium text-foreground"
          : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
      )}
    >
      <Icon className="size-4 shrink-0" />
      {!compact && label}
    </Link>
  );

  const favouriteLink = (
    fav: CrmFavourite,
    href: string,
    { onClick, compact = collapsed }: { onClick?: () => void; compact?: boolean } = {}
  ) => {
    const name = fav.name ?? "Untitled";
    return (
      <Link
        key={fav.id}
        href={href}
        onClick={onClick}
        title={compact ? name : undefined}
        aria-label={compact ? name : undefined}
        className={cn(
          "flex items-center gap-3 rounded-md px-2.5 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground",
          compact && "justify-center px-0"
        )}
      >
        {fav.target_type === "view" ? (
          <ViewIcon name={pins.data?.[1].find((v) => v.id === fav.target_id)?.icon} />
        ) : (
          <Star className="size-4 shrink-0" />
        )}
        {!compact && <span className="truncate">{name}</span>}
      </Link>
    );
  };

  const tools = (
    <>
      <Bell count={unread} onCount={setUnread} timeZone={bootstrap.me.timezone} />
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
    <CrmStateContext.Provider
      value={{ bootstrap, workspaces, reload, favourites, reloadFavourites: pins.reload }}
    >
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
            {pinned.length > 0 && (
              <div className="pt-3" aria-label="Favourites" role="group">
                {!collapsed && (
                  <p className="px-2.5 pb-1 text-xs font-medium text-muted-foreground">Favourites</p>
                )}
                {pinned.map(({ fav, href }) => favouriteLink(fav, href))}
              </div>
            )}
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
          <main className="w-full px-4 py-6 pb-20 md:px-6 md:pb-6">
            <StaleBanner
              jobs={bootstrap.stale_jobs ?? []}
              workspace={ws}
              timeZone={bootstrap.me.timezone}
            />
            {children}
          </main>
        </div>

        <nav className="fixed inset-x-0 bottom-0 z-40 flex border-t bg-background md:hidden">
          {links.slice(0, PHONE_BAR).map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex flex-1 flex-col items-center gap-0.5 py-2 text-[11px]",
                isActive(href) ? "text-foreground" : "text-muted-foreground"
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
              {links.slice(PHONE_BAR).map((link) =>
                navLink(link, { onClick: () => setMoreOpen(false), compact: false })
              )}
              {pinned.length > 0 && (
                <p className="px-2.5 pt-2 pb-1 text-xs font-medium text-muted-foreground">Favourites</p>
              )}
              {pinned.map(({ fav, href }) =>
                favouriteLink(fav, href, { onClick: () => setMoreOpen(false), compact: false })
              )}
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
