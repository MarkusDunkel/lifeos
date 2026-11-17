import type { Salary } from "./api";

const UNIT: Record<string, string> = { YEAR: "year", MONTH: "month", HOUR: "hour" };

/** "from €54,000 / year" — devjobs.at publishes minimum salaries. */
export function formatSalary(salary: Salary | null): string {
  if (!salary) return "";
  const amount = new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: salary.currency || "EUR",
    maximumFractionDigits: 0,
  }).format(salary.amount);
  return `from ${amount} / ${UNIT[salary.unit] ?? salary.unit.toLowerCase()}`;
}

/** "today", "yesterday", "3 days ago", otherwise the date. */
export function ago(iso: string | null, now: Date = new Date()): string {
  if (!iso) return "";
  const then = new Date(iso);
  const day = (d: Date) => Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  const days = Math.round((day(now) - day(then)) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  return then.toLocaleDateString();
}

const EMPLOYMENT: Record<string, string> = {
  FULL_TIME: "full-time",
  PART_TIME: "part-time",
  CONTRACTOR: "freelance",
  INTERN: "internship",
  TEMPORARY: "temporary",
};

export function formatEmployment(types: string[]): string {
  return types.map((t) => EMPLOYMENT[t] ?? t.toLowerCase().replace(/_/g, " ")).join(", ");
}
