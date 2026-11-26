import { CircleAlert, CircleCheck, Info, type LucideIcon, RotateCw, TriangleAlert, X } from "lucide-react";
import type { ReactNode } from "react";

export type Tone = "neutral" | "accent" | "ok" | "warn" | "error" | "ai";

const BADGE: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  accent: "bg-accent-100 text-accent-800 dark:bg-accent-900/50 dark:text-accent-200",
  ok: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
  warn: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
  error: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200",
  ai: "bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300",
};

/** A short label on a tinted pill. */
export function Badge({
  tone = "neutral",
  icon: Icon,
  className = "",
  title,
  children,
}: {
  tone?: Tone;
  icon?: LucideIcon;
  className?: string;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span
      title={title}
      className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium whitespace-nowrap ${BADGE[tone]} ${className}`}
    >
      {Icon && <Icon className="size-3" />}
      {children}
    </span>
  );
}

const NOTICE: Record<Exclude<Tone, "neutral" | "ai">, { box: string; icon: LucideIcon }> = {
  accent: {
    box: "border-accent-200 bg-accent-50 text-accent-900 dark:border-accent-900 dark:bg-accent-950/40 dark:text-accent-100",
    icon: Info,
  },
  ok: {
    box: "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-100",
    icon: CircleCheck,
  },
  warn: {
    box: "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100",
    icon: TriangleAlert,
  },
  error: {
    box: "border-red-200 bg-red-50 text-red-900 dark:border-red-900 dark:bg-red-950/40 dark:text-red-100",
    icon: CircleAlert,
  },
};

/** A message in the page: information, a warning, or an error with a way to try again. */
export function Notice({
  tone = "accent",
  children,
  onRetry,
  onClose,
  className = "",
}: {
  tone?: keyof typeof NOTICE;
  children: ReactNode;
  onRetry?: () => void;
  onClose?: () => void;
  className?: string;
}) {
  const { box, icon: Icon } = NOTICE[tone];
  return (
    <div role={tone === "error" ? "alert" : "status"} className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${box} ${className}`}>
      <Icon className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1 break-words">{children}</div>
      {onRetry && (
        <button type="button" onClick={onRetry} className="inline-flex shrink-0 items-center gap-1 text-xs font-medium underline-offset-2 hover:underline">
          <RotateCw className="size-3.5" /> Retry
        </button>
      )}
      {onClose && (
        <button type="button" onClick={onClose} aria-label="Dismiss" title="Dismiss" className="shrink-0 rounded p-0.5 opacity-70 hover:opacity-100">
          <X className="size-4" />
        </button>
      )}
    </div>
  );
}

/** What a list shows when there is nothing in it yet. */
export function EmptyState({ icon: Icon, title, children }: { icon?: LucideIcon; title: ReactNode; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 px-4 py-8 text-center dark:border-slate-700">
      {Icon && <Icon className="mx-auto mb-2 size-7 text-slate-300 dark:text-slate-600" />}
      <div className="text-sm font-medium text-slate-700 dark:text-slate-200">{title}</div>
      {children && <div className="mx-auto mt-1 max-w-md text-sm text-slate-500 dark:text-slate-400">{children}</div>}
    </div>
  );
}

/** A grey placeholder in the shape of the content that is loading. */
export function Skeleton({ className = "h-4 w-full" }: { className?: string }) {
  return <div aria-hidden className={`animate-pulse rounded-md bg-slate-200/80 dark:bg-slate-800 ${className}`} />;
}

/** Placeholder for a page or a card while its data loads. */
export function Loading({ rows = 3, className = "" }: { rows?: number; className?: string }) {
  return (
    <div role="status" aria-label="Loading" className={`space-y-3 ${className}`}>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className={`h-4 ${i % 3 === 2 ? "w-2/3" : i % 3 === 1 ? "w-5/6" : "w-full"}`} />
      ))}
    </div>
  );
}

/** Loading placeholder or error notice of a query; renders nothing once it has data. */
export function QueryState({
  query,
  rows,
  what = "this page",
}: {
  query: { data?: unknown; error: Error | null; isFetching?: boolean; refetch?: () => unknown };
  rows?: number;
  what?: string;
}) {
  if (query.data !== undefined) return null;
  if (query.error) {
    return (
      <Notice tone="error" onRetry={query.refetch ? () => void query.refetch?.() : undefined}>
        Could not load {what}: {query.error.message}
      </Notice>
    );
  }
  return <Loading rows={rows} />;
}
