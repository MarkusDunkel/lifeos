import { queryOptions } from "@tanstack/react-query";
import { api, qs } from "../../lib/api";

export type RoundInfo = {
  round: string;
  state: "draft" | "sealed";
  items: number;
  created_at: string;
  sealed_at: string | null;
  stale: boolean;
  editable: boolean;
};

export type ReportInfo = {
  path: string;
  name: string;
  title: string;
  modified: string;
  archived: boolean;
  changelog: string | null;
  rounds: RoundInfo[];
};

export type ReportDoc = {
  path: string;
  content: string;
  sha256: string;
  archived: boolean;
  changelog: string | null;
  rounds: RoundInfo[];
};

export type Scope = "blocks" | "rendered-text";

export type FeedbackItem = {
  id: string;
  start_line: number;
  end_line: number;
  scope: Scope;
  quote: string;
  feedback: string;
  target: string;
};

export type RoundData = {
  round: string;
  source: string;
  source_sha256: string;
  state: "draft" | "sealed";
  created_at: string;
  sealed_at: string | null;
  next_id: string;
  items: FeedbackItem[];
  stale: boolean;
  editable: boolean;
};

export type Sealed = RoundData & { feedbackFile: string; prompt: string };

export const reportsQuery = queryOptions({
  queryKey: ["workspaces", "list"],
  queryFn: () => api<{ open: ReportInfo[]; archived: ReportInfo[] }>("/api/workspaces"),
});

export const reportQuery = (path: string) =>
  queryOptions({
    queryKey: ["workspaces", "file", path],
    queryFn: () => api<ReportDoc>(`/api/workspaces/file?${qs({ path })}`),
  });

export const roundQuery = (name: string, round: string) =>
  queryOptions({
    queryKey: ["workspaces", "round", name, round],
    queryFn: () => api<RoundData>(`/api/workspaces/round?${qs({ name, round })}`),
  });

const post = <T>(path: string, json: unknown, method = "POST") => api<T>(path, { method, json });

export const startRound = (name: string) => post<RoundData>("/api/workspaces/rounds", { name });

export const addItem = (
  name: string,
  round: string,
  item: { start_line: number; end_line: number; scope: Scope; feedback: string; quote?: string },
) => post<RoundData>("/api/workspaces/rounds/items", { name, round, ...item });

export const updateItem = (name: string, round: string, id: string, feedback: string) =>
  post<RoundData>("/api/workspaces/rounds/items", { name, round, id, feedback }, "PUT");

export const deleteItem = (name: string, round: string, id: string) =>
  post<RoundData>("/api/workspaces/rounds/items/delete", { name, round, id });

export const sealRound = (name: string, round: string) =>
  post<Sealed>("/api/workspaces/rounds/seal", { name, round });

export const discardRound = (name: string, round: string) =>
  post("/api/workspaces/rounds/discard", { name, round });
