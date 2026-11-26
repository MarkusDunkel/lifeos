import { queryOptions } from "@tanstack/react-query";
import { api } from "../../lib/api";

export type TokenMode = "auto" | "semi" | "interactive";
export type TokenRange = "24h" | "7d" | "30d";
export const MODES: TokenMode[] = ["auto", "semi", "interactive"]; // bottom to top in a stack

export type TokenBucket = { start: string } & Record<TokenMode, number>;
export type TokenConsumer = { id: string; label: string; detail: string; mode: TokenMode; tokens: number };
export type TokenSeries = {
  range: TokenRange;
  bucketSeconds: number;
  buckets: TokenBucket[];
  totals: Record<TokenMode, number>;
  total: number;
  top: TokenConsumer[];
  interactiveReportedAt: string | null;
  modes: Record<TokenMode, string>;
};

export const tokensQuery = (range: TokenRange) =>
  queryOptions({
    queryKey: ["control", "tokens", range],
    queryFn: () => api<TokenSeries>(`/api/control/tokens?range=${range}`),
    refetchInterval: 60_000,
  });

export const MODE_LABEL: Record<TokenMode, string> = {
  auto: "Fully automatic",
  semi: "Semi-automatic",
  interactive: "Interactive",
};

/** Series colours: the first three slots of the categorical palette, light and dark. */
export const MODE_FILL: Record<TokenMode, string> = {
  auto: "fill-[#2a78d6] dark:fill-[#3987e5]",
  semi: "fill-[#eb6834] dark:fill-[#d95926]",
  interactive: "fill-[#1baf7a] dark:fill-[#199e70]",
};
export const MODE_BG: Record<TokenMode, string> = {
  auto: "bg-[#2a78d6] dark:bg-[#3987e5]",
  semi: "bg-[#eb6834] dark:bg-[#d95926]",
  interactive: "bg-[#1baf7a] dark:bg-[#199e70]",
};

/** 950 → "950", 12_400 → "12k", 1_400_000 → "1.4M". */
export function compact(n: number): string {
  const abs = Math.abs(n);
  const fmt = (v: number, unit: string) => `${v >= 10 ? Math.round(v) : Math.round(v * 10) / 10}${unit}`;
  if (abs >= 1e9) return fmt(n / 1e9, "B");
  if (abs >= 1e6) return fmt(n / 1e6, "M");
  if (abs >= 1e3) return fmt(n / 1e3, "k");
  return String(Math.round(n));
}

/** A round axis maximum (1, 2, 2.5, 5 × 10^n) at or above ``max``, with its tick step. */
export function niceScale(max: number, ticks = 4): { top: number; step: number } {
  if (max <= 0) return { top: 1, step: 0.25 };
  const raw = max / ticks;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  return { top: step * Math.ceil(max / step), step };
}

export type Segment = { mode: TokenMode; x: number; y: number; width: number; height: number; top: boolean };

/** Stacked segments of every bucket; touching segments keep a ``gap`` of surface between them. */
export function stackBars(
  buckets: TokenBucket[],
  width: number,
  height: number,
  top: number,
  gap = 2,
): Segment[] {
  if (buckets.length === 0 || top <= 0) return [];
  const slot = width / buckets.length;
  const barWidth = Math.max(1, slot - gap);
  const out: Segment[] = [];
  buckets.forEach((b, i) => {
    const present = MODES.filter((m) => b[m] > 0);
    let base = height;
    present.forEach((mode, k) => {
      const full = (b[mode] / top) * height;
      const last = k === present.length - 1;
      const h = Math.max(1, full - (last ? 0 : gap));
      out.push({ mode, x: i * slot + gap / 2, y: base - h, width: barWidth, height: h, top: last });
      base -= full;
    });
  });
  return out;
}

/** SVG path of a bar whose top corners are rounded (the data end); the base stays square. */
export function barPath(s: Segment, radius = 4): string {
  const r = s.top ? Math.min(radius, s.width / 2, s.height) : 0;
  const { x, y, width: w, height: h } = s;
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/** Axis label of a bucket start, or null when that bucket gets none. */
export function tickLabel(startIso: string, range: TokenRange, index: number): string | null {
  const d = new Date(startIso);
  if (range === "24h") {
    return d.getHours() % 3 === 0 ? `${String(d.getHours()).padStart(2, "0")}:00` : null;
  }
  if (range === "7d") {
    return d.getHours() === 0 ? d.toLocaleDateString(undefined, { weekday: "short", day: "numeric" }) : null;
  }
  return index % 5 === 0 ? d.toLocaleDateString(undefined, { day: "numeric", month: "numeric" }) : null;
}

export function bucketTitle(startIso: string, seconds: number): string {
  const start = new Date(startIso);
  const end = new Date(start.getTime() + seconds * 1000);
  if (seconds >= 86400) return start.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  const time = (d: Date) => d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return `${start.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}, ${time(start)}–${time(end)}`;
}
