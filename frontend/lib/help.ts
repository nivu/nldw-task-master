import "server-only";

import guides from "@/lib/help-content.generated.json";

/**
 * The public how-to guides.
 *
 * The markdown lives in `docs/guides/` at the repository root — the home the
 * project's rules give to "how the system works today". `scripts/build-guides.mjs`
 * renders it into `lib/help-content.generated.json` before every build and
 * dev start, so the pages carry their content with them and can be
 * revalidated on the host without the docs folder being present at runtime.
 *
 * Public on purpose: the guides describe how screens work, never who is on
 * them or what anything costs. Keep them that way — no names, no figures, no
 * screenshots with data.
 */

export interface Guide {
  slug: string;
  title: string;
  summary: string;
  order: number;
  html: string;
}

const GUIDES: Guide[] = guides as Guide[];

export async function listGuides(): Promise<Guide[]> {
  return GUIDES;
}

export async function getGuide(slug: string): Promise<Guide | undefined> {
  return GUIDES.find((guide) => guide.slug === slug);
}
