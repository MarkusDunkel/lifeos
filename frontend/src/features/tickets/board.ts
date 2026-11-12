// Pure helpers for the tickets board and list.

import type { Meta, TicketSummary } from "./api";

export type TicketView = {
  view?: "board" | "list";
  q?: string;
  repo?: string;
  state?: "open" | "closed" | "all";
  ticket?: string;
  changes?: boolean;
};

/** The single-select field the board groups by: "Status" if present, else the first one. */
export function boardField(meta: Meta): string | null {
  const selects = Object.entries(meta.fields).filter(([, f]) => f.type === "SINGLE_SELECT");
  return (selects.find(([name]) => name === "Status") ?? selects[0])?.[0] ?? null;
}

/** Column keys: the field's options in project order plus `null` for "no value". */
export function columnsFor(meta: Meta, field: string | null): (string | null)[] {
  if (!field) return ["open", "closed"];
  return [...(meta.fields[field]?.options ?? []), null];
}

export function columnOf(t: TicketSummary, field: string | null): string | null {
  return field ? (t.project[field] ?? null) : t.state;
}

const fold = (s: string) =>
  s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();

export function filterTickets(tickets: TicketSummary[], view: TicketView): TicketSummary[] {
  const tokens = fold(view.q ?? "").split(/\s+/).filter(Boolean);
  const state = view.state ?? "open";
  return tickets
    .filter((t) => !view.repo || t.repo === view.repo)
    .filter((t) => state === "all" || t.state === state || t.number === null)
    .filter((t) => {
      if (!tokens.length) return true;
      const hay = fold([t.title, t.number ?? "", ...t.labels, ...t.assignees, t.repo].join(" "));
      return tokens.every((tok) => hay.includes(tok));
    })
    .sort((a, b) => (a.number ?? Number.MAX_SAFE_INTEGER) - (b.number ?? Number.MAX_SAFE_INTEGER) || a.path.localeCompare(b.path));
}

export function groupTickets(tickets: TicketSummary[], meta: Meta, field: string | null) {
  const columns = columnsFor(meta, field);
  const groups = new Map<string | null, TicketSummary[]>(columns.map((c) => [c, []]));
  for (const t of tickets) {
    const key = columnOf(t, field);
    // Values that are no longer an option still show up, in their own column.
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(t);
  }
  return groups;
}

export const isPending = (t: TicketSummary) => t.changes.length > 0;

export const ticketLabel = (t: Pick<TicketSummary, "number" | "repo">, showRepo = false) =>
  `${showRepo ? `${t.repo}` : ""}${t.number === null ? (showRepo ? " · " : "") + "new" : `#${t.number}`}`;

/** Readable names for changed fields (`project.Status` → `Status`). */
export const changeName = (field: string) => (field === "new" ? "new ticket" : field.replace(/^project\./, ""));

export function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value].sort((a, b) => a.localeCompare(b));
}
