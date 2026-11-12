// Pure helpers for the notes grid: filtering, sorting and display. No React here.

export type FieldKind = "text" | "select" | "string" | "date" | "datetime";
export type Cell = string | number | boolean | null;
export type Cells = Record<string, Cell>;

export type Field = {
  key: string;
  label: string;
  kind: FieldKind;
  options: string[];
  readonly: boolean;
  hidden: boolean;
};

export type Note = {
  id: string;
  file: string;
  order: number | null;
  cells: Cells;
  revision: string;
};

/** Grid state; it lives in the URL so views can be bookmarked. */
export type View = {
  q?: string;
  sort?: string;
  desc?: boolean;
  f?: Record<string, string>;
};

/** Filter value that matches notes where the field is empty. */
export const EMPTY = "__empty__";
export const CHANGED = "lastChanged";

export const cellText = (v: Cell | undefined): string => (v === null || v === undefined ? "" : String(v));

const fold = (s: string) =>
  s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();

export function matchesQuery(note: Note, q: string): boolean {
  const tokens = fold(q).split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return true;
  const haystack = fold(Object.values(note.cells).map(cellText).join("\n"));
  return tokens.every((t) => haystack.includes(t));
}

export function matchesFilters(note: Note, filters: Record<string, string> | undefined): boolean {
  if (!filters) return true;
  return Object.entries(filters).every(([key, value]) => {
    const text = cellText(note.cells[key]);
    return value === EMPTY ? text === "" : text === value;
  });
}

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

export function sortKey(view: View): { key: string; desc: boolean } {
  return view.sort ? { key: view.sort, desc: view.desc ?? false } : { key: CHANGED, desc: true };
}

export function applyView(notes: Note[], view: View): Note[] {
  const { key, desc } = sortKey(view);
  const visible = notes.filter((n) => matchesFilters(n, view.f) && matchesQuery(n, view.q ?? ""));
  return visible
    .map((note, index) => ({ note, index, text: cellText(note.cells[key]) }))
    .sort((a, b) => {
      // Empty values always go last, whatever the direction.
      if (!a.text || !b.text) return a.text ? -1 : b.text ? 1 : a.index - b.index;
      const c = collator.compare(a.text, b.text);
      return (desc ? -c : c) || a.index - b.index;
    })
    .map((x) => x.note);
}

/** Main text of a note: the first long-text field, else the first short one. */
export function mainField(fields: Field[]): Field | undefined {
  return fields.find((f) => f.kind === "text") ?? fields.find((f) => f.kind === "string");
}

export function preview(note: Note, fields: Field[]): string {
  const main = mainField(fields);
  const text = main ? cellText(note.cells[main.key]) : "";
  return text.trim().split("\n")[0]?.trim() ?? "";
}

export const columns = (fields: Field[]) =>
  fields.filter((f) => !f.hidden && f !== mainField(fields) && f.key !== "created");

export const filterFields = (fields: Field[]) => fields.filter((f) => f.kind === "select" && !f.hidden);

/** Values offered in a filter dropdown: configured options plus whatever occurs in the data. */
export function filterOptions(field: Field, notes: Note[]): string[] {
  const seen = new Set(field.options);
  for (const n of notes) {
    const t = cellText(n.cells[field.key]);
    if (t) seen.add(t);
  }
  return [...seen].sort(collator.compare);
}

export function formatCell(field: Field, value: Cell | undefined): string {
  const text = cellText(value);
  if (field.kind !== "datetime" || !text) return text;
  const d = new Date(text);
  if (Number.isNaN(d.getTime())) return text;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Cells for a new note: active filters become defaults. */
export function newCells(fields: Field[], filters: Record<string, string> | undefined): Cells {
  const cells: Cells = {};
  for (const f of fields) if (!f.readonly) cells[f.key] = null;
  for (const [k, v] of Object.entries(filters ?? {})) if (v !== EMPTY) cells[k] = v;
  return cells;
}

export function sameCells(a: Cells, b: Cells): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const k of keys) if (cellText(a[k]) !== cellText(b[k])) return false;
  return true;
}
