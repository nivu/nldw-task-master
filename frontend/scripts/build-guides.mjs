// Renders docs/guides/*.md into lib/help-content.generated.json at build time.
//
// The help pages used to read the markdown with fs when they rendered. That
// works for a static export but not when a hosting runtime regenerates a page
// later: the docs folder is not part of the deployed bundle. Embedding the
// rendered guides as JSON makes the pages self-contained, so they can be
// revalidated on the host without reaching for files that are not there.
import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { marked } from "marked";

const here = path.dirname(fileURLToPath(import.meta.url));
const guidesDir = path.join(here, "..", "..", "docs", "guides");
const out = path.join(here, "..", "lib", "help-content.generated.json");

function frontmatter(raw) {
  const match = /^---\n([\s\S]*?)\n---\n/.exec(raw);
  if (!match) return { meta: {}, body: raw };
  const meta = {};
  for (const line of match[1].split("\n")) {
    const index = line.indexOf(":");
    if (index > 0) meta[line.slice(0, index).trim()] = line.slice(index + 1).trim();
  }
  return { meta, body: raw.slice(match[0].length) };
}

const files = (await fs.readdir(guidesDir)).filter((name) => name.endsWith(".md")).sort();
const guides = [];
for (const file of files) {
  const raw = await fs.readFile(path.join(guidesDir, file), "utf8");
  const { meta, body } = frontmatter(raw);
  guides.push({
    slug: file.replace(/\.md$/, "").replace(/^\d+-/, ""),
    title: meta.title ?? file,
    summary: meta.summary ?? "",
    order: Number(meta.order ?? 999),
    html: await marked.parse(body, { gfm: true }),
  });
}
guides.sort((a, b) => a.order - b.order);
await fs.writeFile(out, JSON.stringify(guides));
console.log(`guides: ${guides.length} rendered into lib/help-content.generated.json`);
