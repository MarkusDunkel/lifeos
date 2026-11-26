import { useQuery } from "@tanstack/react-query";
import { useLayoutEffect, useRef, useState } from "react";
import { formatAgo } from "./format";
import {
  barPath,
  bucketTitle,
  compact,
  MODE_BG,
  MODE_FILL,
  MODE_LABEL,
  MODES,
  niceScale,
  stackBars,
  tickLabel,
  type TokenMode,
  type TokenRange,
  type TokenSeries,
  tokensQuery,
} from "./tokens";

const RANGES: { id: TokenRange; label: string }[] = [
  { id: "24h", label: "24 h" },
  { id: "7d", label: "7 days" },
  { id: "30d", label: "30 days" },
];
const HEIGHT = 220;
const AXIS_LEFT = 44;
const AXIS_BOTTOM = 22;
const PAD_TOP = 8;

const TOOLTIP_W = 208; // w-52

/** Beside the hovered column, on the side with more room, never past the chart's edges. */
function tooltipLeft(index: number, slot: number, width: number): number {
  const left = AXIS_LEFT + index * slot;
  const wanted = left + slot / 2 > width / 2 ? left - TOOLTIP_W - 8 : left + slot + 8;
  return Math.max(0, Math.min(wanted, width - TOOLTIP_W));
}

const segBtn = (on: boolean) =>
  `rounded-md px-2.5 py-1 text-sm ${on ? "bg-white shadow-sm dark:bg-slate-700" : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"}`;

function Swatch({ mode }: { mode: TokenMode }) {
  return <span className={`inline-block size-2.5 shrink-0 rounded-sm ${MODE_BG[mode]}`} />;
}

export function TokenChart() {
  const [range, setRange] = useState<TokenRange>("24h");
  const q = useQuery(tokensQuery(range));
  const data = q.data;

  return (
    <div className="space-y-4 rounded-xl border border-slate-200 p-4 dark:border-slate-800">
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <h2 className="font-semibold">Burned tokens</h2>
          <p className="text-xs text-slate-500">Tokens processed by the AI (input, cache and output), by how much of you it took.</p>
        </div>
        <div className="ml-auto inline-flex rounded-lg bg-slate-100 p-0.5 dark:bg-slate-800">
          {RANGES.map((r) => (
            <button key={r.id} className={segBtn(range === r.id)} onClick={() => setRange(r.id)}>
              {r.label}
            </button>
          ))}
        </div>
      </div>
      {!data ? (
        <p className="text-sm text-slate-400">{q.error ? q.error.message : "Loading…"}</p>
      ) : (
        <>
          <Totals data={data} />
          <Bars data={data} />
          <TopConsumers data={data} />
          <p className="text-xs text-slate-400">
            Interactive: Claude Code sessions on the PC, last reported {formatAgo(data.interactiveReportedAt)}.
          </p>
        </>
      )}
    </div>
  );
}

function Totals({ data }: { data: TokenSeries }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <div>
        <div className="text-xs text-slate-500">Total</div>
        <div className="text-2xl font-semibold tabular-nums">{compact(data.total)}</div>
      </div>
      {MODES.map((m) => (
        <div key={m} title={data.modes[m]}>
          <div className="flex items-center gap-1.5 text-xs text-slate-500">
            <Swatch mode={m} /> {MODE_LABEL[m]}
          </div>
          <div className="text-lg font-medium tabular-nums">{compact(data.totals[m])}</div>
          <div className="text-xs text-slate-400">{data.modes[m].split(": ")[1]}</div>
        </div>
      ))}
    </div>
  );
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(600);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

