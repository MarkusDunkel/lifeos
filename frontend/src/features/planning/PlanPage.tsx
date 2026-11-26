import { CalendarCheck } from "lucide-react";
import { Page, PageHeader, Tabs } from "../../components/ui";
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
    <Page width="lg">
      <PageHeader icon={CalendarCheck} title="Plan" />
      <Tabs value={tab} onChange={onTab} tabs={PLAN_TABS.map((t) => ({ id: t, label: LABELS[t] }))} />
      {tab === "evening" && <EveningTab />}
      {tab === "week" && <WeekTab />}
      {tab === "goals" && <GoalsTab />}
      {tab === "journal" && <JournalTab />}
      {tab === "calendars" && <CalendarsTab notice={notice} />}
    </Page>
  );
}
