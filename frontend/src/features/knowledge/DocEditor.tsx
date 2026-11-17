import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Check, ExternalLink, Eye, Pencil } from "lucide-react";
import { useMemo, useState, type ReactNode, type RefObject } from "react";
import { treeQuery } from "./api";
import { flattenFiles } from "./links";
import { MarkdownEditor } from "./MarkdownEditor";
import { MarkdownView } from "./MarkdownView";
import { useDocEditor, type DocEditorState } from "./useDocEditor";

export const docBtn =
  "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-40";

/** Shown when a save failed because the file changed elsewhere since it was loaded. */
export function ConflictBanner({ editor, what = "note" }: { editor: DocEditorState; what?: string }) {
  if (editor.conflict === null) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-amber-300 bg-amber-50 px-4 py-2 text-sm dark:border-amber-800 dark:bg-amber-950">
      <span className="flex-1">This {what} was changed elsewhere (Obsidian, an agent, …) since you opened it.</span>
      <button className={`${docBtn} bg-white dark:bg-slate-900`} onClick={editor.loadTheirs}>
        Load theirs (discard mine)
      </button>
      <button className={`${docBtn} bg-amber-600 text-white`} onClick={editor.overwrite}>
        Overwrite with mine
      </button>
    </div>
  );
}

/** Save state text and the Save / View buttons of an editor in edit mode. */
export function EditButtons({ editor, onView }: { editor: DocEditorState; onView: () => void }) {
  return (
    <>
      <span className="hidden text-xs text-slate-400 sm:inline">
        {editor.saving ? "Saving…" : editor.saveError ? "Save failed" : editor.dirty ? "Unsaved changes" : "Saved"}
      </span>
      <button
        onClick={editor.doSave}
        disabled={!editor.dirty || editor.saving}
        className={`${docBtn} bg-sky-600 text-white hover:bg-sky-700`}
      >
        <Check className="size-4" /> Save
      </button>
      <button
        onClick={() => {
          if (editor.dirty && !window.confirm("Discard unsaved changes?")) return;
          editor.discard();
          onView();
        }}
        className={`${docBtn} hover:bg-slate-100 dark:hover:bg-slate-800`}
      >
        <Eye className="size-4" /> View
      </button>
    </>
  );
}

type Props = {
  path: string;
  title?: ReactNode;
  startInEdit?: boolean;
  onSaved?: () => Promise<unknown> | void;
  /** Extra controls in the toolbar (view mode). */
  actions?: ReactNode;
  /** Receives the rendered article (for a table of contents that scrolls to headings). */
  viewRef?: RefObject<HTMLDivElement | null>;
};

/** A knowledge file embedded in another page: rendered, with Edit, Save and conflict handling. */
export function DocEditor({ path, title, startInEdit = false, onSaved, actions, viewRef }: Props) {
  const [editing, setEditing] = useState(startInEdit);
  const editor = useDocEditor(path, { shortcuts: editing, onSaved });
  const tree = useQuery(treeQuery);
  const files = useMemo(() => (tree.data ? flattenFiles(tree.data) : []), [tree.data]);

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 dark:border-slate-800">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-800 dark:bg-slate-900">
        <div className="min-w-0 flex-1 truncate text-sm font-medium">{title ?? path.split("/").at(-1)}</div>
        {editing ? (
          <EditButtons editor={editor} onView={() => setEditing(false)} />
        ) : (
          <>
            {actions}
            <Link
              to="/knowledge/$"
              params={{ _splat: path }}
              className={`${docBtn} text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800`}
              title="Open in Knowledge"
            >
              <ExternalLink className="size-4" /> <span className="hidden sm:inline">Knowledge</span>
            </Link>
            <button onClick={() => setEditing(true)} className={`${docBtn} hover:bg-slate-100 dark:hover:bg-slate-800`}>
              <Pencil className="size-4" /> Edit
            </button>
          </>
        )}
      </div>
      <ConflictBanner editor={editor} what="file" />
      {editor.doc.isError ? (
        <p className="p-4 text-sm text-slate-500">This file does not exist (anymore).</p>
      ) : !editor.doc.data ? (
        <p className="p-4 text-sm text-slate-400">Loading…</p>
      ) : editing ? (
        <div className="h-[70vh] p-2">
          <MarkdownEditor value={editor.content} onChange={editor.setDraft} />
        </div>
      ) : (
        <div ref={viewRef} className="px-4 py-5 sm:px-6">
          <MarkdownView content={editor.content} path={path} files={files} hideFrontmatter />
        </div>
      )}
    </div>
  );
}
