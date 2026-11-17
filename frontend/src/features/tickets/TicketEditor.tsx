import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ExternalLink, Eye, Pencil, Trash2, TriangleAlert, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../../lib/api";
import { MarkdownView } from "../knowledge/MarkdownView";
import {
  deleteTicket,
  type EditableFields,
  type Meta,
  type ProjectValues,
  resolveConflict,
  revertTicket,
  saveTicket,
  type TicketDoc,
  ticketQuery,
} from "./api";
import { changeName, ticketLabel, toggle } from "./list";

const btn =
  "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-40";
const input =
  "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-sky-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900";
const chip = (on: boolean) =>
  `rounded-full px-2.5 py-0.5 text-xs ring-1 ${
    on
      ? "bg-sky-600 text-white ring-sky-600"
      : "text-slate-600 ring-slate-200 hover:bg-slate-100 dark:text-slate-300 dark:ring-slate-700 dark:hover:bg-slate-800"
  }`;

type Draft = Required<Pick<EditableFields, "title" | "state" | "labels" | "assignees" | "body">> & {
  projects: ProjectValues;
};

const draftOf = (d: TicketDoc): Draft => ({
  title: d.title,
  state: d.state,
  labels: d.labels,
  assignees: d.assignees,
  body: d.body,
  projects: Object.fromEntries(Object.entries(d.projects).map(([k, v]) => [k, { ...v }])),
});

const same = (a: Draft, b: Draft) => JSON.stringify(a) === JSON.stringify(b);

