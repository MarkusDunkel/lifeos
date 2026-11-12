import { queryOptions } from "@tanstack/react-query";
import { api, qs } from "../../lib/api";

export type FieldMeta = { type: "SINGLE_SELECT" | "TEXT" | "NUMBER" | "DATE"; options: string[] };

export type Meta = {
  configured: boolean;
  repos: string[];
  project: string | null;
  fields: Record<string, FieldMeta>;
  labels: Record<string, string[]>;
  assignees: Record<string, string[]>;
  synced_at: string | null;
};

export type TicketSummary = {
  path: string;
  repo: string;
  number: number | null;
  title: string;
  state: "open" | "closed";
  labels: string[];
  assignees: string[];
  project: Record<string, string>;
  url: string | null;
  updated: string | null;
  conflicts: string[];
  changes: string[];
};

export type TicketDoc = TicketSummary & {
  body: string;
  revision: string;
  base: (Omit<TicketSummary, "path" | "conflicts" | "changes"> & { body: string; project: Record<string, string | null> }) | null;
};

export type PlanInfo = { id: string; status: "pending" | "applied" };
export type PlanDoc = PlanInfo & { plan: string; result: string | null };

export type TicketsData = {
  meta: Meta;
  tickets: TicketSummary[];
  broken: { path: string; error: string }[];
  plans: PlanInfo[];
};

export type SyncReport = {
  created: number;
  updated: number;
  unchanged: number;
  conflicts: string[];
  pending: string[];
  errors: string[];
  synced_at: string;
};

export type EditableFields = Partial<
  Pick<TicketDoc, "title" | "state" | "labels" | "assignees" | "body"> & { project: Record<string, string | null> }
>;

export const ticketsQuery = queryOptions({
  queryKey: ["tickets", "list"],
  queryFn: () => api<TicketsData>("/api/tickets"),
});

export const ticketQuery = (path: string) =>
  queryOptions({
    queryKey: ["tickets", "file", path],
    queryFn: () => api<TicketDoc>(`/api/tickets/file?${qs({ path })}`),
  });

export const planQuery = (id: string) =>
  queryOptions({
    queryKey: ["tickets", "plan", id],
    queryFn: () => api<PlanDoc>(`/api/tickets/plans/${encodeURIComponent(id)}`),
  });

const post = <T>(path: string, json?: unknown, method = "POST") => api<T>(path, { method, json });

export const saveTicket = (path: string, fields: EditableFields, revision: string) =>
  post<TicketDoc>("/api/tickets/file", { path, fields, revision }, "PUT");

export const createTicket = (repo: string, title: string) => post<TicketDoc>("/api/tickets/new", { repo, title });

export const deleteTicket = (path: string, revision: string) => post("/api/tickets/delete", { path, revision });

/** Discard local changes: the file gets the GitHub state seen at the last sync. */
export const revertTicket = (path: string, revision: string) => post<TicketDoc>("/api/tickets/revert", { path, revision });

export const resolveConflict = (path: string, field: string, choice: "local" | "remote", revision: string) =>
  post<TicketDoc>("/api/tickets/resolve", { path, field, choice, revision });

export const runSync = () => post<SyncReport>("/api/tickets/sync");

export const createPlan = () => post<PlanDoc>("/api/tickets/plans");

export const applyPlan = (id: string) =>
  post<PlanDoc & { sync: SyncReport }>(`/api/tickets/plans/${encodeURIComponent(id)}/apply`);

export const discardPlan = (id: string) => post(`/api/tickets/plans/${encodeURIComponent(id)}/discard`);
