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

export type Near = { label: string; km: number };

export type ProfileFit = {
  slug: string;
  title: string;
  fit: number;
  skills: number;
  title_match: number;
  matched: string[];
};

export type Fit = {
  fit: number;
  profile: string | null;
  profile_title: string | null;
  profiles: ProfileFit[];
  homes: Near[];
  nearest: Near | null;
  remote: boolean;
  passed: boolean;
};

export type Commute = {
  homes: Record<string, { car: number | null; bike: number | null; tooFar: boolean }>;
  minutes: number | null;
  factor: number;
  tooFar: boolean;
};

export type FitOptions = {
  profiles: { slug: string; title: string }[];
  homes: { label: string; lat: number; lon: number; radius_km: number }[];
};

export type Recommendation = "apply" | "maybe" | "no";

export type AssessmentSummary = {
  recommendation: Recommendation | null;
  fit: number | null;
  bestProfile: string | null;
  summary: string;
  at: string | null;
  stale: boolean;
};

export type AssessmentDetail = AssessmentSummary & {
  detail: {
    recommendation: Recommendation;
    fit: number;
    best_profile: string;
    summary: string;
    profiles: { slug: string; fit: number; reason: string }[];
    strengths: string[];
    gaps: string[];
    red_flags: string[];
    questions: string[];
    company: string;
    missing_skills: string[];
  } | null;
  research: string;
  error: string | null;
  usage: { eur: number; tokens: number; searches: number; listEur: number } | null;
};

export type MatcherStatus = {
  configured: boolean;
  dailyLimit?: number;
  manualLimit?: number;
  autoToday?: number;
  manualToday?: number;
  queued?: number;
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
  application: string | null;
  duplicateCount?: number;
  fit?: Fit;
  commute?: Commute;
  assessment?: AssessmentSummary;
};

export type PostingDetail = Omit<PostingRow, "assessment"> & {
  description: string;
  benefits: string[];
  related: PostingRow[];
  assessment?: AssessmentDetail;
  queued?: boolean;
};

export type PostingsPage = { total: number; offset: number; items: PostingRow[] };

export type PostingsFilter = {
  q: string;
  duplicates: boolean;
  gone: boolean;
  applied: boolean;
  days: number | null;
  sort: "new" | "fit" | "match";
  profile: string;
  home: string;
  reach: boolean;
  passed: boolean;
  recommended: boolean;
};

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
      if (filter.applied) params.applied = "true";
      if (filter.days) params.days = String(filter.days);
      if (filter.sort !== "new") params.sort = filter.sort;
      if (filter.profile) params.profile = filter.profile;
      if (filter.home) params.home = filter.home;
      if (filter.reach) params.reach = "true";
      if (filter.passed) params.passed = "true";
      if (filter.recommended) params.recommended = "true";
      return api<PostingsPage>(`/api/jobsearch/postings?${qs(params)}`);
    },
    placeholderData: keepPreviousData,
  });

export const fitOptionsQuery = queryOptions({
  queryKey: ["jobsearch", "fit-options"],
  queryFn: () => api<FitOptions>("/api/jobsearch/fit/options"),
});

export const matcherQuery = queryOptions({
  queryKey: ["jobsearch", "matcher"],
  queryFn: () => api<MatcherStatus>("/api/jobsearch/matcher"),
});

export const requestAssessment = (key: string) => {
  const [source, id] = key.split(":");
  return api<{ queued: boolean; position: number }>(`/api/jobsearch/postings/${source}/${id}/assess`, { method: "POST" });
};

export type Verdicts = { apply: number; maybe: number; no: number };

export type Dashboard = {
  generatedAt: string;
  goodFit: number;
  funnel: {
    active: number;
    reach: number;
    passed: number;
    assessed: number;
    verdicts: Verdicts;
    applied: number;
    open: number;
    daysLeft: number | null;
    matcher: MatcherStatus;
  };
  demand: {
    slug: string;
    title: string;
    good: number;
    near: number;
    share: number;
    weight: number;
    newWeek: number;
    newWeekBefore: number;
  }[];
  places: {
    columns: string[];
    homes: string[];
    rows: { slug: string; title: string; counts: Record<string, number> }[];
    commuteMedian: Record<string, number | null>;
  };
  quality: {
    points: { key: string; title: string; prefilter: number; ai: number | null; recommendation: Recommendation | null }[];
    prefilterMean: number | null;
    aiMean: number | null;
    offset: number | null;
    sameProfile: number | null;
    hitRate: number;
    byProfile: { slug: string; title: string; counts: Verdicts }[];
    stale: number;
    failed: number;
    tokensMean: number | null;
    listEurMean: number | null;
    listEurTotal: number;
  } | null;
  gaps: {
    missing: { skill: string; count: number }[];
    wanted: { skill: string; count: number; level: number | null }[];
    basis: number;
  };
};

export const dashboardQuery = (refresh: boolean) =>
  queryOptions({
    queryKey: ["jobsearch", "dashboard", refresh],
    queryFn: () => api<Dashboard>(`/api/jobsearch/dashboard${refresh ? "?refresh=true" : ""}`),
    staleTime: refresh ? 0 : 60_000,
  });

export type ApplicationStarted = { issue: number; url: string; folder: string };

export const startApplication = (posting: string) =>
  api<ApplicationStarted>("/api/applications", { method: "POST", json: { posting } });

export const postingQuery = (key: string) => {
  const [source, id] = key.split(":");
  return queryOptions({
    queryKey: ["jobsearch", "posting", key],
    queryFn: () => api<PostingDetail>(`/api/jobsearch/postings/${source}/${id}`),
  });
};
