import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Activity, Bot, Map as MapIcon, Play, RefreshCw, Sparkles } from "lucide-react";
import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  Drawer,
  EmptyState,
  KEEP_DRAWER,
  Notice,
  Page,
  PageHeader,
  QueryState,
  SearchInput,
  Segmented,
  Tabs,
} from "../../components/ui";
import { useMediaQuery, useStored } from "../../lib/useMediaQuery";
import {
  type AiAgent,
  type Check,
  type ControlStatus,
  controlQuery,
  type Level,
  type ManagerNote,
  type ProcessHealth,
  type Run,
  runNow,
} from "./api";
import { ActionableDetails, ActionableStrip } from "./Actionable";
import { ControlFlow, WHERE_LABEL } from "./ControlFlow";
import { formatAgo, formatSpan } from "./format";
import { checkLevel, LEVEL_STYLE, originalId } from "./layout";
import { Legend } from "./Legend";
import { RefBadge } from "./RefBadge";
import { filterProcesses, isProblem, runState, sortProcesses, tally } from "./summary";
import { TokenChart } from "./TokenChart";

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "–");

function Dot({ level, className = "size-2.5" }: { level: Level; className?: string }) {
  return <span className={`inline-block shrink-0 rounded-full ${LEVEL_STYLE[level].dot} ${className}`} />;
}

/** A dot that pulses: the worker runs this right now. */
function Pulse({ className = "size-2.5" }: { className?: string }) {
  return (
    <span className={`relative inline-flex shrink-0 ${className}`}>
      <span className="absolute inline-flex size-full animate-ping rounded-full bg-accent-400 opacity-75" />
      <span className="relative inline-flex size-full rounded-full bg-accent-500" />
    </span>
  );
}

/** The process that writes the explanation at the top of the page. */
const EXPLAINER = "control-report";

/**
 * ``selected`` comes from the address (``/control?sel=p:moodle-sync``): ``p:<id>`` a process,
 * ``c:<id>`` a system check, anything else a source, dataset or actionable.
 */
export function ControlPage({ selected, onSelect }: { selected: string | null; onSelect: (id: string | null) => void }) {
  const status = useQuery(controlQuery);
  const queryClient = useQueryClient();
  const wide = useMediaQuery("(min-width: 768px)");
  const [fullscreen, setFullscreen] = useState(false);
  const s = status.data;
  // a finding names a process or a system check (worker, disk)
  const selectByName = (id: string) => onSelect(s?.processes.some((p) => p.id === id) ? `p:${id}` : `c:${id}`);

  return (
    <Page width="xl">
      <PageHeader
        icon={Activity}
        title="Control room"
        actions={
          <Button
            icon={RefreshCw}
            spinIcon
            loading={status.isFetching}
            onClick={() => queryClient.invalidateQueries({ queryKey: ["control"] })}
          >
            Refresh
          </Button>
        }
      >
        {s && <Overall s={s} />}
        {s?.worker?.running && (
          <button
            type="button"
            {...KEEP_DRAWER}
            onClick={() => selectByName(s.worker!.running!.job)}
            className="inline-flex items-center gap-2 rounded-full border border-accent-200 bg-accent-50 px-3 py-1 text-sm text-accent-800 hover:bg-accent-100 dark:border-accent-900 dark:bg-accent-950/40 dark:text-accent-200"
            title="The worker runs one job at a time; the others wait for it"
          >
            <Pulse className="size-2" />
            <span className="text-xs opacity-70">Running now:</span> {s.worker.running.label}
            <span className="text-xs opacity-70">· {formatSpan(s.worker.running.since)}</span>
          </button>
        )}
      </PageHeader>
      {!s ? (
        <QueryState query={status} rows={6} what="the control room" />
      ) : (
        <>
          <SystemStrip s={s} onSelect={(id) => onSelect(`c:${id}`)} />
          <ReportCard s={s} onSelect={selectByName} />
          <ActionableStrip s={s} onSelect={onSelect} />
          {wide ? (
            <div {...KEEP_DRAWER}>
              <ControlFlow status={s} selected={selected} onSelect={onSelect} fullscreen={fullscreen} onFullscreen={setFullscreen} />
            </div>
          ) : (
            <p className="flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:bg-slate-900">
              <MapIcon className="size-4 shrink-0" /> The flow map needs a wider screen; every process is in the list below.
            </p>
          )}
          <Legend />
          <BottomTabs s={s} selected={selected} onSelect={(id) => onSelect(`p:${id}`)} />
          {selected && <Details s={s} id={selected} onSelect={onSelect} onClose={() => onSelect(null)} />}
        </>
      )}
    </Page>
  );
}

