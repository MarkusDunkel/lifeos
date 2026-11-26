import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Plus, Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { notesQuery } from "./api";
import {
  applyView,
  cellText,
  columns,
  EMPTY,
  type Field,
  filterFields,
  filterOptions,
  formatCell,
  newCells,
  type Note,
  preview,
  sortKey,
  type View,
} from "./grid";
import { NoteEditor } from "./NoteEditor";
import { inputClass, Loading } from "../../components/ui";

export type NotesSearch = View & { note?: string };
export const NEW = "new";

type Props = {
  search: NotesSearch;
  /** Update the URL state; `replace` avoids a history entry per keystroke. */
  onChange: (next: NotesSearch, replace?: boolean) => void;
};

const chip = "rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300";

export function NotesPage({ search, onChange }: Props) {
  const { data, isError, error } = useQuery(notesQuery);
  const fields = useMemo(() => data?.fields ?? [], [data]);
  const notes = useMemo(() => data?.notes ?? [], [data]);
  const visible = useMemo(() => applyView(notes, search), [notes, search]);
  const cols = useMemo(() => columns(fields), [fields]);

  const [q, setQ] = useState(search.q ?? "");
  useEffect(() => setQ(search.q ?? ""), [search.q]); // back/forward navigation
  useEffect(() => {
    const t = setTimeout(() => {
      if ((search.q ?? "") !== q) onChange({ ...search, q: q || undefined }, true);
    }, 200);
    return () => clearTimeout(t);
  }, [q, search, onChange]);

  const open = (id: string | undefined) => onChange({ ...search, note: id });
  const setFilter = (key: string, value: string) => {
    const f = { ...search.f };
    if (value) f[key] = value;
    else delete f[key];
    onChange({ ...search, f: Object.keys(f).length ? f : undefined }, true);
  };
  const toggleSort = (key: string) => {
    const current = sortKey(search);
    const desc = current.key === key ? !current.desc : false;
    onChange({ ...search, sort: key, desc: desc || undefined }, true);
  };

  if (isError) return <p className="p-6 text-red-600">Could not load notes: {String(error)}</p>;
  if (!data) return <Loading className="p-6" />;

  const selected = search.note && search.note !== NEW ? notes.find((n) => n.id === search.note) : undefined;
  const editorOpen = search.note === NEW || selected !== undefined;
  const hasFilters = Boolean(search.q || (search.f && Object.keys(search.f).length));

  return (
    <div className="flex h-full min-h-0">
      <div className={`flex min-w-0 flex-1 flex-col ${editorOpen ? "hidden lg:flex" : ""}`}>
        <Toolbar
          q={q}
          setQ={setQ}
          fields={fields}
          notes={notes}
          search={search}
          setFilter={setFilter}
          onSort={(key, desc) => onChange({ ...search, sort: key, desc: desc || undefined }, true)}
          onNew={() => open(NEW)}
          count={visible.length}
          total={notes.length}
          onReset={() => {
            setQ("");
            onChange({ note: search.note }, true);
          }}
          hasFilters={hasFilters}
        />
        <div className="min-h-0 flex-1 overflow-auto">
          {notes.length === 0 ? (
            <p className="p-6 text-slate-500">No notes yet. Create the first one with “New”.</p>
          ) : visible.length === 0 ? (
            <p className="p-6 text-slate-500">No notes match the current filters.</p>
          ) : (
            <>
              <Table notes={visible} fields={fields} cols={cols} search={search} onOpen={open} onSort={toggleSort} />
              <Cards notes={visible} fields={fields} cols={cols} selected={search.note} onOpen={open} />
            </>
          )}
        </div>
      </div>

      {editorOpen && (
        <aside className="w-full min-w-0 border-slate-200 lg:w-[30rem] lg:shrink-0 lg:border-l dark:border-slate-800">
          <NoteEditor
            key={search.note}
            note={selected ?? null}
            initial={selected ? selected.cells : newCells(fields, search.f)}
            fields={fields}
            allNotes={notes}
            onClose={() => open(undefined)}
            onCreated={(id) => onChange({ ...search, note: id }, true)}
          />
        </aside>
      )}
      {search.note && search.note !== NEW && !selected && (
        <aside className="w-full p-6 text-slate-500 lg:w-[30rem] lg:border-l">
          This note does not exist (anymore).{" "}
          <button className="text-accent-600 underline" onClick={() => open(undefined)}>
            Close
          </button>
        </aside>
      )}
    </div>
  );
}

