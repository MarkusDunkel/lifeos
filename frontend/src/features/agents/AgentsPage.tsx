import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation } from "@tanstack/react-router";
import { Bot, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Badge, Button, Card, EmptyState, Notice, Page, PageHeader, QueryState, Segmented, Stat } from "../../components/ui";
import { ApiError } from "../../lib/api";
import { formatAgo } from "../control/format";
import { compact } from "../control/tokens";
import {
  type AgentQuota,
  type AgentSummary,
  agentsQuery,
  type Billing,
  BILLING_LABELS,
  onBoard,
  setBilling,
  sparkBars,
  totals,
} from "./api";

const usd = (v: number) => `$${v.toFixed(2)}`;

export function AgentsPage() {
  const overview = useQuery(agentsQuery);
  const data = overview.data;
  // /agents#<name> (e.g. from the control room): scroll to that agent's card, once per link –
  // not again on every refresh of the numbers
  const focus = useLocation({ select: (l) => l.hash });
  const scrolled = useRef<string | null>(null);
  useEffect(() => {
    if (!focus || !data || scrolled.current === focus) return;
    scrolled.current = focus;
    document.getElementById(focus)?.scrollIntoView({ block: "center" });
  }, [focus, data]);

  const boards = data?.agents.filter(onBoard) ?? [];
  const behind = data?.agents.filter((a) => !onBoard(a)) ?? [];
  const sum = totals(data?.agents ?? []);

  return (
    <Page width="lg">
      <PageHeader
        icon={Bot}
        title="Agents"
        subtitle={
          <>
            Every AI agent LifeOS calls: the teammates on the boards and the agents behind the processes in the control
            room. Their definitions live in the vault under <code>agents/</code>.
          </>
        }
      />
      <QueryState query={overview} rows={6} what="the agents" />
      {data?.errors.map((e) => (
        <Notice key={e} tone="warn">
          {e}
        </Notice>
      ))}
      {data && data.agents.length === 0 && (
        <EmptyState icon={Bot} title="No agents defined yet">
          Add a definition under <code>agents/</code> in the vault.
        </EmptyState>
      )}
      {data && data.agents.length > 0 && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Agents" value={data.agents.length} sub={`${boards.length} on boards, ${behind.length} in processes`} />
            <Stat label="Runs today" value={sum.runs} />
            <Stat label="Tokens today" value={compact(sum.tokens)} sub={sum.usd > 0 ? `${usd(sum.usd)} billed by the API` : "nothing billed by the API"} />
            <Stat
              label="Daily limits reached"
              value={sum.exhausted}
              sub={sum.exhausted ? "these agents wait until midnight" : "every agent can still run"}
            />
          </div>
          <Group title="On the boards" hint="Teammates that take the tickets you approve." agents={boards} options={data.billingOptions} focus={focus} />
          <Group title="Behind processes" hint="Called by a process in the control room when there is work." agents={behind} options={data.billingOptions} focus={focus} />
        </>
      )}
    </Page>
  );
}

function Group({
  title,
  hint,
  agents,
  options,
  focus,
}: {
  title: string;
  hint: string;
  agents: AgentSummary[];
  options: Billing[];
  focus: string;
}) {
  if (agents.length === 0) return null;
  return (
    <section className="space-y-3">
      <div>
        <h2 className="font-semibold">{title}</h2>
        <p className="text-xs text-slate-500 dark:text-slate-400">{hint}</p>
      </div>
      <div className="grid items-start gap-4 lg:grid-cols-2">
        {agents.map((agent) => (
          <AgentCard key={agent.name} agent={agent} options={options} focused={agent.name === focus} />
        ))}
      </div>
    </section>
  );
}

