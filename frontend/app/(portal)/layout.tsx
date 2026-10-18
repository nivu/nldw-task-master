"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Briefcase,
  CalendarDays,
  Users,
  CheckSquare,
  Settings,
  LogOut,
  User,
  Clock,
  BarChart3,
  FolderKanban,
  CircleHelp,
  Home,
  LayoutDashboard,
  ListTodo,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  X,
} from "lucide-react";

import { createClient } from "@/lib/supabase/client";
import { getMe } from "@/lib/api/portal";
import type { Me } from "@/lib/api/types";
import { ThemeToggle } from "@/components/shared/theme-toggle";
import { QuickAdd } from "@/components/tasks/quick-add";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The shell every signed-in page sits in.
 *
 * NFR-01 — the calendar must be usable on a phone, because leave is often
 * marked from bed or in transit. So navigation is a bottom bar on small
 * screens (where thumbs are) and a collapsible left sidebar on wide ones.
 *
 * Spec 007 FR-CRM-UI-03, -04: `/me` is asked first and no page renders (so no
 * other TVA request fires) until it answers. A CRM-only seller is sent to
 * `/crm` and never sees this shell; a staff member with a CRM membership
 * gets a CRM link.
 *
 * Spec 008 FR-TASK-ADD-01, FR-TASK-LIST-08, FR-TASK-ROLL-03: with phase 1 of
 * tasks switched on, a My Tasks link and the quick-add box (the desktop
 * sidebar's header and the phone header); below it, nothing of tasks shows.
 */

const COLLAPSED_KEY = "portal.sidebar.collapsed";

// Section headings for the sidebar. A section shows only if the person has a
// link in it, so a user sees "My work" alone and a lead adds "Team".
const SECTIONS = [
  { id: "mine", label: "My work" },
  { id: "team", label: "Team" },
  { id: "manage", label: "Manage" },
] as const;
export default function PortalLayout({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [failed, setFailed] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    getMe()
      .then((who) => {
        if (who.account_type === "crm_only") router.replace("/crm");
        setMe(who);
      })
      .catch(() => setFailed(true));
  }, [router]);

  // A per-browser convenience. Storage can be unavailable (private windows,
  // blocked site data); the sidebar then simply starts expanded.
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

  if (failed) {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-4 p-6 text-center">
        <p className="text-sm text-muted-foreground">
          Your account could not be loaded. It may have been deactivated.
        </p>
        <Button onClick={signOut}>Sign out</Button>
      </main>
    );
  }

  // A CRM-only account renders nothing here while it is sent to /crm.
  if (me?.account_type === "crm_only") return null;

  // Only the links this person can actually use. The server re-checks every
  // one of these routes regardless — hiding a link is tidiness, not security.
  const caps = me?.account_type === "staff" ? me.capabilities : undefined;
  const tasksOn = (me?.tasks.level ?? 0) >= 1;
  const links = [
    { href: "/home", label: "Home", icon: Home, color: "text-blue-600 dark:text-blue-400", section: "mine", show: true },
    { href: "/calendar", label: "Calendar", icon: CalendarDays, color: "text-violet-600 dark:text-violet-400", section: "mine", show: true },
    { href: "/timesheet", label: "Time", icon: Clock, color: "text-amber-600 dark:text-amber-400", section: "mine", show: true },
    // Spec 008 FR-TASK-LIST-08 — only with phase 1 switched on.
    { href: "/tasks", label: "My Tasks", icon: ListTodo, color: "text-green-600 dark:text-green-400", section: "mine", show: tasksOn },
    { href: "/team", label: "Team", icon: Users, color: "text-pink-600 dark:text-pink-400", section: "team", show: caps?.team_view },
    { href: "/approvals", label: "Approvals", icon: CheckSquare, color: "text-teal-600 dark:text-teal-400", section: "team", show: caps?.team_view },
    { href: "/analytics", label: "Effort", icon: BarChart3, color: "text-orange-600 dark:text-orange-400", section: "team", show: caps?.team_view },
    // Spec 003 FR-DASH — by the owner's authorisation, not by role.
    { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, color: "text-indigo-600 dark:text-indigo-400", section: "manage", show: caps?.dashboard },
    // Spec 003 — the manager tier runs projects from its own page, not from
    // inside the admin panel it may not enter (FR-ROLE-03).
    { href: "/projects", label: "Projects", icon: FolderKanban, color: "text-cyan-600 dark:text-cyan-400", section: "manage", show: caps?.manage_projects },
    { href: "/admin", label: "Admin", icon: Settings, color: "text-slate-600 dark:text-slate-400", section: "manage", show: caps?.admin_panel },
    // Spec 007 FR-CRM-UI-03 — only with a membership; /crm refuses the rest.
    { href: "/crm", label: "CRM", icon: Briefcase, color: "text-rose-600 dark:text-rose-400", section: "mine", show: (me?.crm.workspaces.length ?? 0) > 0 },
    { href: "/account", label: "Account", icon: User, color: "text-gray-600 dark:text-gray-400", section: "footer", show: true },
  ].filter((link) => link.show);

  const sidebarLink = ({ href, label, icon: Icon, color }: (typeof links)[number]) => (
    <Link
      key={href}
      href={href}
      title={collapsed ? label : undefined}
      aria-label={collapsed ? label : undefined}
      className={cn(
        "flex items-center gap-3 rounded-md px-2.5 py-1.5 text-sm transition-colors",
        collapsed && "justify-center px-0",
        pathname === href
          ? "bg-muted font-medium text-foreground"
          : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
      )}
    >
      <Icon className={cn("size-4 shrink-0", color)} />
      {!collapsed && label}
    </Link>
  );

  return (
    <div className="flex min-h-screen flex-col">
      {/* Wide screens: a left sidebar, collapsible to icons. */}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 hidden flex-col border-r bg-background md:flex",
          collapsed ? "w-16" : "w-60"
        )}
      >
        <div className={cn("flex items-center gap-2 px-3 py-4", collapsed && "justify-center px-0")}>
          {!collapsed && (
            <Link href="/home" className="flex-1 truncate font-heading text-sm font-semibold">
              Nunnari Portal
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

        {tasksOn && (
          <QuickAdd
            className={collapsed ? "flex justify-center pb-3" : "px-3 pb-3"}
            collapsed={collapsed}
            onExpand={toggleCollapsed}
          />
        )}

        <nav className="flex-1 space-y-4 overflow-y-auto px-2">
          {SECTIONS.map(({ id, label }) => {
            const mine = links.filter((l) => l.section === id);
            if (mine.length === 0) return null;
            return (
              <div key={id} className="space-y-0.5">
                {collapsed ? (
                  <div className="mx-2 mb-1 border-t" />
                ) : (
                  <p className="px-2.5 pb-1 text-xs font-medium text-muted-foreground">{label}</p>
                )}
                {mine.map(sidebarLink)}
              </div>
            );
          })}
        </nav>

        <div className="space-y-1 border-t px-2 py-3">
          {links.filter((l) => l.section === "footer").map(sidebarLink)}
          <div className={cn("flex items-center gap-1 px-1", collapsed && "flex-col")}>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Help"
              nativeButton={false}
              render={<Link href="/help" />}
            >
              <CircleHelp className="size-4" />
            </Button>
            <ThemeToggle />
            <Button variant="ghost" size="icon" onClick={signOut} aria-label="Sign out">
              <LogOut className="size-4" />
            </Button>
          </div>
          {me && !collapsed && (
            <p className="truncate px-2.5 pt-1 text-xs text-muted-foreground">{me.display_name}</p>
          )}
        </div>
      </aside>

      {/* Phones and small tablets: a slim header; navigation is the bottom bar. */}
      <header className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur md:hidden">
        <div className="mx-auto flex w-full max-w-5xl items-center gap-4 px-4 py-3">
          <Link href="/home" className="font-heading text-sm font-semibold">
            Nunnari Portal
          </Link>

          {tasksOn && <QuickAdd className="min-w-0 flex-1" />}

          <div className="ml-auto flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              aria-label="Help"
              nativeButton={false}
              render={<Link href="/help" />}
            >
              <CircleHelp className="size-4" />
            </Button>
            <ThemeToggle />
            <Button variant="ghost" size="icon" onClick={signOut} aria-label="Sign out">
              <LogOut className="size-4" />
            </Button>
          </div>
        </div>
      </header>

      {/* pb-20 leaves room for the mobile bar so the last row is never
          hidden behind it; the left padding makes room for the sidebar. */}
      <div className={cn("flex-1", collapsed ? "md:pl-16" : "md:pl-60")}>
        <main className="mx-auto w-full max-w-5xl px-4 py-6 pb-20 md:pb-6">{me && children}</main>
      </div>

      {/* Phone: the four everyday links, and a More sheet for the rest, so
          eight items are never squeezed into 400px. */}
      <nav className="fixed inset-x-0 bottom-0 z-40 border-t bg-background md:hidden">
        {moreOpen && (
          <div className="border-b p-2">
            <div className="grid grid-cols-3 gap-1">
              {links.slice(4).map(({ href, label, icon: Icon }) => (
                <Link
                  key={href}
                  href={href}
                  onClick={() => setMoreOpen(false)}
                  className={cn(
                    "flex flex-col items-center gap-0.5 rounded-md py-2 text-[11px]",
                    pathname === href ? "bg-muted text-foreground" : "text-muted-foreground"
                  )}
                >
                  <Icon className="size-5" />
                  {label}
                </Link>
              ))}
            </div>
          </div>
        )}
        <div className="flex">
          {links.slice(0, 4).map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              onClick={() => {
                setMoreOpen(false);
                router.prefetch?.(href);
              }}
              className={cn(
                "flex flex-1 flex-col items-center gap-0.5 py-2 text-[11px]",
                pathname === href ? "text-foreground" : "text-muted-foreground"
              )}
            >
              <Icon className="size-5" />
              {label}
            </Link>
          ))}
          {links.length > 4 && (
            <button
              type="button"
              onClick={() => setMoreOpen((o) => !o)}
              aria-expanded={moreOpen}
              className={cn(
                "flex flex-1 flex-col items-center gap-0.5 py-2 text-[11px]",
                moreOpen || links.slice(4).some((l) => l.href === pathname) ? "text-foreground" : "text-muted-foreground"
              )}
            >
              {moreOpen ? <X className="size-5" /> : <MoreHorizontal className="size-5" />}
              More
            </button>
          )}
        </div>
      </nav>
    </div>
  );
}
