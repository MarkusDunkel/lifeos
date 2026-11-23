import type { Appointment, DayView, Goal, PlanBlock } from "./api";

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

export function amountText(value: number, unit: Goal["unit"]): string {
  const n = Number.isInteger(value) ? String(value) : value.toFixed(1);
  return unit === "hours" ? `${n} h` : `${n}×`;
}

/** Stable hue per category so blocks keep their colour across views. */
export function categoryHue(category: string): number {
  let h = 0;
  for (const ch of category) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}
