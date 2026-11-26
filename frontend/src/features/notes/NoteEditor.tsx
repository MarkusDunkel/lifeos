import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useId, useState } from "react";
import { ApiError } from "../../lib/api";
import { createNote, deleteNote, notesQuery, updateNote, type NotesData } from "./api";
import { cellText, type Cells, type Field, formatCell, type Note, sameCells } from "./grid";
import { buttonBase, buttonClass, inputClass } from "../../components/ui";

const btn =
  buttonBase;
const input =
  `${inputClass} w-full`;

type Props = {
  /** The note being edited, or `null` for a new one. */
  note: Note | null;
  initial: Cells;
  fields: Field[];
  allNotes: Note[];
  onClose: () => void;
  onCreated: (id: string) => void;
};

export function NoteEditor({ note, initial, fields, allNotes, onClose, onCreated }: Props) {
  const queryClient = useQueryClient();
  const [base, setBase] = useState<Note | null>(note);
  const [draft, setDraft] = useState<Cells>(initial);
  const [conflict, setConflict] = useState<string | null>(null);
  const isNew = base === null;
  const dirty = isNew ? Object.values(draft).some((v) => cellText(v) !== "") : !sameCells(draft, base.cells);

  // Pick up changes made elsewhere (agents, other devices) while nothing is edited here.
  useEffect(() => {
    if (note && base && note.revision !== base.revision && !dirty) {
      setBase(note);
      setDraft(note.cells);
    }
  }, [note, base, dirty]);

  const putInCache = (saved: Note) =>
    queryClient.setQueryData<NotesData>(notesQuery.queryKey, (old) =>
      old
        ? {
            ...old,
            notes: old.notes.some((n) => n.id === saved.id)
              ? old.notes.map((n) => (n.id === saved.id ? saved : n))
              : [...old.notes, saved],
          }
        : old,
    );

  const save = useMutation({
    mutationFn: (revision: string | null) =>
      revision === null || base === null ? createNote(draft) : updateNote(base.id, draft, revision),
    onSuccess: (saved) => {
      putInCache(saved);
      setBase(saved);
      setDraft(saved.cells);
      setConflict(null);
      if (isNew) onCreated(saved.id);
      void queryClient.invalidateQueries({ queryKey: notesQuery.queryKey });
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409) {
        setConflict((e.body as { currentRevision: string | null }).currentRevision ?? "");
      }
    },
  });

  const doSave = useCallback(() => {
    if (dirty && !save.isPending) save.mutate(base?.revision ?? null);
  }, [dirty, save, base]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        doSave();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [doSave]);

  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  const close = () => {
    if (dirty && !window.confirm("Discard unsaved changes?")) return;
    onClose();
  };

  const remove = async () => {
    if (!base || !window.confirm("Delete this note? (Recoverable from the vault's git history.)")) return;
    try {
      await deleteNote(base.id, base.revision);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setConflict((e.body as { currentRevision: string | null }).currentRevision ?? "");
        return;
      }
      throw e;
    }
    await queryClient.invalidateQueries({ queryKey: notesQuery.queryKey });
    onClose();
  };

  const loadTheirs = async () => {
    const fresh = await queryClient.fetchQuery({ ...notesQuery, staleTime: 0 });
    const theirs = fresh.notes.find((n) => n.id === base?.id);
    setConflict(null);
    if (!theirs) {
      window.alert("This note was deleted elsewhere.");
      onClose();
      return;
    }
    setBase(theirs);
    setDraft(theirs.cells);
  };

  const set = (key: string, value: string) => setDraft((d) => ({ ...d, [key]: value === "" ? null : value }));
  const editable = fields.filter((f) => !f.hidden && !f.readonly);
  const system = fields.filter((f) => f.readonly && base && cellText(base.cells[f.key]));

  return (
    <div className="flex h-full min-h-0 flex-col bg-white dark:bg-slate-950">
      <header className="flex items-center gap-2 border-b border-slate-200 px-4 py-2 dark:border-slate-800">
        <h2 className="flex-1 text-sm font-semibold">{isNew ? "New note" : "Edit note"}</h2>
        <span className="hidden text-xs text-slate-400 sm:inline">
          {save.isPending ? "Saving…" : dirty ? "Unsaved changes" : isNew ? "" : "Saved"}
        </span>
        <button onClick={doSave} disabled={!dirty || save.isPending} className={`${buttonClass("primary")}`}>
          <Check className="size-4" /> Save
        </button>
        {!isNew && (
          <button onClick={remove} className={`${btn} px-2 text-slate-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950`} aria-label="Delete note">
            <Trash2 className="size-4" />
          </button>
        )}
        <button onClick={close} className={`${btn} px-2 hover:bg-slate-100 dark:hover:bg-slate-800`} aria-label="Close">
          <X className="size-4" />
        </button>
      </header>

      {conflict !== null && (
        <div className="flex flex-wrap items-center gap-2 border-b border-amber-300 bg-amber-50 px-4 py-2 text-sm dark:border-amber-800 dark:bg-amber-950">
          <span className="flex-1">This note was changed elsewhere since you opened it.</span>
          <button className={`${btn} bg-white dark:bg-slate-900`} onClick={loadTheirs}>
            Load theirs (discard mine)
          </button>
          {conflict && (
            <button className={`${btn} bg-amber-600 text-white`} onClick={() => save.mutate(conflict)}>
              Overwrite with mine
            </button>
          )}
        </div>
      )}
      {save.isError && !(save.error instanceof ApiError && save.error.status === 409) && (
        <p className="border-b border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          Could not save: {save.error.message}
        </p>
      )}

      <form
        className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4"
        onSubmit={(e) => {
          e.preventDefault();
          doSave();
        }}
      >
        {editable.map((f) => (
          <FieldInput key={f.key} field={f} value={cellText(draft[f.key])} allNotes={allNotes} onChange={(v) => set(f.key, v)} />
        ))}
        {system.length > 0 && (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 border-t border-slate-200 pt-3 text-xs text-slate-400 dark:border-slate-800">
            {system.map((f) => (
              <div key={f.key} className="contents">
                <dt>{f.label}</dt>
                <dd>{formatCell(f, base?.cells[f.key])}</dd>
              </div>
            ))}
          </dl>
        )}
      </form>
    </div>
  );
}

