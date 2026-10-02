"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
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
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  X,
} from "lucide-react";

import { createClient } from "@/lib/supabase/client";
import { getMe } from "@/lib/api/portal";
import type { Me } from "@/lib/api/types";
import { ThemeToggle } from "@/components/shared/theme-toggle";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The shell every signed-in page sits in.
 *
 * NFR-01 — the calendar must be usable on a phone, because leave is often
 * marked from bed or in transit. So navigation is a bottom bar on small
 * screens (where thumbs are) and a collapsible left sidebar on wide ones.
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
    getMe().then(setMe).catch(() => setFailed(true));
  }, []);

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

  // Only the links this person can actually use. The server re-checks every
  // one of these routes regardless — hiding a link is tidiness, not security.
  const links = [
    { href: "/home", label: "Home", icon: Home, section: "mine", show: true },
    { href: "/calendar", label: "Calendar", icon: CalendarDays, section: "mine", show: true },
    { href: "/timesheet", label: "Time", icon: Clock, section: "mine", show: true },
    { href: "/team", label: "Team", icon: Users, section: "team", show: me?.capabilities.team_view },
    { href: "/approvals", label: "Approvals", icon: CheckSquare, section: "team", show: me?.capabilities.team_view },
    { href: "/analytics", label: "Effort", icon: BarChart3, section: "team", show: me?.capabilities.team_view },
    // Spec 003 — the manager tier runs projects from its own page, not from
    // inside the admin panel it may not enter (FR-ROLE-03).
    { href: "/projects", label: "Projects", icon: FolderKanban, section: "manage", show: me?.capabilities.manage_projects },
    { href: "/admin", label: "Admin", icon: Settings, section: "manage", show: me?.capabilities.admin_panel },
    { href: "/account", label: "Account", icon: User, section: "footer", show: true },
  ].filter((link) => link.show);

  const sidebarLink = ({ href, label, icon: Icon }: (typeof links)[number]) => (
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
      <Icon className="size-4 shrink-0" />
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
        <main className="mx-auto w-full max-w-5xl px-4 py-6 pb-20 md:pb-6">{children}</main>
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
