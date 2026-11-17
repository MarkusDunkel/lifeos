// Pure helpers for the ticket list: filters, labels and change names.

import type { Meta, TicketSummary } from "./api";

/** URL state of the Tickets page. `project` is a project title or "none" (on no board). */
export type TicketView = {
  q?: string;
  repo?: string;
  state?: "open" | "closed" | "all";
  project?: string;
  status?: string;
  label?: string;
  ticket?: string;
  changes?: boolean;
};

export const NO_PROJECT = "none";

/** The status-like field of a project: "Status" if present, else its first single-select. */
export function statusField(meta: Meta, project: string | undefined): string | null {
  const fields = project ? meta.projects[project]?.fields : undefined;
  if (!fields) return null;
  const selects = Object.entries(fields).filter(([, f]) => f.type === "SINGLE_SELECT");
  return (selects.find(([name]) => name === "Status") ?? selects[0])?.[0] ?? null;
}

const fold = (s: string) =>
  s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();

export function filterTickets(tickets: TicketSummary[], view: TicketView, meta?: Meta): TicketSummary[] {
  const tokens = fold(view.q ?? "").split(/\s+/).filter(Boolean);
  const state = view.state ?? "open";
  const field = meta && view.project && view.project !== NO_PROJECT ? statusField(meta, view.project) : null;
  return tickets
    .filter((t) => !view.repo || t.repo === view.repo)
    .filter((t) => state === "all" || t.state === state || t.number === null)
    .filter((t) => {
      if (!view.project) return true;
      if (view.project === NO_PROJECT) return Object.keys(t.projects).length === 0;
      return view.project in t.projects;
    })
    .filter((t) => !view.status || !field || (t.projects[view.project!]?.[field] ?? "") === view.status)
    .filter((t) => !view.label || t.labels.includes(view.label))
    .filter((t) => {
      if (!tokens.length) return true;
      const projects = Object.entries(t.projects).flatMap(([title, values]) => [title, ...Object.values(values)]);
      const hay = fold([t.title, t.number ?? "", ...t.labels, ...t.assignees, t.repo, ...projects].join(" "));
      return tokens.every((tok) => hay.includes(tok));
    })
    .sort((a, b) => (a.number ?? Number.MAX_SAFE_INTEGER) - (b.number ?? Number.MAX_SAFE_INTEGER) || a.path.localeCompare(b.path));
}

/** "Status" (or the status-like field) of a ticket per project, for the list. */
export function projectStatuses(t: TicketSummary, meta: Meta): { project: string; status: string | null }[] {
  return Object.keys(t.projects)
    .sort()
    .map((project) => {
      const field = statusField(meta, project);
      return { project, status: field ? (t.projects[project][field] ?? null) : null };
    });
}

export const isPending = (t: TicketSummary) => t.changes.length > 0;

export const ticketLabel = (t: Pick<TicketSummary, "number" | "repo">, showRepo = false) =>
  `${showRepo ? `${t.repo}` : ""}${t.number === null ? (showRepo ? " · " : "") + "new" : `#${t.number}`}`;

/** Readable names for changed fields: `project:Tasks:Status` → `Tasks · Status`. */
export function changeName(field: string): string {
  if (field === "new") return "new ticket";
  if (!field.startsWith("project:")) return field;
  const rest = field.slice("project:".length);
  const at = rest.indexOf(":");
  return at < 0 ? `${rest} (project)` : `${rest.slice(0, at)} · ${rest.slice(at + 1)}`;
}

export function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value].sort((a, b) => a.localeCompare(b));
}
