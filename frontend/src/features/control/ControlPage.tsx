import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Bot, Play, RefreshCw, Sparkles, X } from "lucide-react";
import { useState } from "react";
import {
  type AiAgent,
  type ControlStatus,
  controlQuery,
  type Level,
  type ManagerNote,
  type ProcessHealth,
  runNow,
} from "./api";
import { ControlFlow, WHERE_LABEL } from "./ControlFlow";
import { ActionableDetails, ActionableStrip } from "./Actionable";
import { formatAgo } from "./format";
import { Legend } from "./Legend";
import { TokenChart } from "./TokenChart";
import { checkLevel, LEVEL_RANK, LEVEL_STYLE, originalId } from "./layout";

const btn =
  "inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100 disabled:opacity-40 dark:border-slate-700 dark:hover:bg-slate-800";

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "–");

function Dot({ level, className = "size-2.5" }: { level: Level; className?: string }) {
  return <span className={`inline-block shrink-0 rounded-full ${LEVEL_STYLE[level].dot} ${className}`} />;
}

export function ControlPage() {
  const status = useQuery(controlQuery);
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const s = status.data;

  return (
    <div className="mx-auto max-w-7xl space-y-5 p-4 sm:p-8">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="flex items-center gap-2 text-2xl font-semibold">
          <Activity className="size-7 text-sky-600" /> Control room
        </h1>
        {s && <Overall s={s} />}
        <button
          className={`${btn} ml-auto`}
          onClick={() => queryClient.invalidateQueries({ queryKey: ["control"] })}
          disabled={status.isFetching}
        >
          <RefreshCw className={`size-4 ${status.isFetching ? "animate-spin" : ""}`} /> Refresh
        </button>
      </div>
      <p className="text-sm text-slate-500">
        Every process that feeds LifeOS: where it runs, when it last succeeded and whether it ran often enough. Red
        means failed, a missing precondition or overdue; yellow means late or skipped. Hover a box to follow its flow,
        click it for details.
      </p>
      {!s ? (
        <p className="text-sm text-slate-400">{status.error ? status.error.message : "Loading…"}</p>
      ) : (
        <>
          <ReportCard s={s} onSelect={(id) => setSelected(`p:${id}`)} />
          <ActionableStrip s={s} onSelect={setSelected} />
          <SystemStrip s={s} onSelect={(id) => setSelected(`p:${id}`)} />
          <div className="hidden md:block">
            <ControlFlow status={s} selected={selected} onSelect={setSelected} />
          </div>
          <Legend />
          <BottomTabs s={s} onSelect={(id) => setSelected(`p:${id}`)} />
          {selected && <Panel s={s} id={selected} onClose={() => setSelected(null)} />}
        </>
      )}
    </div>
  );
}

