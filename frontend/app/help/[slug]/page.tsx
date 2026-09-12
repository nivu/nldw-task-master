import type { Metadata } from "next";

// Re-rendered on the host at most once a minute, so a deploy's new guide text
// is served within a minute even where a CDN kept an older copy.
export const revalidate = 60;
import { notFound } from "next/navigation";

import { getGuide, listGuides } from "@/lib/help";

// Every guide is rendered at build time; an unknown slug is a 404, not a fetch.
export const dynamicParams = false;

export async function generateStaticParams() {
  return (await listGuides()).map(({ slug }) => ({ slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const guide = await getGuide((await params).slug);
  return { title: guide ? `${guide.title} — Help` : "Help" };
}

export default async function GuidePage({ params }: { params: Promise<{ slug: string }> }) {
  const guide = await getGuide((await params).slug);
  if (!guide) notFound();

  // Our own markdown, from the repository, rendered at build time. There is no
  // user-supplied content anywhere in this HTML.
  return <article className="help-prose" dangerouslySetInnerHTML={{ __html: guide.html }} />;
}
