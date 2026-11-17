import type { Bank, CardStatus, Exam, Progress, StudyCard, SubjectSummary } from "./api";

export type ExamRow = Exam & {
  subject: string;
  name: string;
  cards: number;
  secure: number;
  dueNow: number;
  minutesPerDay: number | null;
};

/** Readiness of the topics an exam covers (all topics when the exam names none). */
export function topicStats(progress: Progress, topics: string[]) {
  const entries = topics.length
    ? topics.map((t) => progress.topics[t]).filter((t) => t !== undefined)
    : Object.values(progress.topics);
  const sum = (f: (t: (typeof entries)[number]) => number) => entries.reduce((n, t) => n + f(t), 0);
  const perDay = entries.filter((t) => t.minutesPerDay !== null);
  return {
    cards: sum((t) => t.cards),
    secure: sum((t) => t.secure),
    dueNow: sum((t) => t.dueNow),
    minutesPerDay: perDay.length ? perDay.reduce((n, t) => n + (t.minutesPerDay ?? 0), 0) : null,
  };
}

/** Every exam of every subject: upcoming ones soonest first, past ones latest first. */
export function examRows(subjects: SubjectSummary[]): { upcoming: ExamRow[]; past: ExamRow[] } {
  const rows: ExamRow[] = subjects.flatMap((s) =>
    s.exams.map((e) => ({ ...e, subject: s.key, name: s.name, ...topicStats(s.progress, e.topics) })),
  );
  const time = (r: ExamRow) => new Date(r.date).getTime();
  return {
    upcoming: rows.filter((r) => !r.past).sort((a, b) => time(a) - time(b)),
    past: rows.filter((r) => r.past).sort((a, b) => time(b) - time(a)),
  };
}

const DAY = 86_400_000;
const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

/** "today", "tomorrow", "in 4 days", "yesterday", "3 days ago" (by calendar day). */
export function countdown(date: string, now = new Date()): string {
  const days = Math.round((startOfDay(new Date(date)) - startOfDay(now)) / DAY);
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  return days > 0 ? `in ${days} days` : `${-days} days ago`;
}

/** Date with weekday; the time only when one was given (a date alone means 08:00). */
export function formatExamDate(date: string): string {
  const d = new Date(date);
  const day = d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  if (d.getHours() === 8 && d.getMinutes() === 0) return day;
  return `${day}, ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`;
}

export type CardFilter = "all" | "shaky" | "new";

const SHAKY: CardStatus[] = ["wrong", "partial", "learning"];

export function filterCards(cards: StudyCard[], filter: CardFilter): StudyCard[] {
  if (filter === "new") return cards.filter((c) => c.status === "new");
  if (filter === "shaky") return cards.filter((c) => SHAKY.includes(c.status));
  return cards;
}

/** Question banks grouped by their exam date (soonest first, banks without a date last). */
export function banksByExam(banks: Bank[]): { exam: string | null; banks: Bank[] }[] {
  const groups = new Map<string | null, Bank[]>();
  for (const bank of banks) groups.set(bank.exam, [...(groups.get(bank.exam) ?? []), bank]);
  const time = (e: string | null) => (e === null ? Infinity : new Date(e).getTime());
  return [...groups.entries()].sort(([a], [b]) => time(a) - time(b)).map(([exam, banks]) => ({ exam, banks }));
}

export const STATUS_LABEL: Record<CardStatus, string> = {
  new: "new",
  wrong: "wrong",
  partial: "partial",
  learning: "learning",
  secure: "secure",
};

export const STATUS_COLOR: Record<CardStatus, string> = {
  new: "bg-slate-300 dark:bg-slate-600",
  wrong: "bg-red-500",
  partial: "bg-amber-500",
  learning: "bg-sky-500",
  secure: "bg-emerald-500",
};