function Overall({ s }: { s: ControlStatus }) {
  const style = LEVEL_STYLE[s.level];
  const { fail, warn } = tally(s);
  const text =
    fail + warn === 0
      ? "All green"
      : [fail && `${fail} problem${fail === 1 ? "" : "s"}`, warn && `${warn} warning${warn === 1 ? "" : "s"}`]
          .filter(Boolean)
          .join(", ");
  return (
    <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm ${style.box} ${style.text}`}>
      <Dot level={s.level} />
      {text}
      <span className="text-xs text-slate-500">· checked {formatAgo(s.checkedAt)}</span>
    </span>
  );
}

function ReportCard({ s, onSelect }: { s: ControlStatus; onSelect: (id: string) => void }) {
  const r = s.report;
  if (!r) return null;
  const stale = Date.now() - Date.parse(r.at) > 6 * 3600_000;
  const explainer = s.processes.find((p) => p.id === EXPLAINER);
  const named = (id: string) => s.processes.find((p) => p.id === id) ?? s.system.find((c) => c.id === id);
  return (
    <div {...KEEP_DRAWER} className="rounded-xl border border-accent-200 bg-accent-50 p-4 dark:border-accent-900 dark:bg-accent-950/40">
      <div className="mb-1 flex flex-wrap items-center gap-2 text-sm font-medium">
        <Bot className="size-4 text-accent-600" />
        <button type="button" className="hover:underline" onClick={() => onSelect(EXPLAINER)}>
          {explainer?.label ?? "Explanation"}
        </button>
        <RefBadge value={explainer?.ref} />
        {explainer && <AgentLinks agents={explainer.ai} />}
        <span className="text-xs font-normal text-slate-500">
          {formatAgo(r.at)}
          {stale ? " · may be outdated" : ""}
        </span>
      </div>
      <p className="text-sm whitespace-pre-line">{r.summary}</p>
      {r.findings.length > 0 && (
        <ul className="mt-2 space-y-1.5 text-sm">
          {r.findings.map((f, i) => {
            const what = named(f.process);
            return (
              <li key={`${f.process}:${i}`}>
                <button type="button" className="font-medium text-accent-700 hover:underline dark:text-accent-300" onClick={() => onSelect(f.process)}>
                  {what?.label ?? f.process}
                </button>{" "}
                <RefBadge value={what && "ref" in what ? what.ref : undefined} />: {f.cause}{" "}
                <span className="text-slate-500 dark:text-slate-400">→ {f.action}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/** The system checks (worker, disk, …); every process is drawn in the map below. */
function SystemStrip({ s, onSelect }: { s: ControlStatus; onSelect: (id: string) => void }) {
  return (
    <div {...KEEP_DRAWER} className="flex flex-wrap gap-2">
      {s.system.map((c) => (
        <button
          type="button"
          key={c.id}
          title={c.fix || c.message}
          onClick={() => onSelect(c.id)}
          className={`inline-flex max-w-full items-center gap-2 rounded-lg border px-3 py-1.5 text-left text-xs ${LEVEL_STYLE[checkLevel(c)].box}`}
        >
          <Dot level={checkLevel(c)} className="size-2" />
          <span className="font-medium text-slate-800 dark:text-slate-100">{c.label}</span>
          <span className="truncate text-slate-500 dark:text-slate-400">{c.message}</span>
        </button>
      ))}
    </div>
  );
}

const TABS = ["processes", "tokens"] as const;

function BottomTabs({ s, selected, onSelect }: { s: ControlStatus; selected: string | null; onSelect: (id: string) => void }) {
  const [tab, setTab] = useStored<(typeof TABS)[number]>("lifeos.control.tab", "processes", TABS);
  return (
    <div className="space-y-3">
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "processes", label: "Processes", count: s.processes.length },
          { id: "tokens", label: "Burned tokens" },
        ]}
      />
      {tab === "processes" ? <ProcessList s={s} selected={selected} onSelect={onSelect} /> : <TokenChart />}
    </div>
  );
}

const RUN_COLOR: Record<string, string> = {
  ok: "bg-emerald-400",
  skipped: "bg-slate-300 dark:bg-slate-600",
};

/** The last runs as small bars, oldest left: green ok, grey skipped, red failed. */
function RunStrip({ runs, className = "h-4 w-24" }: { runs: Run[]; className?: string }) {
  if (runs.length === 0) return <span className="text-xs text-slate-400">–</span>;
  return (
    <div className={`flex items-end gap-px ${className}`} aria-label={`last ${runs.length} runs`}>
      {runs.map((h, i) => (
        <span
          key={`${h.at}:${i}`}
          title={`${when(h.at)} · ${h.status}${h.message ? ` · ${h.message}` : ""}`}
          className={`flex-1 rounded-[1px] ${RUN_COLOR[h.status] ?? "bg-red-400"} ${h.status === "skipped" ? "h-1/2" : "h-full"}`}
        />
      ))}
    </div>
  );
}

function ProcessList({ s, selected, onSelect }: { s: ControlStatus; selected: string | null; onSelect: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const [show, setShow] = useStored<"all" | "problems">("lifeos.control.filter", "all", ["all", "problems"]);
  const problems = s.processes.filter(isProblem).length;
  const rows = sortProcesses(filterProcesses(s.processes, { query, problemsOnly: show === "problems" }));
  return (
    <div {...KEEP_DRAWER} className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Segmented
          label="Which processes"
          value={show}
          onChange={setShow}
          options={[
            { id: "all", label: `All (${s.processes.length})` },
            { id: "problems", label: `Problems (${problems})` },
          ]}
        />
        <SearchInput value={query} onChange={setQuery} placeholder="Search processes" className="min-w-40 flex-1 sm:max-w-xs" />
      </div>
      {rows.length === 0 ? (
        <EmptyState title={show === "problems" && !query ? "No problems right now" : "No process matches"}>
          {show === "problems" && !query ? "Every process that is set up ran as expected." : "Try another search word or show all."}
        </EmptyState>
      ) : (
        <>
          {/* phones: one card per process */}
          <ul className="space-y-2 md:hidden">
            {rows.map((p) => (
              <li key={p.id}>
                <button
                  type="button"
                  onClick={() => onSelect(p.id)}
                  className={`w-full rounded-xl border p-3 text-left ${selected === `p:${p.id}` ? "border-accent-400 ring-1 ring-accent-400" : "border-slate-200 dark:border-slate-800"}`}
                >
                  <div className="flex items-center gap-2 text-sm font-medium">
                    {p.runningSince ? <Pulse /> : <Dot level={p.level} />}
                    <span className="min-w-0 flex-1">{p.label}</span>
                    <RefBadge value={p.ref} />
                  </div>
                  <div className={`mt-1 text-xs ${LEVEL_STYLE[p.level].text}`}>{p.reason}</div>
                  <div className="mt-2 flex items-center gap-3 text-xs text-slate-500">
                    <span className="min-w-0 flex-1 truncate">{p.trigger}</span>
                    <RunStrip runs={p.history} className="h-3 w-20" />
                  </div>
                </button>
              </li>
            ))}
          </ul>
          <div className="hidden overflow-x-auto rounded-xl border border-slate-200 md:block dark:border-slate-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-900">
                <tr>
                  <th className="px-3 py-2 font-medium">ID</th>
                  <th className="px-3 py-2 font-medium">Process</th>
                  <th className="px-3 py-2 font-medium">Agent</th>
                  <th className="px-3 py-2 font-medium">Runs</th>
                  <th className="px-3 py-2 font-medium">Last runs</th>
                  <th className="px-3 py-2 font-medium">Last success</th>
                  <th className="px-3 py-2 font-medium">State</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr
                    key={p.id}
                    onClick={() => onSelect(p.id)}
                    className={`cursor-pointer border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-900 ${
                      selected === `p:${p.id}` ? "bg-accent-50 dark:bg-accent-950/30" : ""
                    }`}
                  >
                    <td className="px-3 py-2 whitespace-nowrap">
                      <RefBadge value={p.ref} />
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-2 font-medium">
                        {p.runningSince ? <Pulse /> : <Dot level={p.level} />} {p.label}
                      </div>
                    </td>
                    <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                      {p.ai.length > 0 ? <AgentLinks agents={p.ai} /> : <span className="text-slate-400">–</span>}
                    </td>
                    <td className="px-3 py-2 text-slate-500">
                      <div>{WHERE_LABEL[p.where]}</div>
                      <div className="text-xs">{p.trigger}</div>
                    </td>
                    <td className="px-3 py-2">
                      <RunStrip runs={p.history} />
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap text-slate-500">{formatAgo(p.lastSuccess)}</td>
                    <td className={`px-3 py-2 ${LEVEL_STYLE[p.level].text}`}>
                      {p.runningSince && p.level !== "ok" ? `running for ${formatSpan(p.runningSince)} · ` : ""}
                      {p.reason}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function Details({
  s,
  id,
  onSelect,
  onClose,
}: {
  s: ControlStatus;
  id: string;
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  const process = id.startsWith("p:") ? s.processes.find((p) => `p:${p.id}` === id) : undefined;
  const check = id.startsWith("c:") ? s.system.find((c) => `c:${c.id}` === id) : undefined;
  const nodeId = originalId(id);
  const node = process || check ? undefined : s.nodes.find((n) => n.id === nodeId);
  const kind = process ? "Process" : check ? "System check" : node ? node.kind[0].toUpperCase() + node.kind.slice(1) : "Details";
  return (
    <Drawer label={kind} onClose={onClose}>
      {process ? (
        // keyed: the state of "Run now" belongs to this process, not to the panel
        <ProcessDetails key={process.id} s={s} p={process} />
      ) : check ? (
        <CheckDetails s={s} check={check} />
      ) : node?.kind === "actionable" ? (
        <ActionableDetails s={s} node={node} />
      ) : node ? (
        <div className="space-y-3">
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            {node.kind === "dataset" && <Dot level={node.level} />} {node.label} <RefBadge value={node.ref} />
          </h2>
          <p className="text-sm text-slate-500">{node.detail}</p>
          {node.reason && <p className={`text-sm ${LEVEL_STYLE[node.level].text}`}>{node.reason}</p>}
          <ul className="space-y-1 text-sm">
            {s.processes
              .filter((p) => p.inputs.includes(node.id) || p.outputs.includes(node.id))
              .map((p) => (
                <li key={p.id} className="flex items-center gap-2">
                  <Dot level={p.level} className="size-2" />
                  <button type="button" className="text-left hover:underline" onClick={() => onSelect(`p:${p.id}`)}>
                    {p.label}
                  </button>
                  <RefBadge value={p.ref} />
                  <span className="text-xs text-slate-500">{p.outputs.includes(node.id) ? "writes here" : "reads here"}</span>
                </li>
              ))}
          </ul>
        </div>
      ) : (
        <EmptyState title="Not on this page any more">
          <code>{id}</code> is not part of the control room (any longer).
        </EmptyState>
      )}
    </Drawer>
  );
}

function FindingBox({ s, id }: { s: ControlStatus; id: string }) {
  const finding = s.report?.findings.find((f) => f.process === id);
  if (!finding) return null;
  return (
    <div className="rounded-lg border border-accent-200 bg-accent-50 p-3 text-sm dark:border-accent-900 dark:bg-accent-950/40">
      <div className="mb-1 flex items-center gap-1.5 font-medium">
        <Bot className="size-4 text-accent-600" /> {s.processes.find((x) => x.id === EXPLAINER)?.label ?? "Explanation"}
      </div>
      <div>{finding.cause}</div>
      <div className="mt-1 text-slate-600 dark:text-slate-300">→ {finding.action}</div>
    </div>
  );
}

function CheckDetails({ s, check }: { s: ControlStatus; check: Check }) {
  const level = checkLevel(check);
  const style = LEVEL_STYLE[level];
  const note = level === "ok" ? undefined : s.manager?.[check.id];
  return (
    <div className="space-y-4">
      <h2 className="flex items-center gap-2 text-lg font-semibold">
        <Dot level={level} /> {check.label}
      </h2>
      <div className={`rounded-lg border p-3 text-sm ${style.box}`}>
        <div className={`font-medium ${style.text}`}>{style.label}</div>
        <div className="break-words">{check.message}</div>
      </div>
      {check.fix && <p className="rounded-lg bg-slate-100 p-3 text-sm break-words dark:bg-slate-900">{check.fix}</p>}
      {note && <ManagerNoteBox note={note} />}
      <FindingBox s={s} id={check.id} />
    </div>
  );
}

function ProcessDetails({ s, p }: { s: ControlStatus; p: ProcessHealth }) {
  const queryClient = useQueryClient();
  const run = useMutation({
    mutationFn: () => runNow(p),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["control"] }),
  });
  const state = runState(p, s);
  const managerNote = p.level === "ok" ? undefined : s.manager?.[p.id];
  const style = LEVEL_STYLE[p.level];
  const waiting = state.kind === "running" || state.kind === "queued";
  const runLabel =
    state.kind === "running"
      ? `Running for ${formatSpan(state.since)}…`
      : state.kind === "queued"
        ? state.behind
          ? "Queued – the worker is busy"
          : "Queued for the worker…"
        : run.isPending
          ? p.canRequest
            ? "Asking…"
            : "Running…"
          : "Run now";
  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          {p.runningSince ? <Pulse /> : <Dot level={p.level} />} {p.label} <RefBadge value={p.ref} />
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          {p.ai.length > 0 && <AiChip />}
          {p.mirror && <Badge title="Hand edits in the dataset are overwritten by the next run">mirror</Badge>}
          <span className="text-sm text-slate-500">
            {WHERE_LABEL[p.where]} · {p.trigger}
          </span>
        </div>
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
        <div className="break-words">{p.reason}</div>
      </div>
      {p.enabled && (p.canRequest || p.runPath) && (
        <div className="space-y-1.5">
          <Button icon={Play} variant="primary" onClick={() => run.mutate()} loading={run.isPending} disabled={waiting}>
            {runLabel}
          </Button>
          {run.error && <Notice tone="error">{run.error.message}</Notice>}
          {state.kind === "stuck" && (
            <Notice tone="warn">
              Asked {formatAgo(state.since)}, but the worker has not picked it up. Is the worker running? You can ask
              again.
            </Notice>
          )}
          {state.kind === "queued" && state.behind && (
            <p className="text-xs text-slate-500">
              The worker runs one job at a time and is busy with “{state.behind}”; this one follows when it is free.
            </p>
          )}
          {state.kind === "idle" && p.canRequest && (
            <p className="text-xs text-slate-500">The worker picks it up within 30 seconds.</p>
          )}
        </div>
      )}
      {p.ai.length > 0 && <AiDetails agents={p.ai} />}
      {managerNote && <ManagerNoteBox note={managerNote} />}
      <FindingBox s={s} id={p.id} />
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
          <RunStrip runs={p.history} className="h-5 w-full" />
          <div className="mt-0.5 flex justify-between text-[10px] text-slate-400">
            <span>{formatAgo(p.history[0].at)}</span>
            <span>{formatAgo(p.history[p.history.length - 1].at)}</span>
          </div>
        </div>
      )}
      {p.hint && <p className="rounded-lg bg-slate-100 p-3 text-sm break-words dark:bg-slate-900">{p.hint}</p>}
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
      {note.cause && <div className="break-words">{note.cause}</div>}
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
    <Badge tone="ai" icon={Sparkles}>
      AI
    </Badge>
  );
}

const BILLING: Record<string, string> = { subscription: "Claude subscription", api: "API, billed per token" };

function AiDetails({ agents }: { agents: AiAgent[] }) {
  return (
    <Card className="!border-violet-200 !bg-violet-50 text-sm dark:!border-violet-900 dark:!bg-violet-950/30">
      <div>
        <div className="flex items-center gap-1.5 font-medium">
          <Sparkles className="size-4 text-violet-500" /> Calls an AI model
        </div>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          The schedule only checks for work; the model is called when there is something to do.
        </p>
      </div>
      {agents.length === 0 && <p className="text-xs text-slate-500">No agent set up.</p>}
      <ul className="space-y-2">
        {agents.map((a) => (
          <li key={a.agent}>
            <div className="font-medium">
              <AgentLink agent={a} /> <span className="font-normal text-slate-500">· agents/{a.agent}.md</span>
            </div>
            {a.configured ? (
              <div className="text-xs text-slate-600 dark:text-slate-300">
                {a.model} · {BILLING[a.billing ?? ""] ?? a.billing} · {a.runsToday} run(s) today
                {a.dailyLimit !== null ? ` of max. ${a.dailyLimit}` : ""}
              </div>
            ) : (
              <div className="text-xs text-amber-600">agent file missing or unreadable – the process skips the AI</div>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** An agent's title, linking to its card on the agents page. */
function AgentLink({ agent }: { agent: AiAgent }) {
  if (!agent.configured) return <span>{agent.title}</span>;
  const exhausted = agent.dailyLimit !== null && agent.runsToday >= agent.dailyLimit;
  return (
    <Link
      to="/agents"
      hash={agent.agent}
      title={exhausted ? `Daily limit reached (${agent.runsToday} of ${agent.dailyLimit})` : undefined}
      className="inline-flex items-center gap-1 text-violet-700 hover:underline dark:text-violet-300"
    >
      {agent.title}
      {exhausted && <Badge tone="warn">limit</Badge>}
    </Link>
  );
}

function AgentLinks({ agents }: { agents: AiAgent[] }) {
  if (agents.length === 0) return <span className="text-xs text-amber-600">no agent set up</span>;
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm font-normal">
      <Sparkles className="size-3.5 shrink-0 text-violet-500" />
      {agents.map((a) => (
        <AgentLink key={a.agent} agent={a} />
      ))}
    </span>
  );
}
