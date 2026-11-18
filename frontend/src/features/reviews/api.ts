import { queryOptions } from "@tanstack/react-query";
import { api, qs } from "../../lib/api";
import type { RoundData, RoundInfo } from "../workspaces/api";
import type { NewItem, RoundsApi } from "../review/ReviewPane";

export type ViewType = "markdown" | "pdf" | "pdf-generated" | "none";

export type PackageInfo = {
  path: string;
  title: string;
  ticket?: string | null;
  documents: number;
  modified: string;
  error?: string;
};

export type ReviewDocument = {
  index: number;
  label: string;
  source: string;
  exists: boolean;
  content: string;
  sha256: string;
  view: ViewType;
  rounds: RoundInfo[];
};

export type ReviewPackage = { path: string; title: string; ticket: string | null; documents: ReviewDocument[] };

export const packagesQuery = queryOptions({
  queryKey: ["reviews", "list"],
  queryFn: () => api<PackageInfo[]>("/api/reviews"),
});

export const packageQuery = (path: string) =>
  queryOptions({
    queryKey: ["reviews", "package", path],
    queryFn: () => api<ReviewPackage>(`/api/reviews/package?${qs({ path })}`),
  });

export const viewUrl = (path: string, doc: number, version: string) =>
  `/api/reviews/view?${qs({ path, doc: String(doc), v: version.slice(0, 12) })}`;

/** A GitHub link for ``owner/repo#12``. */
export function ticketUrl(ref: string): string | null {
  const m = /^([\w.-]+\/[\w.-]+)#(\d+)$/.exec(ref.trim());
  return m ? `https://github.com/${m[1]}/issues/${m[2]}` : null;
}

const post = <T>(path: string, json: unknown, method = "POST") => api<T>(path, { method, json });

export function roundsApi(path: string, doc: number): RoundsApi {
  const ref = { path, doc };
  return {
    invalidate: ["reviews"],
    round: (round) =>
      queryOptions({
        queryKey: ["reviews", "round", path, doc, round],
        queryFn: () => api<RoundData>(`/api/reviews/round?${qs({ path, doc: String(doc), round })}`),
      }),
    start: () => post<RoundData>("/api/reviews/rounds", ref),
    add: (round, item: NewItem) => post<RoundData>("/api/reviews/rounds/items", { ...ref, round, ...item }),
    update: (round, id, feedback) => post<RoundData>("/api/reviews/rounds/items", { ...ref, round, id, feedback }, "PUT"),
    remove: (round, id) => post<RoundData>("/api/reviews/rounds/items/delete", { ...ref, round, id }),
    // no `seal`: rounds of a package are sealed together by "Send feedback"
    discard: (round) => post("/api/reviews/rounds/discard", { ...ref, round }),
  };
}

export type SendResult = { ticket: string; url: string | null; comments: number };

/** Post the open comments of all documents to the package's ticket and seal their rounds. */
export const sendFeedback = (path: string) => post<SendResult>("/api/reviews/send", { path });

/** Comments in open drafts, counted once per file (two documents may share a source). */
export function pendingComments(docs: ReviewDocument[]): number {
  const seen = new Set<string>();
  let count = 0;
  for (const d of docs) {
    const draft = d.rounds.find((r) => r.editable);
    if (!draft || seen.has(d.source)) continue;
    seen.add(d.source);
    count += draft.items;
  }
  return count;
}