export function TicketEditor({ path, meta, onClose }: { path: string; meta: Meta; onClose: () => void }) {
  const queryClient = useQueryClient();
  const doc = useQuery(ticketQuery(path));
  const [draft, setDraft] = useState<Draft | null>(null);
  const [preview, setPreview] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const base = doc.data ? draftOf(doc.data) : null;
  const current = draft ?? base;
  const dirty = Boolean(draft && base && !same(draft, base));

  const refresh = async (fresh?: TicketDoc) => {
    if (fresh) queryClient.setQueryData(ticketQuery(path).queryKey, fresh);
    await queryClient.invalidateQueries({ queryKey: ["tickets", "list"] });
  };

  const save = useCallback(async () => {
    if (!doc.data || !draft || !dirty || busy) return;
    setBusy(true);
    setError(null);
    try {
      const fresh = await saveTicket(path, draft, doc.data.revision);
      setDraft(null);
      await refresh(fresh);
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? "The file changed in the meantime (sync, editor, agent). Reload to see the new version."
          : String(e instanceof Error ? e.message : e),
      );
    } finally {
      setBusy(false);
    }
  }, [doc.data, draft, dirty, busy, path]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  const close = () => {
    if (dirty && !window.confirm("Discard unsaved changes?")) return;
    onClose();
  };

  const resolve = async (field: string, choice: "local" | "remote") => {
    if (!doc.data) return;
    if (dirty) return setError("Save or discard your edits before resolving a conflict.");
    setError(null);
    try {
      await refresh(await resolveConflict(path, field, choice, doc.data.revision));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const revert = async () => {
    if (!doc.data) return;
    if (!window.confirm("Discard all local changes to this ticket and use the GitHub version?")) return;
    setError(null);
    setDraft(null);
    try {
      await refresh(await revertTicket(path, doc.data.revision));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const remove = async () => {
    if (!doc.data || !window.confirm("Delete this unsent ticket?")) return;
    await deleteTicket(path, doc.data.revision);
    await refresh();
    onClose();
  };

  if (doc.isError) return <p className="p-6 text-slate-500">This ticket does not exist (anymore).</p>;
  if (!doc.data || !current) return <p className="p-6 text-slate-400">Loading…</p>;
  const d = doc.data;
  const set = (patch: Partial<Draft>) => setDraft({ ...current, ...patch });
  const labels = [...new Set([...(meta.labels[d.repo] ?? []), ...current.labels])].sort();
  const people = [...new Set([...(meta.assignees[d.repo] ?? []), ...current.assignees])].sort();

  return (
    <div className="flex h-full min-h-0 flex-col bg-white dark:bg-slate-950">
      <header className="flex items-center gap-2 border-b border-slate-200 px-4 py-2 dark:border-slate-800">
        <span className="flex-1 truncate text-sm text-slate-500">
          {d.repo} <span className="font-medium text-slate-900 dark:text-slate-100">{ticketLabel(d)}</span>
        </span>
        {d.url && (
          <a
            href={d.url}
            target="_blank"
            rel="noopener noreferrer"
            className={`${btn} border border-slate-300 text-slate-700 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800`}
            title="Open this issue on GitHub"
          >
            <ExternalLink className="size-4" /> GitHub
          </a>
        )}
        <button onClick={() => void save()} disabled={!dirty || busy} className={`${btn} bg-sky-600 text-white hover:bg-sky-700`}>
          <Check className="size-4" /> Save
        </button>
        {d.number === null && (
          <button onClick={remove} className={`${btn} px-2 text-slate-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950`} aria-label="Delete">
            <Trash2 className="size-4" />
          </button>
        )}
        <button onClick={close} className={`${btn} px-2 hover:bg-slate-100 dark:hover:bg-slate-800`} aria-label="Close">
          <X className="size-4" />
        </button>
      </header>

      {error && (
        <div className="flex items-center gap-2 border-b border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          <span className="flex-1 whitespace-pre-line">{error}</span>
          <button
            onClick={async () => {
              setError(null);
              setDraft(null);
              await doc.refetch();
            }}
            className="underline"
          >
            Reload
          </button>
        </div>
      )}
      {d.conflicts.length > 0 && (
        <div className="space-y-2 border-b border-amber-300 bg-amber-50 px-4 py-3 text-sm dark:border-amber-800 dark:bg-amber-950">
          <p className="flex items-center gap-1.5 font-medium">
            <TriangleAlert className="size-4" /> Changed here and on GitHub. Decide per field:
            <span className="flex-1" />
            {!dirty && (
              <button onClick={revert} className="text-xs font-normal underline hover:no-underline">
                Revert everything to GitHub
              </button>
            )}
          </p>
          {d.conflicts.map((field) => (
            <Conflict key={field} field={field} doc={d} onResolve={resolve} />
          ))}
        </div>
      )}
      {d.changes.length > 0 && d.conflicts.length === 0 && (
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-sky-50 px-4 py-1.5 text-xs text-sky-800 dark:border-slate-800 dark:bg-sky-950 dark:text-sky-200">
          <span className="flex-1">
            Not on GitHub yet: {d.changes.map(changeName).join(", ")}. Write back via “Changes”.
          </span>
          {d.number !== null && !dirty && (
            <button onClick={revert} className="underline hover:no-underline">
              Revert to GitHub
            </button>
          )}
        </div>
      )}

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        <input value={current.title} onChange={(e) => set({ title: e.target.value })} className={`${input} text-base font-medium`} aria-label="Title" />

        <div className="grid grid-cols-2 gap-3">
          <label className="text-xs font-medium text-slate-500">
            State
            <select value={current.state} onChange={(e) => set({ state: e.target.value as Draft["state"] })} className={`${input} mt-1`}>
              <option value="open">open</option>
              <option value="closed">closed</option>
            </select>
          </label>
        </div>

        {Object.keys(current.projects)
          .sort()
          .map((project) => {
            const fields = meta.projects[project]?.fields ?? {};
            const values = current.projects[project];
            const setValue = (name: string, value: string) =>
              set({ projects: { ...current.projects, [project]: { ...values, [name]: value || null } } });
            return (
              <fieldset key={project} className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
                <legend className="px-1 text-xs font-semibold text-slate-600 dark:text-slate-300">
                  {project}
                  {!meta.projects[project] && <span className="ml-1 font-normal text-amber-600">(not configured)</span>}
                </legend>
                <div className="grid grid-cols-2 gap-3">
                  {Object.entries(fields).map(([name, f]) => (
                    <label key={name} className="text-xs font-medium text-slate-500">
                      {name}
                      {f.type === "SINGLE_SELECT" ? (
                        <select value={values[name] ?? ""} onChange={(e) => setValue(name, e.target.value)} className={`${input} mt-1`}>
                          <option value="">(none)</option>
                          {f.options.map((o) => (
                            <option key={o}>{o}</option>
                          ))}
                        </select>
                      ) : (
                        <input
                          type={f.type === "DATE" ? "date" : f.type === "NUMBER" ? "number" : "text"}
                          value={values[name] ?? ""}
                          onChange={(e) => setValue(name, e.target.value)}
                          className={`${input} mt-1`}
                        />
                      )}
                    </label>
                  ))}
                </div>
              </fieldset>
            );
          })}
        {Object.keys(meta.projects).some((p) => !(p in current.projects)) && (
          <label className="block text-xs font-medium text-slate-500">
            Add to project
            <select
              value=""
              onChange={(e) => e.target.value && set({ projects: { ...current.projects, [e.target.value]: {} } })}
              className={`${input} mt-1`}
            >
              <option value="">Choose a project…</option>
              {Object.keys(meta.projects)
                .filter((p) => !(p in current.projects))
                .map((p) => (
                  <option key={p}>{p}</option>
                ))}
            </select>
          </label>
        )}

        {labels.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-medium text-slate-500">Labels</p>
            <div className="flex flex-wrap gap-1.5">
              {labels.map((l) => (
                <button key={l} onClick={() => set({ labels: toggle(current.labels, l) })} className={chip(current.labels.includes(l))}>
                  {l}
                </button>
              ))}
            </div>
          </div>
        )}
        {people.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-medium text-slate-500">Assignees</p>
            <div className="flex flex-wrap gap-1.5">
              {people.map((p) => (
                <button key={p} onClick={() => set({ assignees: toggle(current.assignees, p) })} className={chip(current.assignees.includes(p))}>
                  {p}
                </button>
              ))}
            </div>
          </div>
        )}

        <div>
          <div className="mb-1 flex items-center gap-2">
            <p className="flex-1 text-xs font-medium text-slate-500">Description</p>
            <button onClick={() => setPreview((v) => !v)} className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-800">
              {preview ? <Pencil className="size-3.5" /> : <Eye className="size-3.5" />} {preview ? "Edit" : "Preview"}
            </button>
          </div>
          {preview ? (
            <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
              <MarkdownView content={current.body} path="" files={[]} compact />
            </div>
          ) : (
            <textarea
              value={current.body}
              onChange={(e) => set({ body: e.target.value })}
              rows={Math.min(30, Math.max(10, current.body.split("\n").length + 2))}
              className={`${input} font-mono leading-relaxed`}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function Conflict({
  field,
  doc,
  onResolve,
}: {
  field: string;
  doc: TicketDoc;
  onResolve: (field: string, choice: "local" | "remote") => void;
}) {
  const remote = (() => {
    if (!doc.base) return "(unknown)";
    if (field.startsWith("project:")) {
      const rest = field.slice("project:".length);
      const at = rest.indexOf(":");
      if (at < 0) return rest in doc.base.projects ? "on the project" : "not on the project";
      return doc.base.projects[rest.slice(0, at)]?.[rest.slice(at + 1)] ?? "(none)";
    }
    const v = (doc.base as Record<string, unknown>)[field];
    return Array.isArray(v) ? v.join(", ") || "(none)" : String(v ?? "");
  })();
  const isBody = field === "body";
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg bg-white/70 p-2 dark:bg-slate-900/60">
      <span className="font-medium">{changeName(field)}</span>
      <span className="min-w-0 flex-1 truncate text-xs text-slate-500">
        {isBody ? "Both sides edited the text; it now contains <<<<<<< markers." : `GitHub: ${remote}`}
      </span>
      <button className={`${btn} bg-white px-2 py-1 text-xs ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700`} onClick={() => onResolve(field, "remote")}>
        Use GitHub
      </button>
      <button className={`${btn} bg-amber-600 px-2 py-1 text-xs text-white`} onClick={() => onResolve(field, "local")}>
        {isBody ? "Markers removed" : "Keep mine"}
      </button>
    </div>
  );
}
