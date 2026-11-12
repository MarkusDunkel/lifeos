import { queryOptions } from "@tanstack/react-query";
import { api, qs } from "../../lib/api";
import type { TreeNode } from "./links";

export type Doc = { path: string; content: string; revision: string };
export type SearchHit = { path: string; title: string; snippet: string };
export type Backlink = { path: string; title: string };

export const treeQuery = queryOptions({
  queryKey: ["knowledge", "tree"],
  queryFn: () => api<TreeNode>("/api/knowledge/tree"),
});

export const docQuery = (path: string) =>
  queryOptions({
    queryKey: ["knowledge", "file", path],
    queryFn: () => api<Doc>(`/api/knowledge/file?${qs({ path })}`),
  });

export const searchQuery = (q: string) =>
  queryOptions({
    queryKey: ["knowledge", "search", q],
    queryFn: () => api<SearchHit[]>(`/api/knowledge/search?${qs({ q })}`),
    enabled: q.trim().length > 0,
  });

export const backlinksQuery = (path: string) =>
  queryOptions({
    queryKey: ["knowledge", "backlinks", path],
    queryFn: () => api<Backlink[]>(`/api/knowledge/backlinks?${qs({ path })}`),
  });

export const saveDoc = (path: string, content: string, revision: string | null) =>
  api<{ path: string; revision: string }>("/api/knowledge/file", {
    method: "PUT",
    json: { path, content, revision },
  });

export const deleteDoc = (path: string, revision: string) =>
  api("/api/knowledge/delete", { method: "POST", json: { path, revision } });
