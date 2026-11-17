import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleDot, ExternalLink, GitPullRequestArrow, Plus, RefreshCw, Search, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { formatTime } from "../workspaces/review";
import { createTicket, type Meta, runSync, type SyncReport, ticketsQuery, type TicketSummary } from "./api";
import { ChangesPanel } from "./ChangesPanel";
import { filterTickets, isPending, NO_PROJECT, projectStatuses, statusField, ticketLabel, type TicketView } from "./list";
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
  const visible = useMemo(() => (data && meta ? filterTickets(data.tickets, search, meta) : []), [data, meta, search]);
  const pending = data?.tickets.filter(isPending).length ?? 0;
  const multiRepo = (meta?.repos.length ?? 0) > 1;
  const labels = useMemo(() => [...new Set((data?.tickets ?? []).flatMap((t) => t.labels))].sort(), [data]);

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

  if (isError) return <p className="p-6 text-red-600">Could not load tickets: {String(error)}</p>;
  if (!data || !meta) return <p className="p-6 text-slate-400">Loading…</p>;

  const panelOpen = Boolean(search.ticket || search.changes);
  const projectNames = Object.keys(meta.projects);
  const chosen = search.project && search.project !== NO_PROJECT ? search.project : undefined;
  const field = statusField(meta, chosen);
  const statusOptions = chosen && field ? (meta.projects[chosen]?.fields[field]?.options ?? []) : [];
  const boardUrl = chosen ? meta.projects[chosen]?.url : null;

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
            <select
              value={search.project ?? ""}
              onChange={(e) => onChange({ ...search, project: e.target.value || undefined, status: undefined }, true)}
              className={select}
              aria-label="Project"
            >
              <option value="">All projects</option>
              {projectNames.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
              <option value={NO_PROJECT}>No project</option>
            </select>
            {statusOptions.length > 0 && (
              <select value={search.status ?? ""} onChange={(e) => onChange({ ...search, status: e.target.value || undefined }, true)} className={select} aria-label={field ?? "Status"}>
                <option value="">Any {field?.toLowerCase()}</option>
                {statusOptions.map((o) => (
                  <option key={o}>{o}</option>
                ))}
              </select>
            )}
            <select value={search.state ?? "open"} onChange={(e) => onChange({ ...search, state: e.target.value === "open" ? undefined : (e.target.value as TicketView["state"]) }, true)} className={select} aria-label="State">
              <option value="open">Open</option>
              <option value="closed">Closed</option>
              <option value="all">All</option>
            </select>
            {labels.length > 0 && (
              <select value={search.label ?? ""} onChange={(e) => onChange({ ...search, label: e.target.value || undefined }, true)} className={select} aria-label="Label">
                <option value="">Any label</option>
                {labels.map((l) => (
                  <option key={l}>{l}</option>
                ))}
              </select>
            )}
            {multiRepo && (
              <select value={search.repo ?? ""} onChange={(e) => onChange({ ...search, repo: e.target.value || undefined }, true)} className={select} aria-label="Repository">
                <option value="">All repositories</option>
                {meta.repos.map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
            )}
            {boardUrl && (
              <a href={boardUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-sky-600 hover:underline" title="Open the project on GitHub">
                <ExternalLink className="size-3.5" /> {chosen} on GitHub
              </a>
            )}
            <span className="ml-auto text-xs text-slate-400">
              {visible.length} ticket{visible.length === 1 ? "" : "s"}
              {meta.synced_at ? ` · synced ${formatTime(meta.synced_at)}` : meta.configured ? " · not synced yet" : ""}
            </span>
          </div>
          {report && <SyncNotice report={report} onClose={() => setReport(null)} />}
          {!meta.configured && (
            <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-600 dark:bg-slate-900 dark:text-slate-300">
              GitHub is not configured. Set <code>LIFEOS_GITHUB_TOKEN</code>, <code>LIFEOS_GITHUB_REPOS</code> and optionally{" "}
              <code>LIFEOS_GITHUB_PROJECTS</code> in <code>deploy/.env</code> and restart.
            </p>
          )}
          {data.broken.length > 0 && (
            <p className="text-sm text-red-600">Unreadable ticket files: {data.broken.map((b) => `${b.path} (${b.error})`).join("; ")}</p>
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          <TicketList tickets={visible} meta={meta} showRepo={multiRepo} selected={search.ticket} onOpen={(p) => onChange({ ...search, ticket: p, changes: undefined })} />
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

/** Opens the issue on GitHub without opening the ticket in LifeOS. */
function GitHubLink({ url }: { url: string | null }) {
  if (!url) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(e) => e.stopPropagation()}
      className="inline-flex rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-sky-600 dark:hover:bg-slate-800"
      title="Open on GitHub"
      aria-label="Open on GitHub"
    >
      <ExternalLink className="size-3.5" />
    </a>
  );
}

function TicketList(props: { tickets: TicketSummary[]; meta: Meta; showRepo: boolean; selected?: string; onOpen: (path: string) => void }) {
  const th = "px-3 py-2 text-left text-xs font-medium text-slate-500";
  if (props.tickets.length === 0) return <p className="p-6 text-slate-500">No tickets match.</p>;
  return (
    <table className="w-full border-collapse text-sm">
      <thead className="sticky top-0 bg-slate-50 dark:bg-slate-900">
        <tr>
          <th className={th}>#</th>
          <th className={th}>Title</th>
          <th className={th}>Project · status</th>
          <th className={th}>Labels</th>
          <th className={th}></th>
        </tr>
      </thead>
      <tbody>
        {props.tickets.map((t) => (
          <tr key={t.path} onClick={() => props.onOpen(t.path)} className={`cursor-pointer border-t border-slate-100 hover:bg-sky-50 dark:border-slate-800 dark:hover:bg-slate-900 ${props.selected === t.path ? "bg-sky-50 dark:bg-slate-900" : ""}`}>
            <td className="px-3 py-2 whitespace-nowrap text-slate-400">{ticketLabel(t, props.showRepo)}</td>
            <td className="px-3 py-2">
              {t.title}
              {t.state === "closed" && <span className="ml-2 rounded bg-slate-100 px-1 text-xs text-slate-500 dark:bg-slate-800">closed</span>}
            </td>
            <td className="px-3 py-2">
              <div className="flex flex-wrap gap-1">
                {projectStatuses(t, props.meta).map(({ project, status }) => (
                  <span key={project} className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] whitespace-nowrap text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                    {project}
                    {status ? ` · ${status}` : ""}
                  </span>
                ))}
              </div>
            </td>
            <td className="px-3 py-2 text-xs text-slate-500">{t.labels.join(", ")}</td>
            <td className="px-3 py-2 text-right whitespace-nowrap">
              {t.conflicts.length > 0 && <TriangleAlert className="inline size-4 text-amber-600" aria-label="Conflict" />}
              {isPending(t) && <CircleDot className="inline size-4 text-sky-600" aria-label="Local changes" />}
              <GitHubLink url={t.url} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
