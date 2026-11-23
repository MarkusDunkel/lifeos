import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowDownRight, ArrowRight, ArrowUpRight, Bot, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { type Dashboard, dashboardQuery, type Verdicts } from "./api";

// Colors by job (dataviz reference palette): one blue for magnitude, blue/orange for the two
// compared series, status steps for the AI verdicts (always shown with their label).
const BLUE = "bg-[#2a78d6] dark:bg-[#3987e5]";
const ORANGE = "bg-[#eb6834] dark:bg-[#d95926]";
const VERDICT: Record<keyof Verdicts, { label: string; bar: string; fill: string }> = {
  apply: { label: "apply", bar: "bg-[#0ca30c]", fill: "#0ca30c" },
  maybe: { label: "maybe", bar: "bg-[#fab219]", fill: "#fab219" },
  no: { label: "no", bar: "bg-[#a3a29d] dark:bg-[#6b6a65]", fill: "#a3a29d" },
};
// sequential blue ramp, light → dark (100 … 700); index 0 = nothing
const RAMP = ["", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];

const card = "space-y-3 rounded-xl border border-slate-200 p-4 dark:border-slate-800";
const muted = "text-xs text-slate-500";
const pct = (v: number) => `${Math.round(v * 100)} %`;

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section className={card}>
      <div>
        <h2 className="font-semibold">{title}</h2>
        {hint && <p className={muted}>{hint}</p>}
      </div>
      {children}
    </section>
  );
}

function Tile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="min-w-0 rounded-lg bg-slate-50 p-3 dark:bg-slate-900">
      <div className={muted}>{label}</div>
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      {sub && <div className={muted}>{sub}</div>}
    </div>
  );
}

/** A horizontal bar whose length is ``value / max``; the number stays in text ink. */
function Bar({ value, max, color = BLUE, title }: { value: number; max: number; color?: string; title?: string }) {
  const w = max > 0 ? Math.max((100 * value) / max, value > 0 ? 1.5 : 0) : 0;
  return (
    <div className="h-2.5 flex-1 rounded-r bg-slate-100 dark:bg-slate-800" title={title}>
      <div className={`h-full rounded-r ${color}`} style={{ width: `${w}%` }} />
    </div>
  );
}

function Funnel({ d }: { d: Dashboard }) {
  const f = d.funnel;
  const steps: [string, number, string][] = [
    ["Active postings", f.active, "online now, without duplicates"],
    ["Near a home or remote", f.reach, "within a home's radius or fully remote"],
    ["Pass the prefilter", f.passed, "near/remote and fit ≥ 35"],
    ["Assessed by the AI", f.assessed, "of the postings that pass or were requested"],
    ["In application", f.applied, "ticket on the applications board"],
  ];
  const m = f.matcher;
  return (
    <Section title="Funnel" hint="From all postings to the ones worth your time.">
      <ul className="space-y-1.5">
        {steps.map(([label, value, hint]) => (
          <li key={label} className="flex items-center gap-3 text-sm" title={hint}>
            <span className="w-44 shrink-0">{label}</span>
            <Bar value={value} max={f.active} title={`${label}: ${value}`} />
            <span className="w-14 shrink-0 text-right tabular-nums">{value}</span>
          </li>
        ))}
      </ul>
      {f.assessed > 0 && (
        <div className="space-y-1">
          <div className={muted}>AI verdicts of the {f.assessed} assessed postings</div>
          <VerdictBar v={f.verdicts} />
        </div>
      )}
      <p className={muted}>
        {f.open > 0 ? (
          <>
            {f.open} prefiltered postings not assessed yet
            {f.daysLeft !== null && <> · about {f.daysLeft} day(s) at {m.dailyLimit} per day</>}
          </>
        ) : (
          "Every posting that passes the prefilter is assessed."
        )}
        {m.configured && (
          <>
            {" "}
            · AI today: {m.autoToday}/{m.dailyLimit} automatic, {m.manualToday}/{m.manualLimit} on request
          </>
        )}
      </p>
    </Section>
  );
}

