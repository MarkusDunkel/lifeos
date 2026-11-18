import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, TriangleAlert } from "lucide-react";
import { ApiError } from "../../lib/api";
import { type AgentSummary, agentsQuery, type Billing, BILLING_LABELS, setBilling } from "./api";

export function AgentsPage() {
  const overview = useQuery(agentsQuery);
  const data = overview.data;
  return (
    <div className="mx-auto max-w-2xl space-y-6 p-6 md:p-8">
      <div>
        <h1 className="text-2xl font-semibold">Agents</h1>
        <p className="mt-1 text-sm text-slate-500">
          AI teammates on the boards. Their definitions live in the vault under <code>agents/</code>.
        </p>
      </div>
      {overview.isLoading && <p className="text-slate-400">Loading…</p>}
      {overview.error && <p className="text-red-600">{overview.error.message}</p>}
      {data?.errors.map((e) => (
        <p key={e} className="flex gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          <TriangleAlert className="size-4 shrink-0" /> {e}
        </p>
      ))}
      {data && data.agents.length === 0 && <p className="text-slate-500">No agents defined yet.</p>}
      {data?.agents.map((agent) => (
        <AgentCard key={agent.name} agent={agent} options={data.billingOptions} />
      ))}
    </div>
  );
}

function AgentCard({ agent, options }: { agent: AgentSummary; options: Billing[] }) {
  const queryClient = useQueryClient();
  const change = useMutation({
    mutationFn: (billing: Billing) => setBilling(agent, billing),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["agents"] }),
  });
  const error = change.error;
  return (
    <section className="space-y-4 rounded-xl border border-slate-200 p-4 dark:border-slate-800">
      <div className="flex items-start gap-3">
        <Bot className="mt-0.5 size-5 shrink-0 text-sky-600" />
        <div className="min-w-0">
          <h2 className="font-medium">{agent.title}</h2>
          <p className="text-sm text-slate-500">
            {agent.boardTitle ?? agent.board ?? "no board"} · {agent.model} · <code>{agent.path}</code>
          </p>
        </div>
      </div>

      <fieldset>
        <legend className="mb-2 text-sm font-medium">Paid via</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {options.map((option) => {
            const active = agent.billing === option;
            return (
              <button
                key={option}
                type="button"
                disabled={change.isPending || active}
                onClick={() => change.mutate(option)}
                aria-pressed={active}
                className={`rounded-lg border p-3 text-left text-sm transition-colors ${
                  active
                    ? "border-sky-500 bg-sky-50 text-sky-900 dark:border-sky-600 dark:bg-sky-950/40 dark:text-sky-100"
                    : "border-slate-200 hover:bg-slate-50 disabled:opacity-60 dark:border-slate-700 dark:hover:bg-slate-800"
                }`}
              >
                <span className="block font-medium">{BILLING_LABELS[option]?.label ?? option}</span>
                <span className="block text-xs text-slate-500">{BILLING_LABELS[option]?.hint}</span>
              </button>
            );
          })}
        </div>
      </fieldset>

      {change.isPending && <p className="text-sm text-slate-500">Saving…</p>}
      {error && (
        <p className="text-sm text-red-600">
          {error instanceof ApiError && error.status === 409
            ? "The agent file changed in the meantime – reloaded, please try again."
            : error.message}
        </p>
      )}
      <p className="text-xs text-slate-500">
        A change edits <code>billing:</code> in the agent file, adds a line to its history and is
        committed. The worker uses it from the next vault sync on; a run that is already going
        keeps its way of paying.
      </p>
    </section>
  );
}
