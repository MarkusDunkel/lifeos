import { queryOptions } from "@tanstack/react-query";
import { api } from "../../lib/api";

export type Level = "off" | "ok" | "warn" | "fail";
export type Where = "server" | "pc" | "manual" | "host";

export type Check = { id: string; label: string; ok: boolean | null; message: string; fix: string };
export type Run = { at: string; status: string; message: string };

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
};

export type FlowNode = {
  id: string;
  label: string;
  kind: "source" | "target";
  detail: string;
  folder: string | null;
  group: string | null;
  level: Level;
  reason: string;
};

export type Finding = { process: string; cause: string; action: string; affected: string };
export type Report = { at: string; level: Level; summary: string; findings: Finding[]; signature?: string };

export type ControlStatus = {
  level: Level;
  counts: Record<"ok" | "warn" | "fail", number>;
  checkedAt: string;
  processes: ProcessHealth[];
  nodes: FlowNode[];
  system: Check[];
  report: Report | null;
  requests: Record<string, string>;
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
