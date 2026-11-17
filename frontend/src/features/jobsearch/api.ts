import { keepPreviousData, queryOptions } from "@tanstack/react-query";
import { api, qs } from "../../lib/api";

export type Salary = { amount: number; unit: string; currency: string };

export type ImportRun = {
  at: string;
  source: string;
  started?: string | null;
  listed: number;
  new: number;
  duplicates?: number;
  reposts?: number;
  failed: number;
  complete: boolean;
  pruned?: number;
  errors: string[];
};

export type JobsearchStatus = {
  lastImport: ImportRun | null;
  doneToday: boolean;
  counts: { active: number; duplicates: number; reposts: number; gone: number };
  runs: ImportRun[];
};

export type PostingRow = {
  key: string;
  source: string;
  id: string;
  url: string;
  title: string;
  company: string;
  place: string;
  salary: Salary | null;
  employment_type: string[];
  skills: string[];
  industry: string;
  date_posted: string | null;
  valid_through: string | null;
  firstSeen: string | null;
  lastSeen: string | null;
  goneAt: string | null;
  duplicateOf: string | null;
  repostOf: string | null;
  duplicateCount?: number;
};

export type PostingDetail = PostingRow & { description: string; benefits: string[]; related: PostingRow[] };

export type PostingsPage = { total: number; offset: number; items: PostingRow[] };

export type PostingsFilter = { q: string; duplicates: boolean; gone: boolean; days: number | null };

export const PAGE_SIZE = 50;

export const jobsearchStatusQuery = queryOptions({
  queryKey: ["jobsearch", "status"],
  queryFn: () => api<JobsearchStatus>("/api/jobsearch/status"),
});

export const postingsQuery = (filter: PostingsFilter, offset: number) =>
  queryOptions({
    queryKey: ["jobsearch", "postings", filter, offset],
    queryFn: () => {
      const params: Record<string, string> = { offset: String(offset), limit: String(PAGE_SIZE) };
      if (filter.q.trim()) params.q = filter.q.trim();
      if (filter.duplicates) params.duplicates = "true";
      if (filter.gone) params.gone = "true";
      if (filter.days) params.days = String(filter.days);
      return api<PostingsPage>(`/api/jobsearch/postings?${qs(params)}`);
    },
    placeholderData: keepPreviousData,
  });

export const postingQuery = (key: string) => {
  const [source, id] = key.split(":");
  return queryOptions({
    queryKey: ["jobsearch", "posting", key],
    queryFn: () => api<PostingDetail>(`/api/jobsearch/postings/${source}/${id}`),
  });
};
