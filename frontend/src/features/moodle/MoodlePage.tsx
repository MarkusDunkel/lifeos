import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ExternalLink, GraduationCap, LogOut, RefreshCw } from "lucide-react";
import { type FormEvent, useState } from "react";
import { summarize } from "../mirrors/mirror";
import {
  type Course,
  connectMoodle,
  coursesQuery,
  disconnectMoodle,
  type MoodleReport,
  moodleStatusQuery,
  selectCourses,
  syncMoodle,
} from "./api";
import { buttonClass, cardClass, inputClass, Notice, pageClass, PageHeader, QueryState } from "../../components/ui";

const btn =
  buttonClass("secondary");
const primary =
  buttonClass("primary");
const input =
  `${inputClass} w-full`;

export function MoodlePage() {
  const status = useQuery(moodleStatusQuery);
  const s = status.data;
  return (
    <div className={pageClass("md")}>
      <PageHeader
        icon={GraduationCap}
        title="Moodle"
        subtitle="Course materials stay in Moodle. LifeOS mirrors the text of PDFs, slides, documents and pages into the knowledge hub, with a link back to Moodle. A sync only fetches what is new or changed."
      />
      {!s ? (
        <QueryState query={status} />
      ) : !s.configured ? (
        <Notice tone="warn">
          Not set up. Add {s.missing.join(", ")} to the deployment's .env (see docs/vault.md → Moodle).
        </Notice>
      ) : !s.connected ? (
        <ConnectForm url={s.url} />
      ) : (
        <Connected site={s.site} user={s.user} url={s.url} folder={s.folder} files={s.files} last={s.last} />
      )}
    </div>
  );
}

function ConnectForm({ url }: { url: string | null }) {
  const queryClient = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [useToken, setUseToken] = useState(false);
  const connect = useMutation({
    mutationFn: () => connectMoodle(useToken ? { token } : { username, password }),
    onSuccess: () => {
      setPassword("");
      return queryClient.invalidateQueries({ queryKey: ["moodle"] });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    connect.mutate();
  };

  return (
    <form onSubmit={submit} className={`${cardClass} space-y-3 p-4`}>
      <h2 className="font-semibold">Connect {url}</h2>
      <p className="text-sm text-slate-500">
        {useToken
          ? "Paste the token from Moodle → Profile → Preferences → Security keys (Moodle mobile web service)."
          : "Your username and password are sent to Moodle once to get an access token. Only the token is stored (outside the vault)."}
      </p>
      {useToken ? (
        <input className={input} placeholder="Token" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" />
      ) : (
        <>
          <input className={input} placeholder="Username" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
          <input
            className={input}
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <button className={primary} disabled={connect.isPending || (useToken ? !token : !username || !password)}>
          {connect.isPending ? "Connecting…" : "Connect"}
        </button>
        <button type="button" className="text-sm text-accent-600 hover:underline" onClick={() => setUseToken(!useToken)}>
          {useToken ? "Use username and password" : "Use a token instead"}
        </button>
      </div>
      {connect.error && <p className="text-sm text-red-600">{connect.error.message}</p>}
    </form>
  );
}

function Connected(props: {
  site?: string | null;
  user?: string | null;
  url: string | null;
  folder: string;
  files: number;
  last: MoodleReport | null;
}) {
  const queryClient = useQueryClient();
  const courses = useQuery(coursesQuery);
  const sync = useMutation({
    mutationFn: syncMoodle,
    onSettled: () => Promise.all([queryClient.invalidateQueries({ queryKey: ["moodle"] }), queryClient.invalidateQueries({ queryKey: ["knowledge"] })]),
  });
  const select = useMutation({
    mutationFn: selectCourses,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["moodle", "courses"] }),
  });
  const toggle = (course: Course) => {
    const ids = (courses.data ?? []).filter((c) => (c.id === course.id ? !c.selected : c.selected)).map((c) => c.id);
    select.mutate(ids);
  };
  const report = sync.data ?? props.last;

  return (
    <>
      <section className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 p-4 text-sm dark:border-slate-800">
        <div className="min-w-0 flex-1">
          <div className="font-medium">{props.site ?? props.url}</div>
          <div className="text-slate-500">
            {props.user} ·{" "}
            <Link to="/knowledge" className="text-accent-600 hover:underline">
              knowledge / {props.folder}
            </Link>{" "}
            ({props.files} files)
          </div>
        </div>
        <button
          className={btn}
          onClick={async () => {
            if (!window.confirm("Disconnect from Moodle? The mirrored notes stay in the vault.")) return;
            await disconnectMoodle();
            await queryClient.invalidateQueries({ queryKey: ["moodle"] });
          }}
        >
          <LogOut className="size-4" /> Disconnect
        </button>
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-3">
          <h2 className="flex-1 font-semibold">Courses</h2>
          <button className={primary} onClick={() => sync.mutate()} disabled={sync.isPending}>
            <RefreshCw className={`size-4 ${sync.isPending ? "animate-spin" : ""}`} /> {sync.isPending ? "Syncing…" : "Sync now"}
          </button>
        </div>
        {courses.isLoading && <p className="text-sm text-slate-400">Loading courses…</p>}
        {courses.error && <p className="text-sm text-red-600">{courses.error.message}</p>}
        <ul className="divide-y divide-slate-200 rounded-xl border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
          {courses.data?.map((c) => (
            <li key={c.id} className="flex items-center gap-3 px-4 py-2 text-sm">
              <input type="checkbox" checked={c.selected} onChange={() => toggle(c)} disabled={select.isPending} aria-label={`Sync ${c.name}`} />
              <span className="min-w-0 flex-1 truncate">{c.name}</span>
              {c.in_progress && <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-xs text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400">current</span>}
              <a href={c.url} target="_blank" rel="noopener noreferrer" className="text-slate-400 hover:text-accent-600" aria-label="Open in Moodle">
                <ExternalLink className="size-4" />
              </a>
            </li>
          ))}
        </ul>
        <p className="text-xs text-slate-500">Only ticked courses are synced. Current courses are ticked until you change the selection.</p>
      </section>

      {sync.error && <p className="text-sm text-red-600">{sync.error.message}</p>}
      {report && <ReportView report={report} />}
    </>
  );
}

function ReportView({ report }: { report: MoodleReport }) {
  const groups: [string, string[]][] = [
    ["New", report.created],
    ["Updated", report.updated],
    ["Moved", report.renamed],
    ["Removed in Moodle", report.deleted],
    ["Not mirrored", report.skipped ?? []],
    ["Failed", report.errors],
  ];
  return (
    <section className="space-y-2 rounded-xl border border-slate-200 p-4 text-sm dark:border-slate-800">
      <h2 className="font-semibold">Last sync</h2>
      <p className="text-slate-600 dark:text-slate-300">
        {new Date(report.synced_at).toLocaleString()} · {report.courses} courses · {summarize(report)}
        {report.tasks ? ` · ${report.tasks} assignments and quizzes` : ""}
      </p>
      {groups
        .filter(([, items]) => items.length > 0)
        .map(([label, items]) => (
          <details key={label} open={label === "Failed"}>
            <summary className={`cursor-pointer ${label === "Failed" ? "text-red-600" : ""}`}>
              {label} ({items.length})
            </summary>
            <ul className="mt-1 ml-4 list-disc space-y-0.5 text-xs text-slate-600 dark:text-slate-400">
              {items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </details>
        ))}
    </section>
  );
}