function Overall({ s }: { s: ControlStatus }) {
  const style = LEVEL_STYLE[s.level];
  const problems = s.counts.fail + s.counts.warn;
  return (
    <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm ${style.box} ${style.text}`}>
      <Dot level={s.level} />
      {problems === 0
        ? "All green"
        : [s.counts.fail && `${s.counts.fail} problem(s)`, s.counts.warn && `${s.counts.warn} warning(s)`]
            .filter(Boolean)
            .join(", ")}
      <span className="text-xs text-slate-500">· checked {formatAgo(s.checkedAt)}</span>
    </span>
  );
}

function ReportCard({ s, onSelect }: { s: ControlStatus; onSelect: (id: string) => void }) {
  const r = s.report;
  if (!r) return null;
  const stale = Date.now() - Date.parse(r.at) > 6 * 3600_000;
  return (
    <div className="rounded-xl border border-sky-200 bg-sky-50 p-4 dark:border-sky-900 dark:bg-sky-950/40">
      <div className="mb-1 flex items-center gap-2 text-sm font-medium">
        <Bot className="size-4 text-sky-600" /> AI diagnosis
        <span className="text-xs font-normal text-slate-500">
          {formatAgo(r.at)}
          {stale ? " · may be outdated" : ""}
        </span>
      </div>
      <p className="whitespace-pre-line text-sm">{r.summary}</p>
      {r.findings.length > 0 && (
        <ul className="mt-2 space-y-1.5 text-sm">
          {r.findings.map((f) => (
            <li key={f.process + f.cause}>
              <button className="font-medium text-sky-700 hover:underline dark:text-sky-300" onClick={() => onSelect(f.process)}>
                {s.processes.find((p) => p.id === f.process)?.label ?? f.process}
              </button>
              : {f.cause} <span className="text-slate-500">→ {f.action}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SystemStrip({ s, onSelect }: { s: ControlStatus; onSelect: (id: string) => void }) {
  const helpers = s.processes.filter((p) => p.system && p.enabled);
  return (
    <div className="flex flex-wrap gap-2">
      {s.system.map((c) => (
        <span
          key={c.id}
          title={c.fix || c.message}
          className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs ${LEVEL_STYLE[checkLevel(c)].box}`}
        >
          <Dot level={checkLevel(c)} className="size-2" />
          <span className="font-medium">{c.label}</span>
          <span className="text-slate-500">{c.message}</span>
        </span>
      ))}
      {helpers.map((p) => (
        <button
          key={p.id}
          onClick={() => onSelect(p.id)}
          className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs ${LEVEL_STYLE[p.level].box}`}
        >
          <Dot level={p.level} className="size-2" />
          <span className="font-medium">{p.label}</span>
          <span className="text-slate-500">{p.reason}</span>
        </button>
      ))}
    </div>
  );
}

export function sortProcesses(list: ProcessHealth[]): ProcessHealth[] {
  return [...list].sort((a, b) => LEVEL_RANK[b.level] - LEVEL_RANK[a.level] || a.label.localeCompare(b.label));
}

type Tab = "processes" | "tokens";
const TAB_KEY = "lifeos.control.tab";

function savedTab(): Tab {
  try {
    return localStorage.getItem(TAB_KEY) === "tokens" ? "tokens" : "processes";
  } catch {
    return "processes";
  }
}

function BottomTabs({ s, onSelect }: { s: ControlStatus; onSelect: (id: string) => void }) {
  const [tab, setTab] = useState<Tab>(savedTab);
  const choose = (next: Tab) => {
    setTab(next);
    try {
      localStorage.setItem(TAB_KEY, next);
    } catch {
      // private window: the choice only lasts for this visit
    }
  };
  const tabs: { id: Tab; label: string }[] = [
    { id: "processes", label: "Processes" },
    { id: "tokens", label: "Burned tokens" },
  ];
  return (
    <div className="space-y-3">
      <div role="tablist" className="flex gap-1 border-b border-slate-200 dark:border-slate-800">
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => choose(t.id)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${
              tab === t.id
                ? "border-sky-600 text-sky-700 dark:text-sky-400"
                : "border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "processes" ? <ProcessTable s={s} onSelect={onSelect} /> : <TokenChart />}
    </div>
  );
}

function ProcessTable({ s, onSelect }: { s: ControlStatus; onSelect: (id: string) => void }) {
  const rows = sortProcesses(s.processes);
  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800">
      <table className="w-full text-left text-sm">
        <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-900">
          <tr>
            <th className="px-3 py-2">Process</th>
            <th className="px-3 py-2">Runs</th>
            <th className="px-3 py-2">Last success</th>
            <th className="px-3 py-2">State</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr
              key={p.id}
              onClick={() => onSelect(p.id)}
              className="cursor-pointer border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-900"
            >
              <td className="px-3 py-2">
                <div className="flex items-center gap-2 font-medium">
                  <Dot level={p.level} /> {p.label}
                  {p.ai.length > 0 && <AiChip />}
                </div>
              </td>
              <td className="px-3 py-2 text-slate-500">
                <div>{WHERE_LABEL[p.where]}</div>
                <div className="text-xs">{p.trigger}</div>
              </td>
              <td className="px-3 py-2 whitespace-nowrap text-slate-500">{formatAgo(p.lastSuccess)}</td>
              <td className={`px-3 py-2 ${LEVEL_STYLE[p.level].text}`}>{p.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Panel({ s, id, onClose }: { s: ControlStatus; id: string; onClose: () => void }) {
  const process = id.startsWith("p:") ? s.processes.find((p) => `p:${p.id}` === id) : undefined;
  const nodeId = originalId(id);
  const node = s.nodes.find((n) => n.id === nodeId);
  return (
    <div className="fixed inset-y-0 right-0 z-20 w-full max-w-md overflow-y-auto border-l border-slate-200 bg-white p-5 shadow-xl dark:border-slate-800 dark:bg-slate-950">
      <button onClick={onClose} className="float-right rounded p-1 hover:bg-slate-100 dark:hover:bg-slate-800" title="Close">
        <X className="size-5" />
      </button>
      {process ? (
        <ProcessDetails s={s} p={process} />
      ) : node?.kind === "actionable" ? (
        <ActionableDetails s={s} node={node} />
      ) : node ? (
        <div className="space-y-3">
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            {node.kind === "dataset" && <Dot level={node.level} />} {node.label}
          </h2>
          <p className="text-sm text-slate-500">
            {node.kind === "source" ? "Source" : "Target"} · {node.detail}
          </p>
          {node.reason && <p className={`text-sm ${LEVEL_STYLE[node.level].text}`}>{node.reason}</p>}
          <ul className="space-y-1 text-sm">
            {s.processes
              .filter((p) => p.inputs.includes(node.id) || p.outputs.includes(node.id))
              .map((p) => (
                <li key={p.id} className="flex items-center gap-2">
                  <Dot level={p.level} className="size-2" /> {p.label}
                  <span className="text-xs text-slate-500">{p.outputs.includes(node.id) ? "writes here" : "reads here"}</span>
                </li>
              ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function ProcessDetails({ s, p }: { s: ControlStatus; p: ProcessHealth }) {
  const queryClient = useQueryClient();
  const run = useMutation({
    mutationFn: () => runNow(p),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["control"] }),
  });
  const requested = s.requests[p.id];
  const finding = s.report?.findings.find((f) => f.process === p.id);
  const managerNote = p.level === "ok" ? undefined : s.manager?.[p.id];
  const style = LEVEL_STYLE[p.level];
  return (
    <div className="space-y-4">
      <div>
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <Dot level={p.level} /> {p.label}
        </h2>
        {p.ai.length > 0 && <AiChip />}
        <p className="text-sm text-slate-500">
          {WHERE_LABEL[p.where]} · {p.trigger}
          {p.mirror ? " · mirror (hand edits in the dataset are overwritten)" : ""}
        </p>
      </div>
      <p className="text-sm">{p.description}</p>
      {p.reactsTo.length > 0 && (
        <p className="rounded-lg border border-dashed border-violet-300 p-2 text-sm text-violet-700 dark:border-violet-800 dark:text-violet-300">
          Reacts to you: {p.reaction || "your changes"} on{" "}
          {p.reactsTo.map((a) => s.nodes.find((n) => n.id === a)?.label ?? a).join(", ")}
        </p>
      )}
      <div className={`rounded-lg border p-3 text-sm ${style.box}`}>
        <div className={`font-medium ${style.text}`}>{style.label}</div>
        <div>{p.reason}</div>
      </div>
      {p.ai.length > 0 && <AiDetails agents={p.ai} />}
      {managerNote && <ManagerNoteBox note={managerNote} />}
      {finding && (
        <div className="rounded-lg border border-sky-200 bg-sky-50 p-3 text-sm dark:border-sky-900 dark:bg-sky-950/40">
          <div className="mb-1 flex items-center gap-1.5 font-medium">
            <Bot className="size-4 text-sky-600" /> AI diagnosis
          </div>
          <div>{finding.cause}</div>
          <div className="mt-1 text-slate-600 dark:text-slate-300">→ {finding.action}</div>
        </div>
      )}
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-slate-500">Last run</dt>
        <dd>
          {when(p.lastRun)} {p.status ? `(${p.status})` : ""}
        </dd>
        <dt className="text-slate-500">Last success</dt>
        <dd>{when(p.lastSuccess)}</dd>
        {p.message && (
          <>
            <dt className="text-slate-500">Message</dt>
            <dd className="break-words">{p.message}</dd>
          </>
        )}
        {p.app && (
          <>
            <dt className="text-slate-500">App on the PC</dt>
            <dd className="break-words">
              <span className={p.app.behind ? "text-amber-700 dark:text-amber-400" : ""}>
                {p.app.version ?? "unknown"}
                {p.app.serverVersion && ` (server ${p.app.serverVersion})`}
              </span>
              {p.app.update && <span className="block text-xs text-slate-500">{p.app.update}</span>}
            </dd>
          </>
        )}
      </dl>
      {p.checks.length > 0 && (
        <div>
          <h3 className="mb-1 text-sm font-medium">Preconditions</h3>
          <ul className="space-y-1 text-sm">
            {p.checks.map((c) => (
              <li key={c.id} className="flex items-start gap-2">
                <Dot level={checkLevel(c)} className="mt-1.5 size-2" />
                <span>
                  {c.label}: {c.message}
                  {c.fix && <span className="block text-xs text-slate-500">{c.fix}</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {p.history.length > 0 && (
        <div>
          <h3 className="mb-1 text-sm font-medium">Recent runs</h3>
          <div className="flex gap-0.5">
            {p.history.map((h) => (
              <span
                key={h.at}
                title={`${when(h.at)} · ${h.status}${h.message ? ` · ${h.message}` : ""}`}
                className={`h-5 flex-1 rounded-sm ${
                  h.status === "ok" ? "bg-emerald-400" : h.status === "skipped" ? "bg-slate-300 dark:bg-slate-600" : "bg-red-400"
                }`}
              />
            ))}
          </div>
        </div>
      )}
      {p.enabled && (p.canRequest || p.runPath) && (
        <div className="space-y-1">
          <button className={btn} onClick={() => run.mutate()} disabled={run.isPending || !!requested}>
            <Play className="size-4" /> {requested ? "Queued for the worker…" : run.isPending ? "Running…" : "Run now"}
          </button>
          {run.error && <p className="text-sm text-red-600">{run.error.message}</p>}
          {p.canRequest && <p className="text-xs text-slate-500">The worker picks it up within 30 seconds.</p>}
        </div>
      )}
      {p.hint && <p className="rounded-lg bg-slate-100 p-3 text-sm dark:bg-slate-900">{p.hint}</p>}
    </div>
  );
}

function ManagerNoteBox({ note }: { note: ManagerNote }) {
  const tries = note.attempts > 0 ? ` · attempt ${note.attempts}` : "";
  let next = "not explained yet – the manager looks at it";
  if (note.expect === "stays") next = "waits for the open ticket";
  else if (note.until) next = `checks again ${when(note.until)}`;
  return (
    <div className="rounded-lg border border-violet-200 bg-violet-50 p-3 text-sm dark:border-violet-900 dark:bg-violet-950/40">
      <div className="mb-1 flex items-center gap-1.5 font-medium">
        <Bot className="size-4 text-violet-600" /> LifeOS manager
      </div>
      {note.cause && <div>{note.cause}</div>}
      <div className="mt-1 text-slate-600 dark:text-slate-300">
        → {next}
        {tries}
        {note.ticket && (
          <>
            {" · "}
            <a href={note.ticket} target="_blank" rel="noreferrer" className="underline">
              ticket
            </a>
          </>
        )}
      </div>
    </div>
  );
}

export function AiChip() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-violet-100 px-2 py-0.5 text-[11px] font-medium text-violet-700 dark:bg-violet-900/40 dark:text-violet-300">
      <Sparkles className="size-3" /> AI
    </span>
  );
}

const BILLING: Record<string, string> = { subscription: "Claude subscription", api: "API, billed per token" };

function AiDetails({ agents }: { agents: AiAgent[] }) {
  return (
    <div className="rounded-lg border border-violet-200 bg-violet-50 p-3 text-sm dark:border-violet-900 dark:bg-violet-950/30">
      <div className="mb-1 flex items-center gap-1.5 font-medium">
        <Sparkles className="size-4 text-violet-500" /> Calls an AI model
      </div>
      <p className="mb-2 text-xs text-slate-500">
        The schedule only checks for work; the model is called when there is something to do.
      </p>
      {agents.length === 0 && <p className="text-xs text-slate-500">No agent set up.</p>}
      <ul className="space-y-2">
        {agents.map((a) => (
          <li key={a.agent}>
            <div className="font-medium">
              {a.title} <span className="font-normal text-slate-500">· agents/{a.agent}.md</span>
            </div>
            {a.configured ? (
              <div className="text-xs text-slate-600 dark:text-slate-300">
                {a.model} · {BILLING[a.billing ?? ""] ?? a.billing} · {a.runsToday} run(s) today
                {a.dailyLimit !== null ? ` of max. ${a.dailyLimit}` : ""}
              </div>
            ) : (
              <div className="text-xs text-amber-600">agent file missing – the process skips the AI</div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
