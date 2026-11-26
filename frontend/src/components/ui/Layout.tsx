import type { LucideIcon } from "lucide-react";
import type { HTMLAttributes, ReactNode } from "react";

const WIDTH = {
  sm: "max-w-xl",
  md: "max-w-3xl",
  lg: "max-w-5xl",
  xl: "max-w-7xl",
} as const;

/** The classes of a page frame, for pages that need their own element. */
export const pageClass = (width: keyof typeof WIDTH = "lg") => `mx-auto space-y-5 p-4 sm:p-6 lg:p-8 ${WIDTH[width]}`;

/** The frame of a page: one width scale and the same padding everywhere. */
export function Page({
  width = "lg",
  className = "",
  children,
}: {
  width?: keyof typeof WIDTH;
  className?: string;
  children: ReactNode;
}) {
  return <div className={`${pageClass(width)} ${className}`}>{children}</div>;
}

/** Title row of a page: icon, title, what the page is for, and its actions on the right. */
export function PageHeader({
  icon: Icon,
  title,
  subtitle,
  actions,
  children,
}: {
  icon?: LucideIcon;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  /** Shown beside the title (e.g. a status pill). */
  children?: ReactNode;
}) {
  return (
    <header className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="flex items-center gap-2.5 text-2xl font-semibold tracking-tight">
          {Icon && <Icon className="size-6 shrink-0 text-accent-600 dark:text-accent-400" />}
          {title}
        </h1>
        {children}
        {actions && <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {subtitle && <p className="max-w-3xl text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>}
    </header>
  );
}

export const cardClass =
  "rounded-xl border border-slate-200 bg-white shadow-xs dark:border-slate-800 dark:bg-slate-900/40";

/** A bordered surface; ``padded`` adds the usual inner spacing. */
export function Card({
  padded = true,
  className = "",
  children,
  ...rest
}: HTMLAttributes<HTMLElement> & { padded?: boolean }) {
  return (
    <section className={`${cardClass} ${padded ? "space-y-3 p-4" : ""} ${className}`} {...rest}>
      {children}
    </section>
  );
}

/** Heading of a card or a section, with an optional hint below and actions on the right. */
export function SectionTitle({
  children,
  hint,
  actions,
}: {
  children: ReactNode;
  hint?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start gap-2">
      <div className="min-w-0 flex-1">
        <h2 className="font-semibold">{children}</h2>
        {hint && <p className="text-xs text-slate-500 dark:text-slate-400">{hint}</p>}
      </div>
      {actions}
    </div>
  );
}

/** A number with its label (and a line below), for summary rows. */
export function Stat({ label, value, sub, title }: { label: ReactNode; value: ReactNode; sub?: ReactNode; title?: string }) {
  return (
    <div className="min-w-0 rounded-lg bg-slate-50 p-3 dark:bg-slate-900" title={title}>
      <div className="text-xs text-slate-500 dark:text-slate-400">{label}</div>
      <div className="truncate text-2xl font-semibold tabular-nums">{value}</div>
      {sub && <div className="truncate text-xs text-slate-500 dark:text-slate-400">{sub}</div>}
    </div>
  );
}
