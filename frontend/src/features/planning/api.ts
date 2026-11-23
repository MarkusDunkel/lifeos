import { queryOptions } from "@tanstack/react-query";
import { api } from "../../lib/api";

export type CalendarInput = { id: string; label: string };

export type CalendarSyncReport = {
  at: string;
  inputs: number;
  events: number;
  created: number;
  updated: number;
  deleted: number;
  manual: number;
  errors: string[];
};

export type CalendarStatus = {
  configured: boolean;
  missing: string[];
  connected: boolean;
  account: string | null;
  target: string | null;
  inputs: CalendarInput[];
  last: CalendarSyncReport | null;
};

export type GoogleCalendar = {
  id: string;
  name: string;
  primary: boolean;
  color: string | null;
  input: boolean;
  label: string | null;
  target: boolean;
};

export const calendarStatusQuery = queryOptions({
  queryKey: ["calendar", "status"],
  queryFn: () => api<CalendarStatus>("/api/calendar/status"),
});

export const googleCalendarsQuery = queryOptions({
  queryKey: ["calendar", "calendars"],
  queryFn: () => api<GoogleCalendar[]>("/api/calendar/calendars"),
});

export const connectGoogle = () => api<{ url: string }>("/api/calendar/google/connect", { method: "POST" });
export const disconnectGoogle = () => api("/api/calendar/google/disconnect", { method: "POST" });
export const syncCalendars = () => api<CalendarSyncReport>("/api/calendar/sync", { method: "POST" });
export const saveInputs = (inputs: CalendarInput[]) =>
  api("/api/calendar/inputs", { method: "PUT", json: { inputs } });

// -- planning -----------------------------------------------------------------------------

export type BlockStatus = "planned" | "done" | "partial" | "skipped" | "removed";

export type PlanBlock = {
  id: string;
  date: string;
  start: string;
  end: string;
  category: string;
  goal: string;
  title: string;
  reason: string;
  status: BlockStatus;
  minutes_done: number | null;
  minutes: number;
};

export type Appointment = { start: string; end: string; title: string; calendar: string };

export type JournalEntry = {
  date: string;
  mood: number | null;
  energy: number | null;
  sleep: number | null;
  pain: number | null;
  pain_where: string;
  good: string;
  bad: string;
  notes: string;
};

export type Wish = {
  id: string;
  created: string;
  day: string;
  text: string;
  status: "open" | "done" | "declined";
  answer: string;
  answered?: string;
};

export type DayView = { date: string; blocks: PlanBlock[]; appointments: Appointment[]; allDay: string[] };
export type DayDetail = DayView & { journal: JournalEntry; wishes: Wish[] };

export type GoalUnit = "hours" | "sessions";
export type Goal = {
  name: string;
  category: string;
  amount: number | null;
  unit: GoalUnit;
  priority: string;
  note: string;
  auto: boolean;
  target: number;
  done: number;
  planned: number;
  remaining: number;
};

export type Exam = { subject: string; exam: string; inDays: number; readiness: number | null; minutesPerDay: number | null };

export type GoalsData = {
  goals: Goal[];
  rules: string;
  revision: string | null;
  file: string;
  autoStudy: { hours: number; exams: Exam[] };
};

export type PlannerRun = { at: string; reason: string; status: string; message: string; seconds: number; tokens: number };

export type PlannerStatus = {
  queue: { at: string; reason: string }[];
  runs: PlannerRun[];
  summary: string;
  updated: string | null;
  rejected: string[];
  changes: string[];
  now: string;
};

export type WeekView = { start: string; days: DayView[]; goals: Goal[] };

export const dayQuery = (day: string) =>
  queryOptions({ queryKey: ["planning", "day", day], queryFn: () => api<DayDetail>(`/api/planning/day/${day}`) });

export const weekQuery = (start?: string) =>
  queryOptions({
    queryKey: ["planning", "week", start ?? "current"],
    queryFn: () => api<WeekView>(`/api/planning/week${start ? `?start=${start}` : ""}`),
  });

export const goalsQuery = queryOptions({
  queryKey: ["planning", "goals"],
  queryFn: () => api<GoalsData>("/api/planning/goals"),
});

export const plannerStatusQuery = queryOptions({
  queryKey: ["planning", "status"],
  queryFn: () => api<PlannerStatus>("/api/planning/status"),
});

export const journalQuery = queryOptions({
  queryKey: ["planning", "journal"],
  queryFn: () => api<JournalEntry[]>("/api/planning/journal?days=90"),
});

export type GoalInput = Pick<Goal, "name" | "category" | "amount" | "unit" | "priority" | "note">;

export const saveGoals = (goals: GoalInput[], rules: string, revision: string | null) =>
  api<GoalsData>("/api/planning/goals", { method: "PUT", json: { goals, rules, revision } });

export const setBlockStatus = (id: string, status: BlockStatus, minutes?: number) =>
  api<PlanBlock>(`/api/planning/blocks/${id}/status`, { method: "POST", json: { status, minutes } });

export const saveJournal = (day: string, entry: Omit<JournalEntry, "date">) =>
  api<JournalEntry>(`/api/planning/journal/${day}`, { method: "PUT", json: entry });

export const addWish = (day: string, text: string) =>
  api<Wish>("/api/planning/wishes", { method: "POST", json: { day, text } });

export const deleteWish = (id: string) => api(`/api/planning/wishes/${id}`, { method: "DELETE" });

export const replan = () => api<PlannerStatus>("/api/planning/replan", { method: "POST" });
