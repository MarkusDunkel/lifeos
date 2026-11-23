import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Compass, MessageSquareText, RefreshCw, TriangleAlert } from "lucide-react";
import { type Bucket, type CareerProfile, careerQuery, type CurriculumItem, refreshCurriculum } from "./api";

const BUCKETS: { key: Bucket; label: string; hint: string }[] = [
  { key: "now", label: "Now", hint: "important for the main profile and a big gap" },
  { key: "soon", label: "Soon", hint: "" },
  { key: "later", label: "Later", hint: "" },
  { key: "covered", label: "Covered", hint: "level 3" },
];

const STATUS_STYLE: Record<CareerProfile["status"], string> = {
  active: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-200",
  draft: "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-200",
  paused: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  dropped: "bg-slate-100 text-slate-400 line-through dark:bg-slate-800",
};

const SHARE_STYLE = {
  core: "font-medium text-slate-800 dark:text-slate-100",
  secondary: "text-slate-600 dark:text-slate-300",
  edge: "text-slate-400",
} as const;

function ProfileCard({ profile, dir }: { profile: CareerProfile; dir: string }) {
  return (
    <div className="space-y-2 rounded-xl border border-slate-200 p-4 dark:border-slate-800">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="font-medium">{profile.title}</div>
          <div className="text-xs text-slate-500">
            {profile.skills} skills · <code>{profile.slug}</code>
          </div>
        </div>
        <span className={`rounded px-1.5 py-0.5 text-xs ${STATUS_STYLE[profile.status]}`}>{profile.status}</span>
      </div>
      {profile.status === "active" && (
        <div className="flex items-center gap-2">
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
            <div className="h-full rounded-full bg-sky-500" style={{ width: `${Math.round(profile.share * 100)}%` }} />
          </div>
          <span className="w-12 text-right text-xs text-slate-500 tabular-nums">{Math.round(profile.share * 100)}%</span>
        </div>
      )}
      {profile.problems.map((p) => (
        <p key={p} className="flex gap-1 text-xs text-amber-700 dark:text-amber-300">
          <TriangleAlert className="size-3.5 shrink-0" /> {p}
        </p>
      ))}
      <Link
        to="/reviews/$"
        params={{ _splat: `${dir}/${profile.slug}` }}
        className="inline-flex items-center gap-1 text-sm text-sky-700 hover:underline dark:text-sky-300"
      >
        <MessageSquareText className="size-4" /> Review & sharpen
      </Link>
    </div>
  );
}

function Row({ item, rank }: { item: CurriculumItem; rank: number }) {
  return (
    <li className="flex items-start gap-3 py-2">
      <span className="w-6 shrink-0 text-right text-xs text-slate-400 tabular-nums">{rank}</span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-medium">{item.name}</span>
          <span className="text-xs text-slate-500">
            level {item.level ?? "–"} / 3 · importance {item.importance.toFixed(2)}
          </span>
        </div>
        <div className="flex flex-wrap gap-x-2 text-xs">
          {item.sources.map((s) => (
            <span key={s.slug} className={SHARE_STYLE[s.share]}>
              {s.title} ({s.share})
            </span>
          ))}
        </div>
      </div>
      <div className="w-24 shrink-0">
        <div className="h-1.5 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
          <div className="h-full rounded-full bg-violet-500" style={{ width: `${Math.round(item.priority * 100)}%` }} />
        </div>
        <div className="mt-0.5 text-right text-[11px] text-slate-500 tabular-nums">{item.priority.toFixed(2)}</div>
      </div>
    </li>
  );
}

export function CareerPage() {
  const queryClient = useQueryClient();
  const overview = useQuery(careerQuery);
  const refresh = useMutation({
    mutationFn: refreshCurriculum,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["career"] }),
  });
  const d = overview.data;
  const rank = new Map(d?.items.map((item, i) => [item.key, i + 1]));

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6 sm:p-8">
      <h1 className="flex items-center gap-2 text-2xl font-semibold">
        <Compass className="size-7 text-sky-600" /> Career
      </h1>
      <p className="text-sm text-slate-500">
        Job profiles are living documents: sharpen one on its review page and send the feedback – the profile designer
        revises it. Weights, status and your own level per skill live in{" "}
        <code className="text-xs">{d?.overviewFile ?? "Overview.md"}</code>; the curriculum below is calculated from
        them (weight × share × gap).
      </p>
      {overview.isError && <p className="text-red-600">Could not load the profiles.</p>}
      {d && d.profiles.length === 0 && (
        <p className="rounded-xl border border-dashed border-slate-300 p-4 text-sm text-slate-500 dark:border-slate-700">
          No profiles yet. Create a ticket in LifeOS Tasks (e.g. “Create my job profiles”), set Responsible to the
          profile designer and move it to Todo.
        </p>
      )}
      {d && d.profiles.length > 0 && (
        <section className="space-y-3">
          <h2 className="font-semibold">Profiles</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {d.profiles.map((p) => (
              <ProfileCard key={p.slug} profile={p} dir={d.profilesDir} />
            ))}
          </div>
        </section>
      )}
      {d && d.warnings.length > 0 && (
        <ul className="space-y-1 rounded-xl bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          {d.warnings.map((w) => (
            <li key={w} className="flex gap-1.5">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" /> {w}
            </li>
          ))}
        </ul>
      )}
      {d && d.items.length > 0 && (
        <section className="space-y-3">
          <div className="flex items-center gap-2">
            <h2 className="flex-1 font-semibold">Target curriculum</h2>
            <button
              type="button"
              onClick={() => refresh.mutate()}
              disabled={refresh.isPending || !d.overviewExists}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
              title="Write the curriculum into the overview document"
            >
              <RefreshCw className={`size-3.5 ${refresh.isPending ? "animate-spin" : ""}`} /> Write to overview
            </button>
          </div>
          {BUCKETS.map(({ key, label, hint }) => {
            const items = d.items.filter((i) => i.bucket === key);
            if (items.length === 0) return null;
            return (
              <div key={key}>
                <h3 className="text-sm font-medium text-slate-600 dark:text-slate-300">
                  {label} {hint && <span className="font-normal text-slate-400">– {hint}</span>}
                </h3>
                <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                  {items.map((item) => (
                    <Row key={item.key} item={item} rank={rank.get(item.key) ?? 0} />
                  ))}
                </ul>
              </div>
            );
          })}
        </section>
      )}
      {d && d.profiles.length > 0 && d.items.length === 0 && (
        <p className="text-sm text-slate-500">
          The curriculum is empty until at least one profile is <em>active</em> in the overview.
        </p>
      )}
    </div>
  );
}