function Toolbar(props: {
  q: string;
  setQ: (q: string) => void;
  fields: Field[];
  notes: Note[];
  search: NotesSearch;
  setFilter: (key: string, value: string) => void;
  onSort: (key: string, desc: boolean) => void;
  onNew: () => void;
  onReset: () => void;
  count: number;
  total: number;
  hasFilters: boolean;
}) {
  const { search, fields } = props;
  const current = sortKey(search);
  const select =
    inputClass;
  return (
    <div className="space-y-2 border-b border-slate-200 p-3 dark:border-slate-800">
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-slate-400" />
          <input
            value={props.q}
            onChange={(e) => props.setQ(e.target.value)}
            placeholder="Search notes"
            className="w-full rounded-lg border border-slate-200 bg-white py-2 pr-3 pl-8 text-sm focus:border-accent-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900"
          />
        </div>
        <button
          onClick={props.onNew}
          className="inline-flex items-center gap-1.5 rounded-lg bg-accent-600 px-3 py-2 text-sm font-medium text-white hover:bg-accent-700"
        >
          <Plus className="size-4" /> New
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {filterFields(fields).map((f) => (
          <select
            key={f.key}
            value={search.f?.[f.key] ?? ""}
            onChange={(e) => props.setFilter(f.key, e.target.value)}
            className={`${select} ${search.f?.[f.key] ? "border-accent-500 text-accent-700 dark:text-accent-300" : ""}`}
            aria-label={`Filter by ${f.label}`}
          >
            <option value="">{f.label}: all</option>
            {filterOptions(f, props.notes).map((o) => (
              <option key={o} value={o}>
                {f.label}: {o}
              </option>
            ))}
            <option value={EMPTY}>{f.label}: (empty)</option>
          </select>
        ))}
        {/* Sorting on phones; the table headers do this on larger screens. */}
        <select
          value={`${current.key}:${current.desc ? "desc" : "asc"}`}
          onChange={(e) => {
            const [key, dir] = e.target.value.split(":");
            props.onSort(key, dir === "desc");
          }}
          className={`${select} md:hidden`}
          aria-label="Sort"
        >
          {fields
            .filter((f) => !f.hidden && f.kind !== "text")
            .flatMap((f) => [
              <option key={`${f.key}:asc`} value={`${f.key}:asc`}>
                Sort: {f.label} ↑
              </option>,
              <option key={`${f.key}:desc`} value={`${f.key}:desc`}>
                Sort: {f.label} ↓
              </option>,
            ])}
        </select>
        <span className="ml-auto text-xs text-slate-400">
          {props.count === props.total ? `${props.total} notes` : `${props.count} of ${props.total}`}
        </span>
        {props.hasFilters && (
          <button onClick={props.onReset} className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-800">
            <X className="size-3.5" /> Reset
          </button>
        )}
      </div>
    </div>
  );
}

function Table(props: {
  notes: Note[];
  fields: Field[];
  cols: Field[];
  search: NotesSearch;
  onOpen: (id: string) => void;
  onSort: (key: string) => void;
}) {
  const current = sortKey(props.search);
  const th = "px-3 py-2 text-left text-xs font-medium text-slate-500 select-none";
  const SortIcon = current.desc ? ArrowDown : ArrowUp;
  return (
    <table className="hidden w-full border-collapse text-sm md:table">
      <thead className="sticky top-0 z-10 bg-slate-50 dark:bg-slate-900">
        <tr>
          <th className={th}>Note</th>
          {props.cols.map((c) => (
            <th key={c.key} className={`${th} cursor-pointer whitespace-nowrap hover:text-slate-800`} onClick={() => props.onSort(c.key)}>
              {c.label}
              {current.key === c.key && <SortIcon className="ml-1 inline size-3" />}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {props.notes.map((n) => (
          <tr
            key={n.id}
            onClick={() => props.onOpen(n.id)}
            className={`cursor-pointer border-t border-slate-100 hover:bg-accent-50 dark:border-slate-800 dark:hover:bg-slate-900 ${
              props.search.note === n.id ? "bg-accent-50 dark:bg-slate-900" : ""
            }`}
          >
            <td className="max-w-md truncate px-3 py-2">{preview(n, props.fields) || <span className="text-slate-400">(empty)</span>}</td>
            {props.cols.map((c) => (
              <td key={c.key} className="px-3 py-2 whitespace-nowrap text-slate-600 dark:text-slate-300">
                {formatCell(c, n.cells[c.key])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Cards(props: { notes: Note[]; fields: Field[]; cols: Field[]; selected?: string; onOpen: (id: string) => void }) {
  const chips = props.cols.filter((c) => c.kind === "select" || c.kind === "date");
  return (
    <ul className="divide-y divide-slate-100 md:hidden dark:divide-slate-800">
      {props.notes.map((n) => (
        <li key={n.id}>
          <button onClick={() => props.onOpen(n.id)} className="block w-full px-4 py-3 text-left active:bg-slate-100 dark:active:bg-slate-900">
            <p className="line-clamp-2 text-sm">{preview(n, props.fields) || <span className="text-slate-400">(empty)</span>}</p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {chips
                .filter((c) => cellText(n.cells[c.key]))
                .map((c) => (
                  <span key={c.key} className={chip} title={c.label}>
                    {formatCell(c, n.cells[c.key])}
                  </span>
                ))}
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}
