import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { CalendarClock, ChevronRight, NotebookPen } from "lucide-react";
import { subjectsQuery } from "./api";
import { countdown, type ExamRow, examRows, formatExamDate } from "./format";

export function ReadinessBar({ secure, cards }: { secure: number; cards: number }) {
  const pct = cards ? Math.round((100 * secure) / cards) : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
        <div className="h-full rounded-full bg-emerald-500" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-24 text-right text-xs text-slate-500 tabular-nums">
        {pct}% · {secure}/{cards}
      </span>
    </div>
  );
}

function ExamCard({ row }: { row: ExamRow }) {
  return (
    <Link
      to="/study/$subject"
      params={{ subject: row.subject }}
      className="block space-y-3 rounded-xl border border-slate-200 p-4 transition-colors hover:border-sky-300 hover:bg-sky-50/40 dark:border-slate-800 dark:hover:border-sky-800 dark:hover:bg-sky-950/30"
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="font-medium">{row.name}</div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-sm text-slate-500">
            <CalendarClock className="size-4" />
            {formatExamDate(row.date)}
            <span
              className={`rounded px-1.5 py-0.5 text-xs ${row.past ? "bg-slate-100 dark:bg-slate-800" : "bg-sky-100 text-sky-800 dark:bg-sky-900/50 dark:text-sky-200"}`}
            >
              {countdown(row.date)}
            </span>
          </div>
        </div>
        <ChevronRight className="mt-1 size-5 shrink-0 text-slate-400" />
      </div>
      {row.format && <p className="text-sm text-slate-600 dark:text-slate-300">{row.format}</p>}
      {row.topics.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {row.topics.map((t) => (
            <span key={t} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs dark:bg-slate-800">
              {t}
            </span>
          ))}
        </div>
      )}
      <ReadinessBar secure={row.secure} cards={row.cards} />
      {!row.past && (
        <div className="flex flex-wrap gap-x-4 text-xs text-slate-500">
          <span>{row.dueNow} due now</span>
          {row.minutesPerDay !== null && <span>≈ {row.minutesPerDay} min/day until the exam</span>}
        </div>
      )}
    </Link>
  );
}

export function StudyPage() {
  const subjects = useQuery(subjectsQuery);
  const rows = subjects.data ? examRows(subjects.data) : null;
  const undated = subjects.data?.filter((s) => s.exams.length === 0) ?? [];

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6 sm:p-8">
      <h1 className="flex items-center gap-2 text-2xl font-semibold">
        <NotebookPen className="size-7 text-violet-600" /> Study
      </h1>
      <p className="text-sm text-slate-500">
        Exams with their learning material and question banks. The tutor (ChatGPT via MCP) asks these cards; open an
        exam to check and correct what it asks. An exam date is set in <code className="text-xs">_Fach.md</code> or, per
        topic, with <code className="text-xs">prüfung:</code> in a question bank.
      </p>
      {subjects.isError && <p className="text-red-600">Could not load the subjects.</p>}
      {!rows ? (
        <p className="text-slate-400">Loading…</p>
      ) : (
        <>
          <section className="space-y-3">
            <h2 className="text-sm font-semibold tracking-wide text-slate-500 uppercase">Upcoming exams</h2>
            {rows.upcoming.length === 0 && <p className="text-sm text-slate-500">No upcoming exam.</p>}
            {rows.upcoming.map((r) => (
              <ExamCard key={`${r.subject}-${r.date}`} row={r} />
            ))}
          </section>
          {undated.length > 0 && (
            <section className="space-y-2">
              <h2 className="text-sm font-semibold tracking-wide text-slate-500 uppercase">Without exam date</h2>
              {undated.map((s) => (
                <Link key={s.key} to="/study/$subject" params={{ subject: s.key }} className="block text-sky-600 hover:underline">
                  {s.name}
                </Link>
              ))}
            </section>
          )}
          {rows.past.length > 0 && (
            <details className="group space-y-3">
              <summary className="cursor-pointer text-sm font-semibold tracking-wide text-slate-500 uppercase">
                Past exams ({rows.past.length})
              </summary>
              <div className="mt-3 space-y-3">
                {rows.past.map((r) => (
                  <ExamCard key={`${r.subject}-${r.date}`} row={r} />
                ))}
              </div>
            </details>
          )}
        </>
      )}
    </div>
  );
}
