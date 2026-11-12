import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { ChevronRight, File, FilePlus, Folder, Image, Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../../lib/api";
import { saveDoc, searchQuery, treeQuery } from "./api";
import { isImage, isMarkdown, type TreeNode } from "./links";

const EXPANDED_KEY = "lifeos.knowledge.expanded";

function loadExpanded(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(EXPANDED_KEY) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

function useDebounced<T>(value: T, ms: number) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function KnowledgeSidebar({ onNavigate }: { onNavigate?: () => void }) {
  const tree = useQuery(treeQuery);
  const params = useParams({ strict: false }) as { _splat?: string };
  const current = params._splat ?? "";
  const [expanded, setExpanded] = useState(loadExpanded);
  const [q, setQ] = useState("");
  const debounced = useDebounced(q, 250);
  const results = useQuery(searchQuery(debounced));
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // Make sure the folders of the open document are expanded.
  useEffect(() => {
    if (!current) return;
    const parts = current.split("/").slice(0, -1);
    setExpanded((prev) => {
      const next = new Set(prev);
      parts.forEach((_, i) => next.add(parts.slice(0, i + 1).join("/")));
      return next;
    });
  }, [current]);

  useEffect(() => {
    try {
      localStorage.setItem(EXPANDED_KEY, JSON.stringify([...expanded]));
    } catch {
      /* storage unavailable */
    }
  }, [expanded]);

  const toggle = (path: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  const createNote = async () => {
    const folder = current.includes("/") ? current.slice(0, current.lastIndexOf("/") + 1) : "";
    const name = window.prompt("New note (path inside the knowledge hub):", `${folder}Untitled.md`);
    if (!name) return;
    const path = name.endsWith(".md") ? name : `${name}.md`;
    const title = path.slice(path.lastIndexOf("/") + 1, -3);
    try {
      await saveDoc(path, `# ${title}\n\n`, null);
    } catch (e) {
      window.alert(e instanceof ApiError && e.status === 409 ? "That note already exists." : String(e));
      return;
    }
    await queryClient.invalidateQueries({ queryKey: ["knowledge", "tree"] });
    navigate({ to: "/knowledge/$", params: { _splat: path }, search: { edit: true } });
    onNavigate?.();
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 p-3">
        <label className="relative flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-slate-400" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search notes…"
            className="w-full rounded-lg border border-slate-200 bg-white py-2 pr-8 pl-8 text-sm outline-none focus:border-sky-500 dark:border-slate-800 dark:bg-slate-900"
          />
          {q && (
            <button
              onClick={() => setQ("")}
              className="absolute top-1/2 right-2 -translate-y-1/2 text-slate-400"
              aria-label="Clear search"
            >
              <X className="size-4" />
            </button>
          )}
        </label>
        <button
          onClick={createNote}
          className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
          title="New note"
          aria-label="New note"
        >
          <FilePlus className="size-5" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4 text-sm">
        {debounced ? (
          <SearchResults hits={results.data} loading={results.isFetching} onNavigate={onNavigate} />
        ) : tree.data ? (
          <TreeChildren
            node={tree.data}
            depth={0}
            expanded={expanded}
            toggle={toggle}
            current={current}
            onNavigate={onNavigate}
          />
        ) : (
          <p className="p-3 text-slate-400">{tree.isError ? "Could not load the knowledge hub." : "Loading…"}</p>
        )}
      </div>
    </div>
  );
}

function SearchResults({
  hits,
  loading,
  onNavigate,
}: {
  hits?: { path: string; title: string; snippet: string }[];
  loading: boolean;
  onNavigate?: () => void;
}) {
  if (!hits) return <p className="p-3 text-slate-400">{loading ? "Searching…" : ""}</p>;
  if (hits.length === 0) return <p className="p-3 text-slate-400">No matches.</p>;
  return (
    <ul className="space-y-1">
      {hits.map((h) => (
        <li key={h.path}>
          <Link
            to="/knowledge/$"
            params={{ _splat: h.path }}
            onClick={onNavigate}
            className="block rounded-lg px-3 py-2 hover:bg-slate-100 dark:hover:bg-slate-800"
          >
            <div className="font-medium">{h.title}</div>
            <div className="truncate text-xs text-slate-400">{h.path}</div>
            <div className="mt-1 line-clamp-2 text-xs text-slate-500 dark:text-slate-400">
              <Snippet text={h.snippet} />
            </div>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function Snippet({ text }: { text: string }) {
  const parts = useMemo(() => text.split(/\[\[\[|\]\]\]/), [text]);
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="rounded bg-amber-200 px-0.5 dark:bg-amber-500/40 dark:text-inherit">
            {p}
          </mark>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}

type TreeProps = {
  node: TreeNode;
  depth: number;
  expanded: Set<string>;
  toggle: (p: string) => void;
  current: string;
  onNavigate?: () => void;
};

function TreeChildren({ node, depth, ...rest }: TreeProps) {
  return (
    <ul>
      {node.children.map((child) => (
        <TreeItem key={child.path} node={child} depth={depth} {...rest} />
      ))}
    </ul>
  );
}

function TreeItem(props: TreeProps) {
  const { node, depth, expanded, toggle, current, onNavigate } = props;
  const pad = { paddingLeft: `${depth * 14 + 8}px` };
  const row = "flex w-full items-center gap-1.5 rounded-md py-1.5 pr-2 text-left";

  if (node.kind === "dir") {
    const open = expanded.has(node.path);
    return (
      <li>
        <button onClick={() => toggle(node.path)} className={`${row} hover:bg-slate-100 dark:hover:bg-slate-800`} style={pad}>
          <ChevronRight className={`size-3.5 shrink-0 text-slate-400 transition-transform ${open ? "rotate-90" : ""}`} />
          <Folder className="size-4 shrink-0 text-sky-500" />
          <span className="truncate">{node.name}</span>
        </button>
        {open && <TreeChildren {...props} depth={depth + 1} />}
      </li>
    );
  }

  const active = node.path === current;
  const label = isMarkdown(node.path) ? node.name.replace(/\.md$/i, "") : node.name;
  const Icon = isImage(node.path) ? Image : File;
  return (
    <li>
      <Link
        to="/knowledge/$"
        params={{ _splat: node.path }}
        onClick={onNavigate}
        className={`${row} ${
          active
            ? "bg-sky-100 text-sky-900 dark:bg-sky-900/40 dark:text-sky-100"
            : "hover:bg-slate-100 dark:hover:bg-slate-800"
        } ${isMarkdown(node.path) ? "" : "text-slate-400"}`}
        style={{ paddingLeft: `${depth * 14 + 26}px` }}
      >
        <Icon className="size-4 shrink-0 opacity-60" />
        <span className="truncate">{label}</span>
      </Link>
    </li>
  );
}
