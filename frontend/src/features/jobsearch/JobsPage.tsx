import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Briefcase, ChevronDown, ChevronRight, Copy, ExternalLink, Globe, MapPin, Search, Send, Target } from "lucide-react";
import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  type AssessmentDetail,
  type AssessmentSummary,
  type Fit,
  matcherQuery,
  requestAssessment,
  fitOptionsQuery,
  type ImportRun,
  jobsearchStatusQuery,
  PAGE_SIZE,
  type PostingRow,
  type PostingsFilter,
  postingQuery,
  postingsQuery,
  startApplication,
} from "./api";
import { ago, formatEmployment, formatSalary } from "./format";

const btn =
  "inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100 disabled:opacity-40 dark:border-slate-700 dark:hover:bg-slate-800";
const input =
  "w-full rounded-lg border border-slate-300 bg-white py-2 pr-3 pl-9 text-sm dark:border-slate-700 dark:bg-slate-900";
const badge = "rounded px-1.5 py-0.5 text-xs";

const PERIODS: [string, number | null][] = [
  ["any time", null],
  ["last day", 1],
  ["last 7 days", 7],
  ["last 30 days", 30],
];

export function JobsPage() {
  const [text, setText] = useState("");
  const [filter, setFilter] = useState<PostingsFilter>({
    q: "",
    duplicates: false,
    gone: false,
    applied: false,
    days: null,
    sort: "new",
    profile: "",
    home: "",
    reach: false,
    passed: false,
    recommended: false,
  });
  const options = useQuery(fitOptionsQuery);
  const select = "rounded-lg border border-slate-300 bg-white px-2 py-1 dark:border-slate-700 dark:bg-slate-900";
  const [pages, setPages] = useState(1);

  // Search after a short pause in typing; any filter change starts at the first page.
  useEffect(() => {
    const timer = setTimeout(() => setFilter((f) => (f.q === text ? f : { ...f, q: text })), 300);
    return () => clearTimeout(timer);
  }, [text]);
  useEffect(() => setPages(1), [filter]);

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6 sm:p-8">
      <h1 className="flex items-center gap-2 text-2xl font-semibold">
        <Briefcase className="size-7 text-teal-600" /> Jobs
      </h1>
      <p className="text-sm text-slate-500">
        Job postings imported from devjobs.at. The import runs on your PC (devjobs.at blocks servers): once a day it asks
        whether to import, or run <code className="text-xs">lifeos jobsearch import</code> there. Postings for the same
        job are grouped; postings that went offline are kept for a year.
      </p>
      <StatusCard />
      <MatcherLine />
      <section className="space-y-3">
        <div className="relative">
          <Search className="pointer-events-none absolute top-2.5 left-3 size-4 text-slate-400" />
          <input
            className={input}
            placeholder="Search title, company, place or technology…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            aria-label="Search postings"
          />
        </div>
        <div className="flex flex-wrap items-center gap-4 text-sm text-slate-600 dark:text-slate-300">
          <label className="flex items-center gap-2">
            New
            <select
              className="rounded-lg border border-slate-300 bg-white px-2 py-1 dark:border-slate-700 dark:bg-slate-900"
              value={filter.days ?? ""}
              onChange={(e) => setFilter({ ...filter, days: e.target.value ? Number(e.target.value) : null })}
            >
              {PERIODS.map(([label, days]) => (
                <option key={label} value={days ?? ""}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2">
            Sort
            <select
              className={select}
              value={filter.profile ? `profile:${filter.profile}` : filter.sort}
              onChange={(e) => {
                const v = e.target.value;
                if (v.startsWith("profile:")) setFilter({ ...filter, sort: "fit", profile: v.slice(8) });
                else setFilter({ ...filter, sort: v as "new" | "fit" | "match", profile: "" });
              }}
            >
              <option value="new">newest first</option>
              <option value="match">AI recommendation</option>
              <option value="fit">best fit (any profile)</option>
              {options.data?.profiles.map((p) => (
                <option key={p.slug} value={`profile:${p.slug}`}>
                  fit: {p.title}
                </option>
              ))}
            </select>
          </label>
          {(options.data?.homes.length ?? 0) > 0 && (
            <label className="flex items-center gap-2">
              Near
              <select className={select} value={filter.home} onChange={(e) => setFilter({ ...filter, home: e.target.value })}>
                <option value="">anywhere</option>
                {options.data?.homes.map((h) => (
                  <option key={h.label} value={h.label}>
                    {h.label} ({h.radius_km} km)
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={filter.reach} onChange={(e) => setFilter({ ...filter, reach: e.target.checked })} />
            Near a home or remote
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={filter.recommended} onChange={(e) => setFilter({ ...filter, recommended: e.target.checked })} />
            AI recommends applying
          </label>
          <label className="flex items-center gap-2" title="Near a home or remote and a fit of at least 35">
            <input type="checkbox" checked={filter.passed} onChange={(e) => setFilter({ ...filter, passed: e.target.checked })} />
            Passes the prefilter
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={filter.applied} onChange={(e) => setFilter({ ...filter, applied: e.target.checked })} />
            In application
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={filter.duplicates} onChange={(e) => setFilter({ ...filter, duplicates: e.target.checked })} />
            Show duplicates
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={filter.gone} onChange={(e) => setFilter({ ...filter, gone: e.target.checked })} />
            Show offline postings
          </label>
        </div>
        <Results filter={filter} pages={pages} onMore={() => setPages(pages + 1)} />
      </section>
    </div>
  );
}

function StatusCard() {
  const status = useQuery(jobsearchStatusQuery);
  const s = status.data;
  if (!s) {
    return <p className="text-sm text-slate-400">{status.error ? status.error.message : "Loading…"}</p>;
  }
  const last = s.lastImport;
  return (
    <section className="space-y-2 rounded-xl border border-slate-200 p-4 text-sm dark:border-slate-800">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="flex-1 font-semibold">Import</h2>
        {s.doneToday ? (
          <span className={`${badge} bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400`}>imported today</span>
        ) : (
          <span className={`${badge} bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400`}>not imported today</span>
        )}
      </div>
      <p className="text-slate-600 dark:text-slate-300">
        {s.counts.active} postings online · {s.counts.duplicates} duplicates · {s.counts.reposts} republished ·{" "}
        {s.counts.gone} offline
      </p>
      {last ? <RunLine run={last} /> : <p className="text-slate-500">No import yet.</p>}
      {s.runs.length > 1 && (
        <details className="text-slate-500">
          <summary className="cursor-pointer">Earlier imports</summary>
          <ul className="mt-1 space-y-1">
            {s.runs.slice(1).map((run) => (
              <li key={run.at}>
                <RunLine run={run} />
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function RunLine({ run }: { run: ImportRun }) {
  const parts = [`${run.new} new`];
  if (run.duplicates) parts.push(`${run.duplicates} duplicates`);
  if (run.failed) parts.push(`${run.failed} failed`);
  if (run.pruned) parts.push(`${run.pruned} deleted after a year offline`);
  return (
    <p className="text-slate-500">
      {new Date(run.at).toLocaleString()} · {run.listed} listed on {run.source} · {parts.join(", ")}
      {!run.complete && <span className="text-amber-600"> · not finished, continues on the next run</span>}
      {run.errors.length > 0 && <span className="block text-xs text-red-600">{run.errors.at(-1)}</span>}
    </p>
  );
}

function Results({ filter, pages, onMore }: { filter: PostingsFilter; pages: number; onMore: () => void }) {
  const offsets = Array.from({ length: pages }, (_, i) => i * PAGE_SIZE);
  const first = useQuery(postingsQuery(filter, 0));
  const total = first.data?.total ?? 0;
  if (first.error) return <p className="text-sm text-red-600">{first.error.message}</p>;
  if (!first.data) return <p className="text-sm text-slate-400">Loading…</p>;
  return (
    <>
      <p className="text-sm text-slate-500">
        {total} posting{total === 1 ? "" : "s"}
      </p>
      {total > 0 && (
        <ul className="divide-y divide-slate-200 rounded-xl border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
          {offsets.map((offset) => (
            <ResultPage key={offset} filter={filter} offset={offset} />
          ))}
        </ul>
      )}
      {pages * PAGE_SIZE < total && (
        <button className={btn} onClick={onMore}>
          Show more
        </button>
      )}
    </>
  );
}

function ResultPage({ filter, offset }: { filter: PostingsFilter; offset: number }) {
  const page = useQuery(postingsQuery(filter, offset));
  return <>{page.data?.items.map((row) => <PostingItem key={row.key} row={row} />)}</>;
}

function PostingItem({ row }: { row: PostingRow }) {
  const [open, setOpen] = useState(false);
  const facts = [row.place, formatSalary(row.salary), formatEmployment(row.employment_type)].filter(Boolean);
  return (
    <li className="px-4 py-3 text-sm">
      <div className="flex items-start gap-2">
        <button
          className="mt-0.5 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
          onClick={() => setOpen(!open)}
          aria-label={open ? "Hide details" : "Show details"}
          aria-expanded={open}
        >
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
        </button>
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-baseline gap-2">
            <button className="text-left font-medium hover:underline" onClick={() => setOpen(!open)}>
              {row.title}
            </button>
            <Badges row={row} />
          </div>
          <div className="text-slate-600 dark:text-slate-300">
            {row.company}
            {facts.length > 0 && <span className="text-slate-500"> · {facts.join(" · ")}</span>}
          </div>
          {row.skills.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {row.skills.map((skill) => (
                <span key={skill} className={`${badge} bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300`}>
                  {skill}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="shrink-0 text-right text-xs text-slate-400">
          <div>{ago(row.firstSeen)}</div>
          <a href={row.url} target="_blank" rel="noopener noreferrer" className="inline-flex text-slate-400 hover:text-sky-600" aria-label="Open on devjobs.at">
            <ExternalLink className="size-4" />
          </a>
        </div>
      </div>
      {open && <PostingDetails postingKey={row.key} />}
    </li>
  );
}

const VERDICT = {
  apply: { label: "apply", style: "bg-emerald-600 text-white dark:bg-emerald-700" },
  maybe: { label: "maybe", style: "bg-amber-400 text-amber-950 dark:bg-amber-600 dark:text-white" },
  no: { label: "no", style: "bg-slate-300 text-slate-700 dark:bg-slate-700 dark:text-slate-200" },
} as const;

function MatcherLine() {
  const status = useQuery(matcherQuery);
  const s = status.data;
  if (!s?.configured) return null;
  return (
    <p className="flex items-center gap-2 text-xs text-slate-500">
      <Bot className="size-4" /> AI matcher today: {s.autoToday}/{s.dailyLimit} automatic, {s.manualToday}/{s.manualLimit} on
      request{s.queued ? ` · ${s.queued} waiting` : ""} · runs every 10 minutes on the best prefiltered postings
    </p>
  );
}

function VerdictBadge({ a }: { a: AssessmentSummary }) {
  if (!a.recommendation) return <span className={`${badge} bg-red-100 text-red-700`}>AI failed</span>;
  const v = VERDICT[a.recommendation];
  return (
    <span className={`${badge} inline-flex items-center gap-1 font-medium ${v.style}`} title={a.summary}>
      <Bot className="size-3" /> {v.label}
      {a.fit !== null && ` · ${a.fit}`}
      {a.stale && " · outdated"}
    </span>
  );
}

function AssessmentPanel({ postingKey, a, queued }: { postingKey: string; a?: AssessmentDetail; queued?: boolean }) {
  const queryClient = useQueryClient();
  const ask = useMutation({
    mutationFn: () => requestAssessment(postingKey),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["jobsearch"] }),
  });
  const waiting = queued || ask.isSuccess;
  const button = (
    <button className={btn} disabled={ask.isPending || waiting} onClick={() => ask.mutate()}>
      <Bot className="size-4" /> {waiting ? "Queued – the AI assesses it within minutes" : a ? "Assess again" : "Assess with AI"}
    </button>
  );
  const d = a?.detail;
  if (!a || !d) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        {button}
        {a?.error && <span className="text-xs text-red-600">Last attempt failed: {a.error}</span>}
        {ask.error && <span className="text-xs text-red-600">{ask.error.message}</span>}
      </div>
    );
  }
  const list = (title: string, items: string[]) =>
    items.length > 0 && (
      <div>
        <div className="font-medium">{title}</div>
        <ul className="list-disc pl-5">
          {items.map((i) => (
            <li key={i}>{i}</li>
          ))}
        </ul>
      </div>
    );
  return (
    <div className="space-y-2 rounded-lg border border-slate-200 p-3 text-xs dark:border-slate-800">
      <div className="flex flex-wrap items-center gap-2">
        <VerdictBadge a={a} />
        <span className="text-slate-500">
          assessed {a.at ? new Date(a.at).toLocaleString() : ""}
          {a.stale && " – the profiles changed since"}
        </span>
      </div>
      <p className="text-sm">{d.summary}</p>
      <div className="flex flex-wrap gap-x-4">
        {d.profiles.map((p) => (
          <span key={p.slug} title={p.reason}>
            {p.slug}: <span className="tabular-nums">{p.fit}</span>
          </span>
        ))}
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {list("Strengths", d.strengths)}
        {list("Gaps", d.gaps)}
        {list("Red flags", d.red_flags)}
        {list("To clarify", d.questions)}
      </div>
      {d.company && <p className="text-slate-600 dark:text-slate-300">{d.company}</p>}
      {d.missing_skills.length > 0 && <p className="text-slate-500">Not in any profile: {d.missing_skills.join(", ")}</p>}
      <details>
        <summary className="cursor-pointer text-slate-500">Research</summary>
        <div className="prose prose-xs prose-slate mt-1 max-w-none dark:prose-invert">
          <Markdown remarkPlugins={[remarkGfm]}>{a.research}</Markdown>
        </div>
      </details>
      {button}
    </div>
  );
}

function fitStyle(fit: number): string {
  if (fit >= 70) return "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300";
  if (fit >= 50) return "bg-teal-50 text-teal-700 dark:bg-teal-950/60 dark:text-teal-300";
  if (fit >= 35) return "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300";
  return "bg-slate-50 text-slate-400 dark:bg-slate-900 dark:text-slate-500";
}

function FitBadges({ fit }: { fit: Fit }) {
  return (
    <>
      {fit.profile_title && (
        <span className={`${badge} inline-flex items-center gap-1 ${fitStyle(fit.fit)}`} title={`Prefilter fit to ${fit.profile_title}`}>
          <Target className="size-3" /> {fit.fit} · {fit.profile_title}
        </span>
      )}
      {fit.homes.map((n) => (
        <span key={n.label} className={`${badge} inline-flex items-center gap-1 bg-amber-50 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300`}>
          <MapPin className="size-3" /> {n.label} · {Math.round(n.km)} km
        </span>
      ))}
      {fit.remote && (
        <span className={`${badge} inline-flex items-center gap-1 bg-sky-50 text-sky-700 dark:bg-sky-950/60 dark:text-sky-300`}>
          <Globe className="size-3" /> remote
        </span>
      )}
      {fit.homes.length === 0 && !fit.remote && fit.nearest && (
        <span className={`${badge} text-slate-400`}>
          {Math.round(fit.nearest.km)} km from {fit.nearest.label}
        </span>
      )}
    </>
  );
}

function FitDetails({ fit }: { fit: Fit }) {
  if (fit.profiles.length === 0) return null;
  return (
    <div className="space-y-1 rounded-lg bg-slate-50 p-3 text-xs dark:bg-slate-900">
      <div className="font-medium text-slate-600 dark:text-slate-300">
        Prefilter {fit.passed ? "passed" : "not passed"} · fit = 60 % skills + 40 % title
      </div>
      {fit.profiles.map((p) => (
        <div key={p.slug} className="flex flex-wrap gap-x-2">
          <span className="w-56 shrink-0 truncate">{p.title}</span>
          <span className="tabular-nums">
            {p.fit} (skills {p.skills}, title {p.title_match})
          </span>
          {p.matched.length > 0 && <span className="text-slate-500">{p.matched.join(", ")}</span>}
        </div>
      ))}
    </div>
  );
}

function Badges({ row }: { row: PostingRow }) {
  return (
    <>
      {row.assessment && <VerdictBadge a={row.assessment} />}
      {row.fit && <FitBadges fit={row.fit} />}
      {row.goneAt && <span className={`${badge} bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300`}>offline</span>}
      {row.duplicateOf && (
        <span className={`${badge} inline-flex items-center gap-1 bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300`}>
          <Copy className="size-3" /> duplicate
        </span>
      )}
      {!row.duplicateOf && (row.duplicateCount ?? 0) > 0 && (
        <span className={`${badge} bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300`}>
          +{row.duplicateCount} duplicate{row.duplicateCount === 1 ? "" : "s"}
        </span>
      )}
      {row.repostOf && <span className={`${badge} bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300`}>republished</span>}
      {row.application && (
        <a
          href={row.application}
          target="_blank"
          rel="noopener noreferrer"
          className={`${badge} bg-emerald-100 text-emerald-700 hover:underline dark:bg-emerald-950 dark:text-emerald-300`}
        >
          in application
        </a>
      )}
    </>
  );
}

function PostingDetails({ postingKey }: { postingKey: string }) {
  const detail = useQuery(postingQuery(postingKey));
  const queryClient = useQueryClient();
  const apply = useMutation({
    mutationFn: () => startApplication(postingKey),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["jobsearch"] }),
  });
  const d = detail.data;
  if (!d) return <p className="mt-2 pl-6 text-slate-400">{detail.error ? detail.error.message : "Loading…"}</p>;
  const dates = [
    d.date_posted && `posted ${new Date(d.date_posted).toLocaleDateString()}`,
    d.valid_through && `open until ${new Date(d.valid_through).toLocaleDateString()}`,
    d.firstSeen && `imported ${new Date(d.firstSeen).toLocaleDateString()}`,
    d.goneAt && `offline since ${new Date(d.goneAt).toLocaleDateString()}`,
  ].filter(Boolean);
  return (
    <div className="mt-3 space-y-3 pl-6">
      <div className="flex flex-wrap items-center gap-3">
        {d.application ? (
          <a href={d.application} target="_blank" rel="noopener noreferrer" className="text-sm text-emerald-700 hover:underline dark:text-emerald-400">
            In application – open the ticket
          </a>
        ) : (
          <button
            className={btn}
            disabled={apply.isPending || !!d.goneAt}
            onClick={() => {
              if (window.confirm(`Apply to "${d.title}" at ${d.company}? This creates a ticket in column "Neu".`)) apply.mutate();
            }}
          >
            <Send className="size-4" /> {apply.isPending ? "Creating ticket…" : "Apply"}
          </button>
        )}
        {apply.error && <span className="text-sm text-red-600">{apply.error.message}</span>}
        {apply.data && (
          <a href={apply.data.url} target="_blank" rel="noopener noreferrer" className="text-sm text-sky-600 hover:underline">
            Ticket #{apply.data.issue} created
          </a>
        )}
      </div>
      <AssessmentPanel postingKey={postingKey} a={d.assessment} queued={d.queued} />
      {d.fit && <FitDetails fit={d.fit} />}
      <p className="text-xs text-slate-500">
        {dates.join(" · ")}
        {d.industry && ` · ${d.industry}`}
      </p>
      <div className="prose prose-sm prose-slate max-w-none dark:prose-invert">
        <Markdown remarkPlugins={[remarkGfm]}>{d.description}</Markdown>
      </div>
      {d.benefits.length > 0 && <p className="text-xs text-slate-500">Benefits: {d.benefits.join(", ")}</p>}
      {d.related.length > 0 && (
        <div className="text-xs text-slate-500">
          <div className="font-medium">Same job, other postings:</div>
          <ul className="list-disc pl-5">
            {d.related.map((r: PostingRow) => (
              <li key={r.key}>
                <a href={r.url} target="_blank" rel="noopener noreferrer" className="hover:text-sky-600 hover:underline">
                  {r.title}
                </a>{" "}
                · {r.place} · imported {ago(r.firstSeen)}
                {r.goneAt ? " · offline" : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
