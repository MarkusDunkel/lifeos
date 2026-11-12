import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleDot, GitPullRequestArrow, LayoutGrid, List, Plus, RefreshCw, Search, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, qs } from "../../lib/api";
import { formatTime } from "../workspaces/review";
import {
  createTicket,
  type Meta,
  runSync,
  saveTicket,
  type SyncReport,
  type TicketDoc,
  ticketsQuery,
  type TicketSummary,
} from "./api";
import { boardField, columnOf, filterTickets, groupTickets, isPending, ticketLabel, type TicketView } from "./board";
import { ChangesPanel } from "./ChangesPanel";
import { TicketEditor } from "./TicketEditor";

type Props = { search: TicketView; onChange: (next: TicketView, replace?: boolean) => void };

const btn =
  "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-40";
const select =
  "rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900";

export function TicketsPage({ search, onChange }: Props) {
  const queryClient = useQueryClient();
  const { data, isError, error } = useQuery(ticketsQuery);
  const [q, setQ] = useState(search.q ?? "");
  const [syncing, setSyncing] = useState(false);
  const [report, setReport] = useState<SyncReport | { error: string } | null>(null);

  useEffect(() => setQ(search.q ?? ""), [search.q]);
  useEffect(() => {
    const t = setTimeout(() => {
      if ((search.q ?? "") !== q) onChange({ ...search, q: q || undefined }, true);
    }, 200);
    return () => clearTimeout(t);
  }, [q, search, onChange]);

  const meta = data?.meta;
  const field = meta ? boardField(meta) : null;
  const visible = useMemo(() => (data ? filterTickets(data.tickets, search) : []), [data, search]);
  const pending = data?.tickets.filter(isPending).length ?? 0;
  const multiRepo = (meta?.repos.length ?? 0) > 1;

  const sync = async () => {
    setSyncing(true);
    try {
      setReport(await runSync());
    } catch (e) {
      setReport({ error: e instanceof Error ? e.message : String(e) });
    } finally {
      setSyncing(false);
      await queryClient.invalidateQueries({ queryKey: ["tickets"] });
    }
  };

  const newTicket = async () => {
    if (!meta) return;
    const repo = search.repo ?? (meta.repos.length === 1 ? meta.repos[0] : window.prompt(`Repository (${meta.repos.join(", ")}):`, meta.repos[0]));
    if (!repo) return;
    const title = window.prompt("Title of the new ticket:");
    if (!title?.trim()) return;
    try {
      const doc = await createTicket(repo, title);
      await queryClient.invalidateQueries({ queryKey: ["tickets", "list"] });
      onChange({ ...search, ticket: doc.path, changes: undefined });
    } catch (e) {
      window.alert(e instanceof Error ? e.message : String(e));
    }
  };

  /** Drag & drop on the board: set the board field (or the state) and save right away. */
  const move = async (path: string, column: string | null) => {
    const fresh = await api<TicketDoc>(`/api/tickets/file?${qs({ path })}`);
    const fields = field ? { project: { ...fresh.project, [field]: column } } : { state: (column ?? "open") as TicketDoc["state"] };
    try {
      await saveTicket(path, fields, fresh.revision);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : String(e));
    }
    await queryClient.invalidateQueries({ queryKey: ["tickets"] });
  };

  if (isError) return <p className="p-6 text-red-600">Could not load tickets: {String(error)}</p>;
  if (!data || !meta) return <p className="p-6 text-slate-400">Loading…</p>;

  const panelOpen = Boolean(search.ticket || search.changes);

  return (
    <div className="flex h-full min-h-0">
      <div className={`flex min-w-0 flex-1 flex-col ${panelOpen ? "hidden lg:flex" : ""}`}>
        <div className="space-y-2 border-b border-slate-200 p-3 dark:border-slate-800">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-40 flex-1">
              <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-slate-400" />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search tickets"
                className="w-full rounded-lg border border-slate-200 bg-white py-2 pr-3 pl-8 text-sm focus:border-sky-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900"
              />
            </div>
            <button onClick={sync} disabled={syncing || !meta.configured} className={`${btn} ring-1 ring-slate-200 hover:bg-slate-100 dark:ring-slate-700 dark:hover:bg-slate-800`} title="Fetch from GitHub and merge">
              <RefreshCw className={`size-4 ${syncing ? "animate-spin" : ""}`} /> Sync
            </button>
            <button
              onClick={() => onChange({ ...search, changes: search.changes ? undefined : true, ticket: undefined })}
              className={`${btn} ${pending ? "bg-amber-500 text-white hover:bg-amber-600" : "ring-1 ring-slate-200 hover:bg-slate-100 dark:ring-slate-700 dark:hover:bg-slate-800"}`}
            >
              <GitPullRequestArrow className="size-4" /> Changes{pending ? ` (${pending})` : ""}
            </button>
            <button onClick={newTicket} disabled={meta.repos.length === 0} className={`${btn} bg-sky-600 text-white hover:bg-sky-700`}>
              <Plus className="size-4" /> New
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <div className="inline-flex rounded-lg ring-1 ring-slate-200 dark:ring-slate-700">
              {(["board", "list"] as const).map((v) => (
                <button
                  key={v}
                  onClick={() => onChange({ ...search, view: v === "board" ? undefined : v }, true)}
                  className={`inline-flex items-center gap-1 px-2.5 py-1.5 first:rounded-l-lg last:rounded-r-lg ${
                    (search.view ?? "board") === v ? "bg-slate-100 font-medium dark:bg-slate-800" : "text-slate-500"
                  }`}
                >
                  {v === "board" ? <LayoutGrid className="size-4" /> : <List className="size-4" />} {v === "board" ? "Board" : "List"}
                </button>
              ))}
            </div>
            <select value={search.state ?? "open"} onChange={(e) => onChange({ ...search, state: e.target.value === "open" ? undefined : (e.target.value as TicketView["state"]) }, true)} className={select} aria-label="State">
              <option value="open">Open</option>
              <option value="closed">Closed</option>
              <option value="all">All</option>
            </select>
            {multiRepo && (
              <select value={search.repo ?? ""} onChange={(e) => onChange({ ...search, repo: e.target.value || undefined }, true)} className={select} aria-label="Repository">
                <option value="">All repositories</option>
                {meta.repos.map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
            )}
            <span className="ml-auto text-xs text-slate-400">
              {meta.synced_at ? `Synced ${formatTime(meta.synced_at)}` : meta.configured ? "Not synced yet" : ""}
            </span>
          </div>
          {report && <SyncNotice report={report} onClose={() => setReport(null)} />}
          {!meta.configured && (
            <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-600 dark:bg-slate-900 dark:text-slate-300">
              GitHub is not configured. Set <code>LIFEOS_GITHUB_TOKEN</code>, <code>LIFEOS_GITHUB_REPOS</code> and optionally{" "}
              <code>LIFEOS_GITHUB_PROJECT</code> in <code>deploy/.env</code> and restart.
            </p>
          )}
          {data.broken.length > 0 && (
            <p className="text-sm text-red-600">Unreadable ticket files: {data.broken.map((b) => `${b.path} (${b.error})`).join("; ")}</p>
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          {(search.view ?? "board") === "board" ? (
            <Board tickets={visible} meta={meta} field={field} showRepo={multiRepo} selected={search.ticket} onOpen={(p) => onChange({ ...search, ticket: p, changes: undefined })} onMove={move} />
          ) : (
            <TicketList tickets={visible} field={field} showRepo={multiRepo} selected={search.ticket} onOpen={(p) => onChange({ ...search, ticket: p, changes: undefined })} />
          )}
        </div>
      </div>

      {panelOpen && (
        <aside className="w-full min-w-0 border-slate-200 lg:w-[34rem] lg:shrink-0 lg:border-l dark:border-slate-800">
          {search.ticket ? (
            <TicketEditor key={search.ticket} path={search.ticket} meta={meta} onClose={() => onChange({ ...search, ticket: undefined })} />
          ) : (
            <ChangesPanel data={data} onOpen={(p) => onChange({ ...search, ticket: p, changes: undefined })} onClose={() => onChange({ ...search, changes: undefined })} />
          )}
        </aside>
      )}
    </div>
  );
}

function SyncNotice({ report, onClose }: { report: SyncReport | { error: string }; onClose: () => void }) {
  const failed =
    "error" in report || report.errors.length > 0 || report.conflicts.length > 0 || report.pending.length > 0;
  const text =
    "error" in report
      ? report.error
      : [
          `${report.created} new, ${report.updated} updated, ${report.unchanged} unchanged.`,
          report.pending.length
            ? `${report.pending.length} local change(s) kept — not on GitHub yet (see “Changes”, or revert them).`
            : "",
          report.conflicts.length ? `Conflicts: ${report.conflicts.join(", ")}.` : "",
          report.errors.join(" "),
        ]
          .filter(Boolean)
          .join(" ");
  return (
    <div className={`flex items-start gap-2 rounded-lg px-3 py-2 text-sm ${failed ? "bg-amber-50 text-amber-900 dark:bg-amber-950 dark:text-amber-200" : "bg-emerald-50 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200"}`}>
      <span className="flex-1">{text}</span>
      <button onClick={onClose} className="text-xs underline">
        OK
      </button>
    </div>
  );
}

function Card({ t, showRepo, selected, onOpen }: { t: TicketSummary; showRepo: boolean; selected: boolean; onOpen: () => void }) {
  return (
    <button
      draggable
      onDragStart={(e) => e.dataTransfer.setData("text/plain", t.path)}
      onClick={onOpen}
      className={`block w-full rounded-lg border bg-white p-2.5 text-left text-sm shadow-sm hover:border-sky-400 dark:bg-slate-900 ${
        selected ? "border-sky-500" : "border-slate-200 dark:border-slate-700"
      }`}
    >
      <div className="mb-1 flex items-center gap-1.5 text-xs text-slate-400">
        <span>{ticketLabel(t, showRepo)}</span>
        {t.state === "closed" && <span className="rounded bg-slate-100 px-1 dark:bg-slate-800">closed</span>}
        <span className="flex-1" />
        {t.conflicts.length > 0 && <TriangleAlert className="size-3.5 text-amber-600" aria-label="Conflict" />}
        {isPending(t) && <CircleDot className="size-3.5 text-sky-600" aria-label="Local changes" />}
      </div>
      <p className="font-medium">{t.title}</p>
      {t.labels.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {t.labels.map((l) => (
            <span key={l} className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">
              {l}
            </span>
          ))}
        </div>
      )}
    </button>
  );
}

function Board(props: {
  tickets: TicketSummary[];
  meta: Meta;
  field: string | null;
  showRepo: boolean;
  selected?: string;
  onOpen: (path: string) => void;
  onMove: (path: string, column: string | null) => void;
}) {
  const groups = groupTickets(props.tickets, props.meta, props.field);
  const [over, setOver] = useState<string | null | undefined>(undefined);
  return (
    <div className="flex h-full min-h-0 gap-3 overflow-x-auto p-3">
      {[...groups.entries()].map(([column, items]) => (
        <section
          key={column ?? "__none__"}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(column);
          }}
          onDragLeave={() => setOver(undefined)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(undefined);
            const path = e.dataTransfer.getData("text/plain");
            const t = props.tickets.find((x) => x.path === path);
            if (t && columnOf(t, props.field) !== column) props.onMove(path, column);
          }}
          className={`flex w-72 shrink-0 flex-col rounded-xl bg-slate-50 dark:bg-slate-900/50 ${over === column ? "ring-2 ring-sky-400" : ""}`}
        >
          <h3 className="flex items-center gap-2 px-3 pt-3 pb-2 text-xs font-semibold tracking-wide text-slate-500 uppercase">
            <span className="flex-1">{column ?? (props.field ? `No ${props.field}` : "—")}</span>
            <span className="font-normal">{items.length}</span>
          </h3>
          <div className="min-h-16 flex-1 space-y-2 overflow-y-auto px-2 pb-2">
            {items.map((t) => (
              <Card key={t.path} t={t} showRepo={props.showRepo} selected={props.selected === t.path} onOpen={() => props.onOpen(t.path)} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function TicketList(props: { tickets: TicketSummary[]; field: string | null; showRepo: boolean; selected?: string; onOpen: (path: string) => void }) {
  const th = "px-3 py-2 text-left text-xs font-medium text-slate-500";
  if (props.tickets.length === 0) return <p className="p-6 text-slate-500">No tickets match.</p>;
  return (
    <table className="w-full border-collapse text-sm">
      <thead className="sticky top-0 bg-slate-50 dark:bg-slate-900">
        <tr>
          <th className={th}>#</th>
          <th className={th}>Title</th>
          {props.field && <th className={th}>{props.field}</th>}
          <th className={th}>State</th>
          <th className={th}>Labels</th>
          <th className={th}></th>
        </tr>
      </thead>
      <tbody>
        {props.tickets.map((t) => (
          <tr key={t.path} onClick={() => props.onOpen(t.path)} className={`cursor-pointer border-t border-slate-100 hover:bg-sky-50 dark:border-slate-800 dark:hover:bg-slate-900 ${props.selected === t.path ? "bg-sky-50 dark:bg-slate-900" : ""}`}>
            <td className="px-3 py-2 whitespace-nowrap text-slate-400">{ticketLabel(t, props.showRepo)}</td>
            <td className="px-3 py-2">{t.title}</td>
            {props.field && <td className="px-3 py-2 whitespace-nowrap">{t.project[props.field] ?? ""}</td>}
            <td className="px-3 py-2">{t.state}</td>
            <td className="px-3 py-2 text-xs text-slate-500">{t.labels.join(", ")}</td>
            <td className="px-3 py-2 text-right">
              {t.conflicts.length > 0 && <TriangleAlert className="inline size-4 text-amber-600" />}
              {isPending(t) && <CircleDot className="inline size-4 text-sky-600" />}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
