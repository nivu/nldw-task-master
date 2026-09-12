import type { Metadata } from "next";

// Re-rendered on the host at most once a minute, so a deploy's new guide text
// is served within a minute even where a CDN kept an older copy.
export const revalidate = 60;
import Link from "next/link";

import { Card, CardContent } from "@/components/ui/card";
import { listGuides } from "@/lib/help";

export const metadata: Metadata = { title: "Help — Nunnari Employee Portal" };

export default async function HelpIndex() {
  const guides = await listGuides();

  return (
    <div className="space-y-5">
      <div>
        <h1 className="font-heading text-lg font-semibold">How to use the portal</h1>
        <p className="text-sm text-muted-foreground">
          Short guides, one per job. Start with the first if you are new.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {guides.map((guide) => (
          <Link key={guide.slug} href={`/help/${guide.slug}`} className="group">
            <Card className="h-full transition-colors group-hover:bg-muted/60">
              <CardContent className="space-y-1 p-4">
                <p className="text-sm font-medium">{guide.title}</p>
                <p className="text-xs text-muted-foreground">{guide.summary}</p>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
