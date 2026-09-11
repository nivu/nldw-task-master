import Link from "next/link";

import { ThemeToggle } from "@/components/shared/theme-toggle";
import { HelpNav } from "@/components/shared/help-nav";
import { listGuides } from "@/lib/help";

/**
 * The public help section — no sign-in required.
 *
 * Its own shell rather than the portal's, because the portal layout loads the
 * signed-in person and this must work for somebody who has never signed in.
 */
export default async function HelpLayout({ children }: { children: React.ReactNode }) {
  const guides = await listGuides();

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-5xl items-center gap-3 px-4 py-3">
          <Link href="/help" className="font-heading text-sm font-semibold">
            Nunnari Portal
            <span className="ml-2 font-normal text-muted-foreground">Help</span>
          </Link>
          <div className="ml-auto flex items-center gap-1">
            <ThemeToggle />
            <Link
              href="/calendar"
              className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground"
            >
              Open the portal
            </Link>
          </div>
        </div>
      </header>

      <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-4 py-6 md:flex-row">
        <HelpNav guides={guides.map(({ slug, title }) => ({ slug, title }))} />
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}
