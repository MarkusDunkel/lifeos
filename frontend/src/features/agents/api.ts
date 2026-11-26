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
  /** ``workflow``: a teammate on a board; anything else runs inside a process. */
  runner?: string;
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
  /** Tokens per local day, the last seven days, today last. */
  tokens7d?: number[];
  /** Its last model call (from the token ledger). */
  lastCall?: string | null;
  /** The newest run of the worker job it works in: often only a check for work. */
  lastJob?: { at: string; status: string | null; message: string } | null;
  /** The newest entry of its run log: time · ticket · outcome. */
  lastLog?: string | null;
  note: string;
  limits: string[];
};

export type AgentsOverview = {
  agents: AgentSummary[];
  billingOptions: Billing[];
  errors: string[];
};

export const BILLING_LABELS: Record<Billing, { label: string; hint: string }> = {
  subscription: { label: "Subscription", hint: "Claude Code on your plan, no extra cost" },
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

/** The numbers above the cards: everything the agents did today. */
export function totals(agents: AgentSummary[]): { tokens: number; usd: number; runs: number; exhausted: number } {
  return agents.reduce(
    (sum, a) => ({
      tokens: sum.tokens + (a.quota?.tokensToday ?? 0),
      usd: sum.usd + (a.quota?.usdToday ?? 0),
      runs: sum.runs + (a.quota?.runsToday ?? 0),
      exhausted: sum.exhausted + (a.quota?.exhausted ? 1 : 0),
    }),
    { tokens: 0, usd: 0, runs: 0, exhausted: 0 },
  );
}

/** Teammates that take tickets on a board vs. agents a process calls. */
export const onBoard = (a: AgentSummary) => (a.runner ? a.runner === "workflow" : Boolean(a.board));

export type SparkBar = { x: number; y: number; width: number; height: number; value: number };

/**
 * Bars of a small chart without axes: one per value, scaled to the largest one. A day with
 * tokens always shows at least a sliver; a day without stays empty.
 */
export function sparkBars(values: number[], width: number, height: number, gap = 2): SparkBar[] {
  if (values.length === 0) return [];
  const max = Math.max(...values, 0);
  const slot = width / values.length;
  return values.map((value, i) => {
    const h = max > 0 && value > 0 ? Math.max(1.5, (value / max) * height) : 0;
    return { x: i * slot + gap / 2, y: height - h, width: Math.max(1, slot - gap), height: h, value };
  });
}