/** Tokens of the last seven days as small bars, today last. */
function Spark({ values }: { values: number[] }) {
  const W = 84;
  const H = 24;
  const days = ["6 days ago", "5 days ago", "4 days ago", "3 days ago", "2 days ago", "yesterday", "today"];
  return (
    <svg width={W} height={H} role="img" aria-label={`Tokens per day, last ${values.length} days`} className="shrink-0">
      <line x1={0} x2={W} y1={H - 0.5} y2={H - 0.5} className="stroke-slate-200 dark:stroke-slate-700" />
      {sparkBars(values, W, H - 1).map((b, i) => (
        <rect
          key={i}
          x={b.x}
          y={b.y}
          width={b.width}
          height={b.height}
          rx={1.5}
          className={i === values.length - 1 ? "fill-accent-500" : "fill-slate-300 dark:fill-slate-600"}
        >
          <title>{`${days[days.length - values.length + i] ?? ""}: ${b.value.toLocaleString()} tokens`}</title>
        </rect>
      ))}
    </svg>
  );
}

const JOB_TONE: Record<string, "ok" | "neutral" | "error"> = { ok: "ok", skipped: "neutral" };

/** How the agent last ran: its model call, the job it works in, its run log. */
function LastRun({ quota }: { quota: AgentQuota }) {
  const job = quota.lastJob;
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
      <dt className="text-slate-500 dark:text-slate-400">Last model call</dt>
      <dd>{quota.lastCall ? formatAgo(quota.lastCall) : "none on record"}</dd>
      {job && (
        <>
          <dt className="text-slate-500 dark:text-slate-400">Last check</dt>
          <dd className="min-w-0">
            <span className="inline-flex items-center gap-1.5">
              <Badge tone={JOB_TONE[job.status ?? ""] ?? "error"}>{job.status ?? "?"}</Badge>
              {formatAgo(job.at)}
            </span>
            {job.message && (
              <span className="block truncate text-xs text-slate-500 dark:text-slate-400" title={job.message}>
                {job.message}
              </span>
            )}
          </dd>
        </>
      )}
      {quota.lastLog && (
        <>
          <dt className="text-slate-500 dark:text-slate-400">Last ticket</dt>
          <dd className="truncate" title={quota.lastLog}>
            {quota.lastLog}
          </dd>
        </>
      )}
    </dl>
  );
}

function Usage({ quota }: { quota: AgentQuota }) {
  const { runsToday, dailyLimit, exhausted } = quota;
  const share = dailyLimit ? Math.min(1, runsToday / dailyLimit) : 0;
  return (
    <div className="space-y-2">
      <div>
        <div className="mb-1 flex items-baseline justify-between gap-2 text-sm">
          <span className="text-slate-500 dark:text-slate-400">Runs today</span>
          <span className={`tabular-nums ${exhausted ? "font-medium text-amber-800 dark:text-amber-200" : ""}`}>
            {runsToday}
            {dailyLimit !== null ? ` of max. ${dailyLimit}` : " · no daily limit"}
          </span>
        </div>
        {dailyLimit !== null && (
          <div className="h-1.5 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
            <div className={`h-full rounded-full ${exhausted ? "bg-amber-500" : "bg-accent-500"}`} style={{ width: `${share * 100}%` }} />
          </div>
        )}
        {quota.note && <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{quota.note}</p>}
      </div>
      <div className="flex items-end justify-between gap-3">
        <div className="text-sm">
          <div className="text-slate-500 dark:text-slate-400">Tokens today</div>
          <div>
            <span className="text-lg font-semibold tabular-nums">{compact(quota.tokensToday)}</span>
            {quota.usdToday > 0 && <span className="text-xs text-slate-500"> · {usd(quota.usdToday)} billed</span>}
          </div>
        </div>
        {quota.tokens7d && (
          <div className="text-right">
            <Spark values={quota.tokens7d} />
            <div className="text-[10px] text-slate-400">7 days · {compact(quota.tokens7d.reduce((a, b) => a + b, 0))}</div>
          </div>
        )}
      </div>
      {exhausted && (
        <Notice tone="warn">
          Used up for today: the agent does not run again until midnight. Raise <code>daily_limit</code> in the agent
          file to allow more.
        </Notice>
      )}
    </div>
  );
}

