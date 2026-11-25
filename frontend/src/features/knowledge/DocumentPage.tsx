import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, Link2, Pencil, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { backlinksQuery, deleteDoc, treeQuery } from "./api";
import { ConflictBanner, docBtn, EditButtons } from "./DocEditor";
import { MirrorNotice } from "../control/MirrorBadge";
import { MirrorBar } from "../mirrors/MirrorBar";
import { mirrorOf } from "../mirrors/mirror";
import { assetUrl, flattenFiles, isImage, isMarkdown, splitFrontmatter } from "./links";
import { MarkdownEditor } from "./MarkdownEditor";
import { MarkdownView } from "./MarkdownView";
import { useDocEditor } from "./useDocEditor";

export function DocumentPage({ path, edit }: { path: string; edit: boolean }) {
  if (!isMarkdown(path)) return <AssetPage path={path} />;
  return <MarkdownPage key={path} path={path} startInEdit={edit} />;
}

function Header({ path, children }: { path: string; children?: React.ReactNode }) {
  const parts = path.split("/");
  return (
    <header className="sticky top-0 z-10 flex items-center gap-2 border-b border-slate-200 bg-white/90 px-4 py-2 backdrop-blur dark:border-slate-800 dark:bg-slate-950/90">
      <Link to="/knowledge" className="-ml-1 rounded-md p-1.5 text-slate-500 md:hidden" aria-label="Back to the tree">
        <ArrowLeft className="size-5" />
      </Link>
      <div className="min-w-0 flex-1 truncate text-sm text-slate-500">
        {parts.slice(0, -1).join(" / ")}
        {parts.length > 1 && " / "}
        <span className="font-medium text-slate-900 dark:text-slate-100">{parts.at(-1)}</span>
      </div>
      {children}
    </header>
  );
}

function AssetPage({ path }: { path: string }) {
  return (
    <div>
      <Header path={path} />
      <div className="p-4">
        {isImage(path) ? (
          <img src={assetUrl(path)} alt={path} className="max-w-full rounded-lg" />
        ) : (
          <a className="text-sky-600 underline" href={assetUrl(path)} target="_blank" rel="noreferrer">
            Open file
          </a>
        )}
      </div>
    </div>
  );
}

const btn = docBtn;

function MarkdownPage({ path, startInEdit }: { path: string; startInEdit: boolean }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const editor = useDocEditor(path);
  const doc = editor.doc;
  const tree = useQuery(treeQuery);
  const backlinks = useQuery(backlinksQuery(path));
  const files = useMemo(() => (tree.data ? flattenFiles(tree.data) : []), [tree.data]);
  // Mirrors (OneDrive presentations, Moodle) are read-only and get their own header card.
  const parts = useMemo(() => splitFrontmatter(doc.data?.content ?? ""), [doc.data?.content]);
  const mirror = useMemo(() => mirrorOf(parts.frontmatter), [parts.frontmatter]);

  const [editing, setEditing] = useState(startInEdit);

  const remove = async () => {
    if (!doc.data || !window.confirm(`Delete "${path}"? (Recoverable from the vault's git history.)`)) return;
    await deleteDoc(path, doc.data.revision);
    await queryClient.invalidateQueries({ queryKey: ["knowledge"] });
    navigate({ to: "/knowledge" });
  };

  if (doc.isError)
    return (
      <div>
        <Header path={path} />
        <p className="p-6 text-slate-500">This note does not exist (anymore).</p>
      </div>
    );
  if (!doc.data) return <p className="p-6 text-slate-400">Loading…</p>;

  const content = editor.content;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <Header path={path}>
        {editing ? (
          <EditButtons editor={editor} onView={() => setEditing(false)} />
        ) : (
          <>
            {!mirror && (
              <button onClick={() => setEditing(true)} className={`${btn} hover:bg-slate-100 dark:hover:bg-slate-800`}>
                <Pencil className="size-4" /> Edit
              </button>
            )}
            <button onClick={remove} className={`${btn} text-slate-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950`} aria-label="Delete note">
              <Trash2 className="size-4" />
            </button>
          </>
        )}
      </Header>

      <ConflictBanner editor={editor} />

      <div className="min-h-0 flex-1 overflow-y-auto">
        {editing ? (
          <div className="h-full p-3">
            <MarkdownEditor value={content} onChange={editor.setDraft} />
          </div>
        ) : (
          <div className="mx-auto max-w-3xl px-4 py-6 sm:px-8">
            <MirrorNotice path={path} />
            {mirror && <MirrorBar meta={mirror} body={parts.body} />}
            <MarkdownView content={content} path={path} files={files} hideFrontmatter={mirror !== null} />
            {backlinks.data && backlinks.data.length > 0 && (
              <section className="mt-12 border-t border-slate-200 pt-4 dark:border-slate-800">
                <h2 className="mb-2 flex items-center gap-1.5 text-xs font-semibold tracking-wide text-slate-500 uppercase">
                  <Link2 className="size-3.5" /> Linked from
                </h2>
                <ul className="space-y-1 text-sm">
                  {backlinks.data.map((b) => (
                    <li key={b.path}>
                      <Link to="/knowledge/$" params={{ _splat: b.path }} className="text-sky-600 hover:underline dark:text-sky-400">
                        {b.title}
                      </Link>
                      <span className="ml-2 text-xs text-slate-400">{b.path}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
