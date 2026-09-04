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
 * screens (where thumbs are) and a top bar on wide ones.
 */
export default function PortalLayout({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [failed, setFailed] = useState(false);
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    getMe().then(setMe).catch(() => setFailed(true));
  }, []);

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
    { href: "/calendar", label: "Calendar", icon: CalendarDays, show: true },
    { href: "/timesheet", label: "Time", icon: Clock, show: true },
    { href: "/team", label: "Team", icon: Users, show: me?.capabilities.team_view },
    { href: "/approvals", label: "Approvals", icon: CheckSquare, show: me?.capabilities.team_view },
    { href: "/analytics", label: "Effort", icon: BarChart3, show: me?.capabilities.team_view },
    { href: "/admin", label: "Admin", icon: Settings, show: me?.capabilities.admin_panel },
    { href: "/account", label: "Account", icon: User, show: true },
  ].filter((link) => link.show);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-5xl items-center gap-4 px-4 py-3">
          <Link href="/calendar" className="font-heading text-sm font-semibold">
            Nunnari Portal
          </Link>

          <nav className="ml-4 hidden gap-1 sm:flex">
            {links.map(({ href, label }) => (
              <Link
                key={href}
                href={href}
                className={cn(
                  "rounded-md px-3 py-1.5 text-sm transition-colors",
                  pathname === href
                    ? "bg-muted font-medium text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {label}
              </Link>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-1">
            {me && (
              <span className="hidden text-sm text-muted-foreground md:inline">
                {me.display_name}
              </span>
            )}
            <ThemeToggle />
            <Button variant="ghost" size="icon" onClick={signOut} aria-label="Sign out">
              <LogOut className="size-4" />
            </Button>
          </div>
        </div>
      </header>

      {/* pb-20 leaves room for the mobile bar so the last row is never
          hidden behind it. */}
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6 pb-20 sm:pb-6">
        {children}
      </main>

      <nav className="fixed inset-x-0 bottom-0 z-40 border-t bg-background sm:hidden">
        <div className="flex">
          {links.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              onClick={() => router.prefetch?.(href)}
              className={cn(
                "flex flex-1 flex-col items-center gap-0.5 py-2 text-[11px]",
                pathname === href ? "text-foreground" : "text-muted-foreground"
              )}
            >
              <Icon className="size-5" />
              {label}
            </Link>
          ))}
        </div>
      </nav>
    </div>
  );
}