function AgentCard({ agent, options, focused }: { agent: AgentSummary; options: Billing[]; focused: boolean }) {
  const queryClient = useQueryClient();
  const change = useMutation({
    mutationFn: (billing: Billing) => setBilling(agent, billing),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["agents"] }),
  });
  // asked first: the switch edits and commits the agent file
  const [ask, setAsk] = useState<Billing | null>(null);
  const error = change.error;
  const quota = agent.quota;
  return (
    <Card
      id={agent.name}
      className={`scroll-mt-4 !space-y-4 ${
        focused ? "!border-accent-500 ring-2 ring-accent-500/40" : quota?.exhausted ? "!border-amber-400 dark:!border-amber-700" : ""
      }`}
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-lg bg-violet-100 text-violet-600 dark:bg-violet-900/40 dark:text-violet-300">
          <Sparkles className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="flex flex-wrap items-center gap-x-2 gap-y-1 font-semibold">
            {agent.title}
            {quota?.exhausted && <Badge tone="warn">daily limit reached</Badge>}
          </h3>
          <p className="truncate text-xs text-slate-500 dark:text-slate-400" title={agent.path}>
            <span className="font-mono">{agent.name}</span> · {agent.model}
            {agent.board ? ` · ${agent.boardTitle ?? agent.board}` : ""}
          </p>
        </div>
      </div>

      {quota ? (
        <>
          <Usage quota={quota} />
          <LastRun quota={quota} />
          <div>
            <div className="mb-1 text-xs font-medium text-slate-500 dark:text-slate-400">Runs in</div>
            {quota.processes.length === 0 ? (
              <span className="text-sm text-slate-500">no process calls this agent</span>
            ) : (
              <ul className="space-y-1 text-sm">
                {quota.processes.map((p) => (
                  <li key={p.id} className="flex flex-wrap items-center gap-x-2">
                    <Link
                      to="/control"
                      search={{ sel: `p:${p.id}` }}
                      className="inline-flex items-center gap-1.5 font-medium text-accent-700 hover:underline dark:text-accent-300"
                      title="Open in the control room"
                    >
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] font-normal text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                        {p.ref}
                      </span>
                      {p.label}
                    </Link>
                    <span className="text-xs text-slate-500">{p.enabled ? p.trigger : "off"}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {quota.limits.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {quota.limits.map((l) => (
                <Badge key={l}>{l}</Badge>
              ))}
            </div>
          )}
        </>
      ) : (
        <p className="text-sm text-slate-500">The run counters could not be read.</p>
      )}

      <div className="space-y-2 border-t border-slate-100 pt-3 dark:border-slate-800">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-slate-500 dark:text-slate-400">Paid via</span>
          <Segmented
            size="sm"
            label="Paid via"
            value={agent.billing}
            onChange={setAsk}
            options={options.map((o) => ({
              id: o,
              label: BILLING_LABELS[o]?.label ?? o,
              title: BILLING_LABELS[o]?.hint,
              disabled: change.isPending,
            }))}
          />
          <span className="text-xs text-slate-500 dark:text-slate-400">{BILLING_LABELS[agent.billing]?.hint}</span>
        </div>
        {ask && ask !== agent.billing && (
          <div className="flex flex-wrap items-center gap-2 rounded-lg bg-slate-50 p-2 text-sm dark:bg-slate-900">
            <span className="min-w-0 flex-1">
              Switch to <b>{BILLING_LABELS[ask]?.label ?? ask}</b> ({BILLING_LABELS[ask]?.hint})? This edits{" "}
              <code>billing:</code> in the agent file, adds a line to its history and commits it. A run that is already
              going keeps its way of paying.
            </span>
            <Button size="sm" variant="ghost" onClick={() => setAsk(null)}>
              Cancel
            </Button>
            <Button
              size="sm"
              variant="primary"
              loading={change.isPending}
              onClick={() => change.mutate(ask, { onSettled: () => setAsk(null) })}
            >
              Switch
            </Button>
          </div>
        )}
        {error && (
          <Notice tone="error">
            {error instanceof ApiError && error.status === 409
              ? "The agent file changed in the meantime – reloaded, please try again."
              : error.message}
          </Notice>
        )}
      </div>
    </Card>
  );
}