function Bars({ data }: { data: TokenSeries }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const [hidden, setHidden] = useState<TokenMode[]>([]);
  const shown = MODES.filter((m) => !hidden.includes(m));
  const visible = data.buckets.map((b) => ({ ...b, ...Object.fromEntries(hidden.map((m) => [m, 0])) }));
  const toggle = (m: TokenMode) =>
    setHidden((h) => (h.includes(m) ? h.filter((x) => x !== m) : shown.length > 1 ? [...h, m] : h));
  const plotW = Math.max(100, width - AXIS_LEFT);
  const plotH = HEIGHT - AXIS_BOTTOM - PAD_TOP;
  const max = Math.max(0, ...visible.map((b) => MODES.reduce((sum, m) => sum + b[m], 0)));
  const { top, step } = niceScale(max);
  const segments = stackBars(visible, plotW, plotH, top, data.buckets.length > 40 ? 1 : 2);
  const slot = plotW / Math.max(1, data.buckets.length);
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
  const hovered = hover === null ? null : data.buckets[hover];

  return (
    <div ref={ref} className="relative" onMouseLeave={() => setHover(null)}>
      <svg width={width} height={HEIGHT} role="img" aria-label="Burned tokens over time, stacked by mode">
        <g transform={`translate(${AXIS_LEFT},${PAD_TOP})`}>
          {ticks.map((t) => {
            const y = plotH - (t / top) * plotH;
            return (
              <g key={t}>
                <line x1={0} x2={plotW} y1={y} y2={y} className="stroke-slate-200 dark:stroke-slate-800" strokeWidth={1} />
                <text x={-6} y={y} dy="0.32em" textAnchor="end" className="fill-slate-400 text-[10px] tabular-nums">
                  {compact(t)}
                </text>
              </g>
            );
          })}
          {hover !== null && (
            <rect x={hover * slot} y={0} width={slot} height={plotH} className="fill-slate-100 dark:fill-slate-800/60" />
          )}
          {segments.map((s, i) => (
            <path key={i} d={barPath(s)} className={MODE_FILL[s.mode]} />
          ))}
          {data.buckets.map((b, i) => {
            const label = tickLabel(b.start, data.range, i);
            return label && i * slot < plotW - 30 ? (
              <text key={b.start} x={i * slot} y={plotH + 15} className="fill-slate-400 text-[10px]">
                {label}
              </text>
            ) : null;
          })}
          {/* hit targets: the whole column, bigger than the bar */}
          {data.buckets.map((b, i) => (
            <rect
              key={b.start}
              x={i * slot}
              y={0}
              width={slot}
              height={plotH}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
              onClick={() => setHover(i)}
            />
          ))}
        </g>
      </svg>
      {max === 0 && (
        <p className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-slate-400">
          No tokens in this range yet.
        </p>
      )}
      {hovered && hover !== null && (
        <div
          className="pointer-events-none absolute top-2 z-10 w-52 rounded-lg border border-slate-200 bg-white p-2.5 text-xs shadow-lg dark:border-slate-700 dark:bg-slate-900"
          style={{ left: tooltipLeft(hover, slot, width) }}
        >
          <div className="mb-1.5 font-medium">{bucketTitle(hovered.start, data.bucketSeconds)}</div>
          {[...MODES].reverse().map((m) => (
            <div key={m} className="flex items-center gap-1.5">
              <Swatch mode={m} /> <span className="text-slate-500">{MODE_LABEL[m]}</span>
              <span className="ml-auto tabular-nums">{hovered[m].toLocaleString()}</span>
            </div>
          ))}
          <div className="mt-1 flex border-t border-slate-100 pt-1 font-medium dark:border-slate-800">
            Total
            <span className="ml-auto tabular-nums">{MODES.reduce((s, m) => s + hovered[m], 0).toLocaleString()}</span>
          </div>
        </div>
      )}
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
        {MODES.map((m) => (
          <button
            key={m}
            onClick={() => toggle(m)}
            aria-pressed={!hidden.includes(m)}
            className={`inline-flex items-center gap-1.5 hover:text-slate-800 dark:hover:text-slate-200 ${hidden.includes(m) ? "line-through opacity-50" : ""}`}
          >
            <Swatch mode={m} /> {MODE_LABEL[m]}
          </button>
        ))}
        <span className="text-slate-400">· click to hide a mode, the axis rescales</span>
      </div>
    </div>
  );
}

function TopConsumers({ data }: { data: TokenSeries }) {
  if (data.top.length === 0) return null;
  const max = data.top[0].tokens;
  return (
    <details className="group" open>
      <summary className="cursor-pointer text-sm font-medium">Top consumers in this range</summary>
      <table className="mt-2 w-full text-left text-sm">
        <thead className="text-xs text-slate-500">
          <tr>
            <th className="py-1 pr-2 font-normal">What</th>
            <th className="py-1 pr-2 font-normal">Mode</th>
            <th className="py-1 text-right font-normal">Tokens</th>
          </tr>
        </thead>
        <tbody>
          {data.top.map((t) => (
            <tr key={`${t.id}:${t.mode}`} className="border-t border-slate-100 dark:border-slate-800">
              <td className="py-1.5 pr-2">
                {t.label}
                {t.detail && <span className="text-slate-500"> · {t.detail}</span>}
                <div className="mt-1 h-1 rounded-full bg-slate-100 dark:bg-slate-800">
                  <div className={`h-1 rounded-full ${MODE_BG[t.mode]}`} style={{ width: `${(t.tokens / max) * 100}%` }} />
                </div>
              </td>
              <td className="py-1.5 pr-2 whitespace-nowrap text-slate-500">
                <span className="inline-flex items-center gap-1.5">
                  <Swatch mode={t.mode} /> {MODE_LABEL[t.mode]}
                </span>
              </td>
              <td className="py-1.5 text-right tabular-nums">{t.tokens.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
