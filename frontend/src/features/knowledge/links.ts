/** Obsidian-style link resolution, shared by the renderer and tests. */

export type TreeNode = {
  name: string;
  path: string;
  kind: "dir" | "file";
  children: TreeNode[];
};

export function flattenFiles(node: TreeNode): string[] {
  if (node.kind === "file") return [node.path];
  return node.children.flatMap(flattenFiles);
}

const dirOf = (path: string) => (path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "");
const stripMd = (p: string) => p.replace(/\.md$/i, "");

/** Resolve a wikilink target (``Note``, ``Folder/Note``, ``img.png``, ``Note#Heading``). */
export function resolveWikilink(target: string, files: string[], from: string): string | null {
  const t = stripMd(target.split(/[#^]/)[0].trim().replace(/\\/g, "/")).toLowerCase();
  if (!t) return null;
  const candidates = files.filter((f) => {
    const lower = f.toLowerCase();
    const noExt = stripMd(lower);
    const base = noExt.slice(noExt.lastIndexOf("/") + 1);
    const fullBase = lower.slice(lower.lastIndexOf("/") + 1);
    if (t.includes("/")) return noExt === t || noExt.endsWith("/" + t) || lower.endsWith("/" + t);
    return base === t || fullBase === t;
  });
  if (candidates.length === 0) return null;
  const here = dirOf(from).toLowerCase();
  return [...candidates].sort((a, b) => {
    const sameA = dirOf(a).toLowerCase() === here ? 0 : 1;
    const sameB = dirOf(b).toLowerCase() === here ? 0 : 1;
    return sameA - sameB || a.split("/").length - b.split("/").length || a.localeCompare(b);
  })[0];
}

/** Resolve a relative Markdown link/image path against the current document. */
export function resolveRelative(href: string, from: string): string {
  const parts = dirOf(from) ? dirOf(from).split("/") : [];
  for (const seg of decodeURIComponent(href.split("#")[0]).split("/")) {
    if (seg === "..") parts.pop();
    else if (seg && seg !== ".") parts.push(seg);
  }
  return parts.join("/");
}

export const isExternal = (href: string) => /^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith("//");

export const assetUrl = (path: string) => `/api/knowledge/asset?path=${encodeURIComponent(path)}`;

export const isMarkdown = (path: string) => /\.(md|txt)$/i.test(path);
export const isImage = (path: string) => /\.(png|jpe?g|gif|svg|webp|bmp)$/i.test(path);

/** Split leading YAML frontmatter from the body. */
export function splitFrontmatter(content: string): { frontmatter: string | null; body: string } {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(content);
  return m ? { frontmatter: m[1], body: content.slice(m[0].length) } : { frontmatter: null, body: content };
}
