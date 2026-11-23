import type { Appointment, DayView, Goal, GoalInput, PlanBlock } from "./api";

/** Local calendar day as YYYY-MM-DD. */
export function isoDay(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function addDays(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  return isoDay(new Date(y, m - 1, d + days));
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export function dayLabel(iso: string, today: string): string {
  if (iso === today) return "Today";
  if (iso === addDays(today, 1)) return "Tomorrow";
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  return `${WEEKDAYS[date.getDay()]} ${d}.${m}.`;
}

export type TimelineItem =
  | { kind: "block"; start: string; end: string; block: PlanBlock }
  | { kind: "appointment"; start: string; end: string; appointment: Appointment };

/** Blocks (removed ones left out) and appointments of a day, in time order. */
export function timeline(day: DayView): TimelineItem[] {
  const items: TimelineItem[] = [
    ...day.blocks
      .filter((b) => b.status !== "removed")
      .map((block) => ({ kind: "block" as const, start: block.start, end: block.end, block })),
    ...day.appointments.map((appointment) => ({
      kind: "appointment" as const,
      start: appointment.start,
      end: appointment.end,
      appointment,
    })),
  ];
  return items.sort((a, b) => a.start.localeCompare(b.start) || a.end.localeCompare(b.end));
}

/** Share of the weekly target that is done, and done + still planned (each 0..1). */
export function progress(goal: Goal): { done: number; planned: number } {
  if (!goal.target) return { done: goal.done > 0 ? 1 : 0, planned: 0 };
  const done = Math.min(goal.done / goal.target, 1);
  const planned = Math.min((goal.done + goal.planned) / goal.target, 1);
  return { done, planned };
}

export function amountText(value: number, unit: Goal["targetUnit"]): string {
  const n = Number.isInteger(value) ? String(value) : value.toFixed(1);
  return unit === "hours" ? `${n} h` : `${n}×`;
}

export type ResolvedGoal = {
  target: number;
  unit: "hours" | "sessions";
  isGroup: boolean;
  warnings: string[];
};

/** Top-level goals and the members of each group (one level), like the backend. */
export function groupGoals(goals: GoalInput[]): { top: GoalInput[]; members: Map<string, GoalInput[]> } {
  const names = new Set(goals.map((g) => g.name));
  const topNames = new Set(goals.filter((g) => !g.group || !names.has(g.group)).map((g) => g.name));
  const members = new Map<string, GoalInput[]>();
  const top: GoalInput[] = [];
  for (const g of goals) {
    if (g.group && topNames.has(g.group) && g.group !== g.name) {
      members.set(g.group, [...(members.get(g.group) ?? []), g]);
    } else top.push(g);
  }
  return { top, members };
}

const round2 = (n: number) => Math.round(n * 100) / 100;

/** Weekly targets per goal name, before saving (mirrors ``goals.resolve`` in the backend):
 * percentages share what the fixed and auto members of the group leave. */
export function resolveGoals(goals: GoalInput[], autoHours: number): Map<string, ResolvedGoal> {
  const { top, members } = groupGoals(goals);
  const out = new Map<string, ResolvedGoal>();
  for (const g of top) {
    const own = members.get(g.name) ?? [];
    if (!own.length) {
      out.set(g.name, {
        target: g.amount === null ? autoHours : g.amount,
        unit: g.unit === "sessions" ? "sessions" : "hours",
        isGroup: false,
        warnings: [],
      });
      continue;
    }
    const warnings: string[] = [];
    const fixed = new Map<string, number>();
    const shares = new Map<string, number>();
    for (const m of own) {
      if (m.unit === "percent" && m.amount !== null) shares.set(m.name, m.amount);
      else if (m.unit !== "sessions") fixed.set(m.name, m.amount === null ? autoHours : m.amount);
    }
    const fixedSum = [...fixed.values()].reduce((a, b) => a + b, 0);
    let total = g.amount ?? 0;
    if (g.amount === null || g.unit !== "hours") {
      warnings.push("a group needs a fixed number of hours");
      total = fixedSum;
    }
    const rest = Math.max(total - fixedSum, 0);
    if (fixedSum > total) warnings.push(`fixed and auto members need ${fixedSum} h, more than ${total} h`);
    const pct = [...shares.values()].reduce((a, b) => a + b, 0);
    if (shares.size && pct > 100) warnings.push(`percentages add up to ${pct} %, scaled down to 100 %`);
    else if (shares.size && pct < 100) warnings.push(`only ${pct} % of the remaining ${round2(rest)} h are assigned`);
    out.set(g.name, { target: total, unit: "hours", isGroup: true, warnings });
    for (const m of own) {
      const share = shares.get(m.name);
      if (share !== undefined) {
        out.set(m.name, { target: round2((rest * share) / Math.max(pct, 100)), unit: "hours", isGroup: false, warnings: [] });
      } else if (m.unit === "sessions") {
        out.set(m.name, { target: m.amount ?? 0, unit: "sessions", isGroup: false, warnings: [] });
      } else {
        out.set(m.name, { target: fixed.get(m.name) ?? 0, unit: "hours", isGroup: false, warnings: [] });
      }
    }
  }
  return out;
}

/** Stable hue per category so blocks keep their colour across views. */
export function categoryHue(category: string): number {
  let h = 0;
  for (const ch of category) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}
