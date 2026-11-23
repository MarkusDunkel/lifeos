import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useState } from "react";
import { type Goal, weekQuery } from "./api";
import { addDays, amountText, categoryHue, dayLabel, isoDay, progress } from "./format";
import { Timeline } from "./Timeline";

/** Seven days at a glance and how far each weekly goal is. */
export function WeekTab() {
  const today = isoDay(new Date());
  const [start, setStart] = useState<string | undefined>(undefined);
  const week = useQuery(weekQuery(start));
  const w = week.data;
  const go = (days: number) => w && setStart(addDays(w.start, days));
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <button aria-label="Previous week" className="rounded border border-slate-300 p-1 dark:border-slate-700" onClick={() => go(-7)}>
          <ChevronLeft className="size-4" />
        </button>
        <span className="text-sm font-medium">{w ? `Week of ${dayLabel(w.start, today)}` : "…"}</span>
        <button aria-label="Next week" className="rounded border border-slate-300 p-1 dark:border-slate-700" onClick={() => go(7)}>
          <ChevronRight className="size-4" />
        </button>
        {start && (
          <button className="text-sm text-teal-700 underline" onClick={() => setStart(undefined)}>
            this week
          </button>
        )}
      </div>
      {!!w?.goals.length && <GoalBars goals={w.goals} />}
      {week.error && <p className="text-sm text-red-600">{week.error.message}</p>}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {w?.days.map((day) => (
          <section
            key={day.date}
            className={`space-y-2 rounded-xl border p-3 ${day.date === today ? "border-teal-600" : "border-slate-200 dark:border-slate-800"}`}
          >
            <h3 className="text-sm font-semibold">{dayLabel(day.date, today)}</h3>
            <Timeline day={day} compact />
          </section>
        ))}
      </div>
    </div>
  );
}

export function GoalBars({ goals }: { goals: Goal[] }) {
  return (
    <section className="space-y-2 rounded-xl border border-slate-200 p-4 dark:border-slate-800">
      <h2 className="font-semibold">This week</h2>
      <ul className="space-y-2">
        {goals.map((g) => {
          const p = progress(g);
          const hue = categoryHue(g.category);
          return (
            <li key={g.name} className="text-sm">
              <div className="flex justify-between gap-2">
                <span className="font-medium">{g.name}</span>
                <span className="tabular-nums text-slate-500">
                  {amountText(g.done, g.unit)} done · {amountText(g.planned, g.unit)} planned / {amountText(g.target, g.unit)}
                  {g.auto && " (auto)"}
                </span>
              </div>
              <div
                className="relative mt-1 h-2 overflow-hidden rounded bg-slate-100 dark:bg-slate-800"
                role="meter"
                aria-valuemin={0}
                aria-valuemax={g.target}
                aria-valuenow={g.done}
                aria-label={`${g.name} progress`}
              >
                <div className="absolute inset-y-0 left-0" style={{ width: `${p.planned * 100}%`, background: `hsl(${hue} 65% 45% / 0.3)` }} />
                <div className="absolute inset-y-0 left-0" style={{ width: `${p.done * 100}%`, background: `hsl(${hue} 65% 45%)` }} />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
