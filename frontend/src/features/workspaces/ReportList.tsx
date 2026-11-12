import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { Archive, FileText, MessageSquare } from "lucide-react";
import { useState } from "react";
import { reportsQuery, type ReportInfo } from "./api";
import { formatTime } from "./review";

export function ReportList() {
  const { data, isError } = useQuery(reportsQuery);
  const params = useParams({ strict: false }) as { _splat?: string };
  const [showArchive, setShowArchive] = useState(false);

  if (isError) return <p className="p-4 text-sm text-red-600">Could not load reports.</p>;
  if (!data) return <p className="p-4 text-sm text-slate-400">Loading…</p>;

  return (
    <nav className="h-full overflow-y-auto p-2">
      <h2 className="px-2 pt-2 pb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase">Reports</h2>
      {data.open.length === 0 ? (
        <p className="px-2 py-3 text-sm text-slate-500">
          No open reports. Agents write them into <code>ready/workspaces/</code>.
        </p>
      ) : (
        <ul>
          {data.open.map((r) => (
            <Row key={r.path} report={r} active={params._splat === r.path} />
          ))}
        </ul>
      )}

      <button
        onClick={() => setShowArchive((v) => !v)}
        className="mt-4 flex w-full items-center gap-1.5 px-2 py-1 text-xs font-semibold tracking-wide text-slate-500 uppercase hover:text-slate-800"
      >
        <Archive className="size-3.5" /> Archive ({data.archived.length})
      </button>
      {showArchive && (
        <ul>
          {data.archived.map((r) => (
            <Row key={r.path} report={r} active={params._splat === r.path} />
          ))}
        </ul>
      )}
    </nav>
  );
}

function Row({ report, active }: { report: ReportInfo; active: boolean }) {
  const draft = report.rounds.find((r) => r.editable);
  const sealed = report.rounds.filter((r) => r.state === "sealed").length;
  return (
    <li>
      <Link
        to="/workspaces/$"
        params={{ _splat: report.path }}
        className={`flex gap-2 rounded-lg px-2 py-2 text-sm hover:bg-slate-100 dark:hover:bg-slate-800 ${
          active ? "bg-sky-50 text-sky-900 dark:bg-sky-900/40 dark:text-sky-100" : ""
        }`}
      >
        <FileText className="mt-0.5 size-4 shrink-0 text-slate-400" />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{report.title}</span>
          <span className="flex flex-wrap items-center gap-x-2 text-xs text-slate-400">
            {formatTime(report.modified)}
            {draft && (
              <span className="inline-flex items-center gap-0.5 text-amber-600">
                <MessageSquare className="size-3" /> draft · {draft.items}
              </span>
            )}
            {sealed > 0 && <span>{sealed} sealed</span>}
          </span>
        </span>
      </Link>
    </li>
  );
}
