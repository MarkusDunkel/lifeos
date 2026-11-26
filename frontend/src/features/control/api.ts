import { queryOptions } from "@tanstack/react-query";
import { api } from "../../lib/api";

export type Level = "off" | "ok" | "warn" | "fail";
export type Where = "server" | "pc" | "manual" | "host";

export type Check = { id: string; label: string; ok: boolean | null; message: string; fix: string };
export type Run = { at: string; status: string; message: string };
export type AiAgent = {
  agent: string;
  title: string;
  model: string | null;
  billing: "api" | "subscription" | null;
  configured: boolean;
  runsToday: number;
  dailyLimit: number | null;
};

export type ProcessHealth = {
  id: string;
  label: string;
  where: Where;
  trigger: string;
  enabled: boolean;
  level: Level;
  reason: string;
  description: string;
  inputs: string[];
  outputs: string[];
  status: string | null;
  message: string;
  lastRun: string | null;
  lastSuccess: string | null;
  history: Run[];
  checks: Check[];
  runPath: string | null;
  canRequest: boolean;
  hint: string;
  mirror: boolean;
  system: boolean;
  foundation: boolean;
  secondOrder?: boolean;
  reactsTo: string[];
  reaction?: string;
  ai: AiAgent[];
  /** PC tasks: the app commit they ran on, the server's, and how the self-update went. */
  app?: PcApp | null;
};

export type PcApp = {
  version: string | null;
  serverVersion: string | null;
  update: string;
  ok: boolean | null;
  behind: boolean;
};

export type BasedOn = {
  id: string;
  label: string;
  kind: string;
  local?: boolean;
  derivedFrom?: string[];
  level: Level;
  updated: string | null;
  by: string;
  byLabel: string;
  lastRun: string | null;
  newSince?: number;
  pending?: number;
  total?: number;
  consideredLastRun?: number;
};

export type Generator = {
  lastRun: string | null;
  summary: string | null;
  events: string[] | null;
  uncertain: string[] | null;
  considered: Record<string, number> | null;
  pending: Record<string, number> | null;
  totals: Record<string, number> | null;
  factsChanged: number | null;
};

export type Trust = {
  level: Level;
  reason: string;
  producers: string[];
  upstream: string[];
  basedOn: BasedOn[];
  generators?: Record<string, Generator>;
};

export type FlowNode = {
  id: string;
  label: string;
  kind: "source" | "dataset" | "actionable";
  detail: string;
  folder: string | null;
  group: string | null;
  url?: string | null;
  local?: boolean;
  derivedFrom?: string[];
  level: Level;
  reason: string;
  trust?: Trust;
};

export type Finding = { process: string; cause: string; action: string; affected: string };
export type Report = { at: string; level: Level; summary: string; findings: Finding[]; signature?: string };

/** What the LifeOS manager says about a yellow or red light. */
export type ManagerNote = {
  cause: string | null;
  /** green: should be green by `until`; stays: an open ticket for the owner covers it. */
  expect: "green" | "stays" | null;
  ticket: string | null;
  at: string | null;
  until: string | null;
  /** runs the manager already spent on this light */
  attempts: number;
};

export type ControlStatus = {
  level: Level;
  counts: Record<"ok" | "warn" | "fail", number>;
  checkedAt: string;
  processes: ProcessHealth[];
  nodes: FlowNode[];
  system: Check[];
  report: Report | null;
  requests: Record<string, string>;
  manager?: Record<string, ManagerNote>;
};

export const controlQuery = queryOptions({
  queryKey: ["control"],
  queryFn: () => api<ControlStatus>("/api/control/status"),
  refetchInterval: 60_000,
});

/** Worker jobs are queued for the worker; manual syncs call their own endpoint. */
export const runNow = (p: ProcessHealth) =>
  p.canRequest
    ? api(`/api/control/run/${encodeURIComponent(p.id)}`, { method: "POST" })
    : api(p.runPath ?? "", { method: "POST" });