/** apply / maybe / no as one stacked bar with labels (status colors never stand alone). */
function VerdictBar({ v }: { v: Verdicts }) {
  const total = v.apply + v.maybe + v.no;
  if (!total) return null;
  return (
    <div className="space-y-1">
      <div className="flex h-3 gap-0.5 overflow-hidden rounded">
        {(Object.keys(VERDICT) as (keyof Verdicts)[]).map(
          (k) =>
            v[k] > 0 && (
              <div key={k} className={VERDICT[k].bar} style={{ width: `${(100 * v[k]) / total}%` }} title={`${k}: ${v[k]}`} />
            ),
        )}
      </div>
      <div className="flex flex-wrap gap-3 text-xs">
        {(Object.keys(VERDICT) as (keyof Verdicts)[]).map((k) => (
          <span key={k} className="inline-flex items-center gap-1">
            <span className={`inline-block size-2.5 rounded-sm ${VERDICT[k].bar}`} />
            {VERDICT[k].label} <span className="tabular-nums text-slate-500">{v[k]}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function Trend({ now, before }: { now: number; before: number }) {
  const Icon = now > before ? ArrowUpRight : now < before ? ArrowDownRight : ArrowRight;
  return (
    <span className="inline-flex items-center gap-0.5 tabular-nums" title={`${now} new in the last 7 days, ${before} in the 7 days before`}>
      <Icon className="size-3.5" /> {now} <span className="text-slate-400">/ {before}</span>
    </span>
  );
}

function Demand({ d }: { d: Dashboard }) {
  const max = Math.max(...d.demand.map((p) => Math.max(p.share, p.weight)), 0.01);
  return (
    <Section
      title="Demand per profile"
      hint={`Postings with a fit ≥ ${d.goodFit} to the profile (all of Austria). Market share next to the weight you gave the profile.`}
    >
      <div className="flex flex-wrap gap-4 text-xs">
        <span className="inline-flex items-center gap-1">
          <span className={`inline-block size-2.5 rounded-sm ${BLUE}`} /> market share
        </span>
        <span className="inline-flex items-center gap-1">
          <span className={`inline-block size-2.5 rounded-sm ${ORANGE}`} /> your weight
        </span>
      </div>
      <ul className="space-y-3">
        {d.demand.map((p) => (
          <li key={p.slug} className="space-y-1 text-sm">
            <div className="flex flex-wrap items-baseline gap-x-3">
              <span className="font-medium">{p.title}</span>
              <span className={muted}>
                {p.good} postings · {p.near} near you · new 7 days <Trend now={p.newWeek} before={p.newWeekBefore} />
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Bar value={p.share} max={max} title={`market share ${pct(p.share)}`} />
              <span className="w-12 text-right text-xs tabular-nums">{pct(p.share)}</span>
            </div>
            <div className="flex items-center gap-2">
              <Bar value={p.weight} max={max} color={ORANGE} title={`your weight ${pct(p.weight)}`} />
              <span className="w-12 text-right text-xs tabular-nums">{pct(p.weight)}</span>
            </div>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function Places({ d }: { d: Dashboard }) {
  const { columns, homes, rows, commuteMedian } = d.places;
  const max = Math.max(1, ...rows.flatMap((r) => columns.map((c) => r.counts[c] ?? 0)));
  const step = (v: number) => (v <= 0 ? 0 : Math.min(RAMP.length - 1, 1 + Math.floor(((RAMP.length - 2) * v) / max)));
  return (
    <Section
      title="Profile × place"
      hint={`Postings with a fit ≥ ${d.goodFit}. Your homes and remote first, then the most frequent cities beyond your homes.`}
    >
      <div className="overflow-x-auto">
        <table className="w-full border-separate border-spacing-0.5 text-xs">
          <thead>
            <tr>
              <th className="text-left font-normal text-slate-500" />
              {columns.map((c) => (
                <th key={c} className={`px-1 text-center font-medium ${homes.includes(c) ? "" : "text-slate-500"}`}>
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.slug}>
                <td className="max-w-48 truncate pr-2" title={r.title}>
                  {r.title}
                </td>
                {columns.map((c) => {
                  const v = r.counts[c] ?? 0;
                  const s = step(v);
                  return (
                    <td
                      key={c}
                      className={`h-8 min-w-12 rounded text-center tabular-nums ${s === 0 ? "text-slate-400" : s >= 4 ? "text-white" : "text-slate-900"}`}
                      style={{ background: s ? RAMP[s] : undefined }}
                      title={`${r.title} · ${c}: ${v}`}
                    >
                      {v}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {Object.keys(commuteMedian).length > 0 && (
        <p className={muted}>
          Median commute to these postings (quicker of car and bike):{" "}
          {Object.entries(commuteMedian)
            .map(([home, m]) => `${home} ${m === null ? "–" : `${Math.round(m)} min`}`)
            .join(" · ")}
        </p>
      )}
    </Section>
  );
}

function Calibration({ points }: { points: NonNullable<Dashboard["quality"]>["points"] }) {
  const size = 220;
  const pad = 28;
  const x = (v: number) => pad + ((size - pad - 8) * v) / 100;
  const y = (v: number) => size - pad - ((size - pad - 8) * v) / 100;
  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="w-full max-w-64" role="img" aria-label="Prefilter fit against AI fit">
      {[0, 50, 100].map((t) => (
        <g key={t} className="text-slate-300 dark:text-slate-700">
          <line x1={x(0)} x2={x(100)} y1={y(t)} y2={y(t)} stroke="currentColor" strokeWidth={0.5} />
          <text x={pad - 4} y={y(t) + 3} textAnchor="end" className="fill-slate-500 text-[8px]">
            {t}
          </text>
          <text x={x(t)} y={size - pad + 12} textAnchor="middle" className="fill-slate-500 text-[8px]">
            {t}
          </text>
        </g>
      ))}
      <line x1={x(0)} y1={y(0)} x2={x(100)} y2={y(100)} className="stroke-slate-400" strokeDasharray="3 3" strokeWidth={1} />
      <text x={x(100)} y={y(100) - 3} textAnchor="end" className="fill-slate-500 text-[7px]">
        equal
      </text>
      {points
        .filter((p) => p.ai !== null)
        .map((p) => (
          <circle
            key={p.key}
            cx={x(p.prefilter)}
            cy={y(p.ai ?? 0)}
            r={4.5}
            fill={p.recommendation ? VERDICT[p.recommendation].fill : "#a3a29d"}
            className="stroke-white dark:stroke-slate-950"
            strokeWidth={2}
          >
            <title>{`${p.title}: prefilter ${p.prefilter}, AI ${p.ai} (${p.recommendation})`}</title>
          </circle>
        ))}
      <text x={x(50)} y={size - 2} textAnchor="middle" className="fill-slate-500 text-[8px]">
        prefilter fit
      </text>
      <text x={8} y={y(50)} textAnchor="middle" transform={`rotate(-90 8 ${y(50)})`} className="fill-slate-500 text-[8px]">
        AI fit
      </text>
    </svg>
  );
}

function Quality({ d }: { d: Dashboard }) {
  const q = d.quality;
  if (!q) {
    return (
      <Section title="AI assessment">
        <p className="text-sm text-slate-500">No assessments yet – the matcher runs every 10 minutes.</p>
      </Section>
    );
  }
  const offset = q.offset ?? 0;
  return (
    <Section title="AI assessment" hint="How the AI judges the prefiltered postings, and how well the rule prefilter predicts it.">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Tile label="Worth a look" value={pct(q.hitRate)} sub="apply or maybe" />
        <Tile label="Same best profile" value={q.sameProfile === null ? "–" : pct(q.sameProfile)} sub="prefilter = AI" />
        <Tile
          label="Prefilter vs. AI"
          value={`${offset > 0 ? "+" : ""}${Math.round(offset)}`}
          sub={`Ø ${Math.round(q.prefilterMean ?? 0)} vs. ${Math.round(q.aiMean ?? 0)} points`}
        />
        <Tile
          label="Per assessment"
          value={q.tokensMean ? `${Math.round(q.tokensMean / 1000)}k` : "–"}
          sub={q.listEurMean !== null ? `tokens · ≈ ${q.listEurMean.toFixed(2)} € API price` : "tokens"}
        />
      </div>
      <div className="grid items-start gap-4 sm:grid-cols-[16rem_1fr]">
        <Calibration points={q.points} />
        <div className="space-y-3 text-sm">
          <p className="text-slate-600 dark:text-slate-300">
            {offset >= 15
              ? `The prefilter rates postings about ${Math.round(offset)} points higher than the AI: it overestimates. Points below the dashed line are postings the AI likes less than the rules.`
              : offset <= -15
                ? `The AI rates postings about ${Math.round(-offset)} points higher than the prefilter: the prefilter may be too strict.`
                : "Prefilter and AI agree on average."}
          </p>
          <div className="space-y-2">
            <div className={muted}>Verdict by best profile</div>
            {q.byProfile.map((p) => (
              <div key={p.slug}>
                <div className="text-xs">{p.title}</div>
                <VerdictBar v={p.counts} />
              </div>
            ))}
          </div>
          {(q.stale > 0 || q.failed > 0) && (
            <p className={muted}>
              {q.stale > 0 && `${q.stale} outdated (profiles changed since) `}
              {q.failed > 0 && `· ${q.failed} failed`}
            </p>
          )}
          <p className={muted}>API value of all assessments so far: ≈ {q.listEurTotal.toFixed(2)} € (paid by the subscription)</p>
        </div>
      </div>
    </Section>
  );
}

function Gaps({ d }: { d: Dashboard }) {
  const g = d.gaps;
  const maxMissing = Math.max(1, ...g.missing.map((m) => m.count));
  const maxWanted = Math.max(1, ...g.wanted.map((m) => m.count));
  const levelStyle = (l: number | null) =>
    l === null
      ? "text-slate-400"
      : l <= 1
        ? "rounded bg-red-50 px-1 text-red-700 dark:bg-red-950/50 dark:text-red-300"
        : "text-slate-600 dark:text-slate-300";
  return (
    <Section title="Gaps by the market" hint="What the postings ask for, next to your own level from the career overview.">
      <div className="grid gap-6 sm:grid-cols-2">
        <div className="space-y-1.5">
          <div className="text-sm font-medium">Skills in your fitting postings near you</div>
          <div className={muted}>{g.basis} postings with a fit ≥ {d.goodFit}; your level 0–3 (red: 0–1)</div>
          {g.wanted.map((w) => (
            <div key={w.skill} className="flex items-center gap-2 text-sm">
              <span className="w-32 shrink-0 truncate" title={w.skill}>
                {w.skill}
              </span>
              <Bar value={w.count} max={maxWanted} title={`${w.skill}: ${w.count} postings`} />
              <span className="w-8 text-right text-xs tabular-nums">{w.count}</span>
              <span className={`w-10 text-center text-xs ${levelStyle(w.level)}`} title="your level">
                {w.level ?? "?"}
              </span>
            </div>
          ))}
        </div>
        <div className="space-y-1.5">
          <div className="text-sm font-medium">Named missing by the AI</div>
          <div className={muted}>Skills postings stress that none of your profiles lists</div>
          {g.missing.length === 0 && <p className="text-sm text-slate-500">Nothing yet.</p>}
          {g.missing.map((m) => (
            <div key={m.skill} className="flex items-center gap-2 text-sm">
              <span className="w-40 shrink-0 truncate" title={m.skill}>
                {m.skill}
              </span>
              <Bar value={m.count} max={maxMissing} title={`${m.skill}: ${m.count} assessments`} />
              <span className="w-8 text-right text-xs tabular-nums">{m.count}</span>
            </div>
          ))}
        </div>
      </div>
      <p className={muted}>
        Skills without a level (?) are not in your overview yet.{" "}
        <Link to="/career" className="text-sky-700 hover:underline dark:text-sky-300">
          Open the career page
        </Link>
      </p>
    </Section>
  );
}

export function DashboardView() {
  const queryClient = useQueryClient();
  const dashboard = useQuery(dashboardQuery(false));
  const d = dashboard.data;
  if (dashboard.error) return <p className="text-sm text-red-600">{dashboard.error.message}</p>;
  if (!d) return <p className="text-sm text-slate-400">Calculating…</p>;
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-xs text-slate-500">
        <Bot className="size-4" />
        <span className="flex-1">As of {new Date(d.generatedAt).toLocaleString()}</span>
        <button
          className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
          onClick={() => queryClient.fetchQuery(dashboardQuery(true)).then((fresh) => queryClient.setQueryData(dashboardQuery(false).queryKey, fresh))}
        >
          <RefreshCw className="size-3.5" /> Recalculate
        </button>
      </div>
      <Funnel d={d} />
      <Quality d={d} />
      <Demand d={d} />
      <Places d={d} />
      <Gaps d={d} />
    </div>
  );
}
