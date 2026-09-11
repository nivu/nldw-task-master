import "server-only";

import { promises as fs } from "node:fs";
import path from "node:path";

import { marked } from "marked";

/**
 * The public how-to guides.
 *
 * The guides are markdown files in `docs/guides/` at the repository root —
 * the documentation home the project's rules give to "how the system works
 * today" — and are read once at build time, so `/help` is static HTML and
 * changes to a guide ship with the next deploy. Nothing here runs on a request.
 *
 * Public on purpose: the guides describe how screens work, never who is on
 * them or what anything costs. Keep them that way — no names, no figures, no
 * screenshots with data.
 */

const GUIDES_DIR = path.join(process.cwd(), "..", "docs", "guides");

export interface Guide {
  slug: string;
  title: string;
  summary: string;
  order: number;
  html: string;
}

function frontmatter(raw: string): { meta: Record<string, string>; body: string } {
  const match = /^---\n([\s\S]*?)\n---\n/.exec(raw);
  if (!match) return { meta: {}, body: raw };
  const meta: Record<string, string> = {};
  for (const line of match[1].split("\n")) {
    const index = line.indexOf(":");
    if (index > 0) meta[line.slice(0, index).trim()] = line.slice(index + 1).trim();
  }
  return { meta, body: raw.slice(match[0].length) };
}

export async function listGuides(): Promise<Guide[]> {
  const files = (await fs.readdir(GUIDES_DIR)).filter((name) => name.endsWith(".md"));
  const guides = await Promise.all(
    files.map(async (file) => {
      const raw = await fs.readFile(path.join(GUIDES_DIR, file), "utf8");
      const { meta, body } = frontmatter(raw);
      return {
        // "03-logging-your-day.md" → "logging-your-day"; the number only orders
        // the files in a directory listing.
        slug: file.replace(/\.md$/, "").replace(/^\d+-/, ""),
        title: meta.title ?? file,
        summary: meta.summary ?? "",
        order: Number(meta.order ?? 999),
        html: await marked.parse(body, { gfm: true }),
      };
    })
  );
  return guides.sort((a, b) => a.order - b.order);
}

export async function getGuide(slug: string): Promise<Guide | undefined> {
  return (await listGuides()).find((guide) => guide.slug === slug);
}
