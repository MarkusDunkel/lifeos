import { queryOptions } from "@tanstack/react-query";
import { api } from "../../lib/api";

export type Billing = "api" | "subscription";

export type AgentSummary = {
  name: string;
  title: string;
  board: string | null;
  boardTitle: string | null;
  model: string;
  billing: Billing;
  path: string;
  revision: string | null;
  /** Its processes, runs today and limits; null when the counters could not be read. */
  quota: AgentQuota | null;
};

export type AgentQuota = {
  processes: { id: string; ref: string; label: string; trigger: string; enabled: boolean }[];
  runsToday: number;
  dailyLimit: number | null;
  exhausted: boolean;
  /** Tokens of all its model calls today (local day), and what the API billed for them. */
  tokensToday: number;
  usdToday: number;
  note: string;
  limits: string[];
};

export type AgentsOverview = {
  agents: AgentSummary[];
  billingOptions: Billing[];
  errors: string[];
};

export const BILLING_LABELS: Record<Billing, { label: string; hint: string }> = {
  subscription: { label: "Claude subscription", hint: "Claude Code on your plan, no extra cost" },
  api: { label: "API", hint: "billed per token, capped by the agent's budget" },
};

export const agentsQuery = queryOptions({
  queryKey: ["agents"],
  queryFn: () => api<AgentsOverview>("/api/agents"),
});

export const setBilling = (agent: AgentSummary, billing: Billing) =>
  api<AgentSummary>(`/api/agents/${encodeURIComponent(agent.name)}/billing`, {
    method: "PUT",
    json: { billing, revision: agent.revision },
  });
