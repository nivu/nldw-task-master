"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

/** The list of guides — a sidebar on wide screens, a scrolling strip on a phone. */
export function HelpNav({ guides }: { guides: { slug: string; title: string }[] }) {
  const pathname = usePathname();

  return (
    <nav aria-label="Guides" className="md:w-56 md:shrink-0">
      <ul className="-mx-4 flex gap-1 overflow-x-auto px-4 md:mx-0 md:flex-col md:px-0">
        {guides.map((guide) => {
          const href = `/help/${guide.slug}`;
          return (
            <li key={guide.slug} className="shrink-0">
              <Link
                href={href}
                className={cn(
                  "block rounded-md px-3 py-1.5 text-sm whitespace-nowrap md:whitespace-normal",
                  pathname === href
                    ? "bg-muted font-medium text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {guide.title}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
