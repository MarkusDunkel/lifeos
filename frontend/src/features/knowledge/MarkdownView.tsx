import { Link } from "@tanstack/react-router";
import type { Root } from "mdast";
import { findAndReplace } from "mdast-util-find-and-replace";
import { useMemo } from "react";
import Markdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  assetUrl,
  bodyLineOffset,
  isExternal,
  resolveRelative,
  resolveWikilink,
  splitFrontmatter,
} from "./links";

const WIKI = "wikilink:";

/** Turns ``[[Target|Alias]]`` / ``![[image.png]]`` into link/image nodes. */
function remarkWikilinks() {
  return (tree: Root) => {
    findAndReplace(tree, [
      [
        /(!?)\[\[([^\]]+)\]\]/g,
        (_: string, bang: string, inner: string) => {
          const [target, alias] = inner.split("|");
          if (bang) return { type: "image", url: WIKI + target, alt: alias ?? target };
          const label = alias ?? target.split(/[#^]/)[0];
          return { type: "link", url: WIKI + target, children: [{ type: "text", value: label }] };
        },
      ],
    ]);
  };
}

const urlTransform = (url: string) => (url.startsWith(WIKI) ? url : defaultUrlTransform(url));

type HastNode = {
  type: string;
  properties?: Record<string, unknown>;
  position?: { start: { line: number }; end: { line: number } };
  children?: HastNode[];
};

/** Adds ``data-ls``/``data-le`` (one-based source lines of the whole file) to every element. */
function rehypeSourceLines(offset: number) {
  return () => (tree: HastNode) => {
    const visit = (node: HastNode) => {
      if (node.type === "element" && node.position) {
        node.properties = {
          ...node.properties,
          dataLs: node.position.start.line + offset,
          dataLe: node.position.end.line + offset,
        };
      }
      node.children?.forEach(visit);
    };
    visit(tree);
  };
}

type Props = {
  content: string;
  path: string;
  files: string[];
  /** Annotate elements with their source lines (used for feedback targeting). */
  sourceLines?: boolean;
  /** Smaller type for side panels. */
  compact?: boolean;
  /** Do not show the raw frontmatter block (when the page renders it itself). */
  hideFrontmatter?: boolean;
};

export function MarkdownView({
  content,
  path,
  files,
  sourceLines = false,
  compact = false,
  hideFrontmatter = false,
}: Props) {
  const { frontmatter, body } = useMemo(() => splitFrontmatter(content), [content]);
  const rehypePlugins = useMemo(
    () => (sourceLines ? [rehypeSourceLines(bodyLineOffset(content, body))] : []),
    [sourceLines, content, body],
  );

  const resolve = (href: string): { internal: string | null; missing?: boolean } => {
    if (href.startsWith(WIKI)) {
      const target = resolveWikilink(href.slice(WIKI.length), files, path);
      return target ? { internal: target } : { internal: null, missing: true };
    }
    if (isExternal(href) || href.startsWith("#")) return { internal: null };
    return { internal: resolveRelative(href, path) };
  };

  return (
    <article className={`prose prose-slate ${compact ? "prose-sm" : ""} max-w-none dark:prose-invert prose-headings:scroll-mt-4 prose-a:text-sky-600 dark:prose-a:text-sky-400 prose-img:rounded-lg`}>
      {frontmatter && !hideFrontmatter && (
        <pre className="not-prose mb-6 overflow-x-auto rounded-lg bg-slate-100 p-3 text-xs text-slate-600 dark:bg-slate-900 dark:text-slate-400">
          {frontmatter}
        </pre>
      )}
      <Markdown
        remarkPlugins={[remarkGfm, remarkWikilinks]}
        rehypePlugins={rehypePlugins}
        urlTransform={urlTransform}
        components={{
          a: ({ href = "", children }) => {
            const { internal, missing } = resolve(href);
            if (missing)
              return (
                <span className="text-slate-400 underline decoration-dotted" title="Note not found">
                  {children}
                </span>
              );
            if (internal !== null)
              return (
                <Link to="/knowledge/$" params={{ _splat: internal }}>
                  {children}
                </Link>
              );
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
          img: ({ src = "", alt }) => {
            const s = typeof src === "string" ? src : "";
            const { internal } = resolve(s);
            return <img src={internal !== null ? assetUrl(internal) : s} alt={alt ?? ""} loading="lazy" />;
          },
        }}
      >
        {body}
      </Markdown>
    </article>
  );
}