function FieldInput({
  field,
  value,
  allNotes,
  onChange,
}: {
  field: Field;
  value: string;
  allNotes: Note[];
  onChange: (v: string) => void;
}) {
  const id = useId();
  const label = (
    <label htmlFor={id} className="mb-1 block text-xs font-medium text-slate-500">
      {field.label}
    </label>
  );
  if (field.kind === "text") {
    return (
      <div>
        {label}
        <textarea
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            // Notes use tabs for structure; keep Tab inside the text instead of moving focus.
            if (e.key !== "Tab" || e.shiftKey) return;
            e.preventDefault();
            const el = e.currentTarget;
            const { selectionStart: s, selectionEnd: end } = el;
            onChange(`${value.slice(0, s)}\t${value.slice(end)}`);
            requestAnimationFrame(() => el.setSelectionRange(s + 1, s + 1));
          }}
          rows={Math.min(24, Math.max(6, value.split("\n").length + 1))}
          className={`${input} font-mono leading-relaxed whitespace-pre-wrap [tab-size:4]`}
        />
      </div>
    );
  }
  if (field.kind === "select") {
    const options = new Set(field.options);
    for (const n of allNotes) {
      const t = cellText(n.cells[field.key]);
      if (t) options.add(t);
    }
    return (
      <div>
        {label}
        <input id={id} list={`${id}-options`} value={value} onChange={(e) => onChange(e.target.value)} className={input} autoComplete="off" />
        <datalist id={`${id}-options`}>
          {[...options].sort().map((o) => (
            <option key={o} value={o} />
          ))}
        </datalist>
      </div>
    );
  }
  return (
    <div>
      {label}
      <input
        id={id}
        type={field.kind === "date" ? "date" : "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={input}
      />
    </div>
  );
}
