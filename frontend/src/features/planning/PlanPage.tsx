import { CalendarsTab } from "./CalendarsTab";
import { EveningTab } from "./EveningTab";
import { GoalsTab } from "./GoalsTab";
import { JournalTab } from "./JournalTab";
import { WeekTab } from "./WeekTab";

export const PLAN_TABS = ["evening", "week", "goals", "journal", "calendars"] as const;
export type PlanTab = (typeof PLAN_TABS)[number];

const LABELS: Record<PlanTab, string> = {
  evening: "Evening",
  week: "Week",
  goals: "Goals",
  journal: "Journal",
  calendars: "Calendars",
};

export function PlanPage({ tab, onTab, notice }: { tab: PlanTab; onTab: (tab: PlanTab) => void; notice?: string }) {
  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4 sm:p-8">
      <h1 className="text-2xl font-semibold">Plan</h1>
      <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-slate-200 dark:border-slate-800">
        {PLAN_TABS.map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => onTab(t)}
            className={`-mb-px whitespace-nowrap border-b-2 px-3 py-1.5 text-sm ${tab === t ? "border-teal-600 font-medium" : "border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"}`}
          >
            {LABELS[t]}
          </button>
        ))}
      </div>
      {tab === "evening" && <EveningTab />}
      {tab === "week" && <WeekTab />}
      {tab === "goals" && <GoalsTab />}
      {tab === "journal" && <JournalTab />}
      {tab === "calendars" && <CalendarsTab notice={notice} />}
    </div>
  );
}
