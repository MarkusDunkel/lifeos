import { Search } from "lucide-react";
import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";

export const inputClass =
  "rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm outline-none transition-colors placeholder:text-slate-400 focus:border-accent-500 focus:ring-2 focus:ring-accent-500/20 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900";

export const selectClass = `${inputClass} pr-8`;

export function Input({ className = "", ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`${inputClass} ${className}`} {...rest} />;
}

export function Select({ className = "", children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={`${selectClass} ${className}`} {...rest}>
      {children}
    </select>
  );
}

/** A text field with a magnifier, for filtering lists. */
export function SearchInput({
  value,
  onChange,
  placeholder = "Search",
  className = "",
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}) {
  return (
    <div className={`relative ${className}`}>
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-slate-400" />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className={`${inputClass} w-full pl-8`}
      />
    </div>
  );
}

export type TabItem<T extends string> = { id: T; label: ReactNode; count?: number };

/** Underlined tabs: the sections of one page. */
export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  className = "",
}: {
  tabs: TabItem<T>[];
  value: T;
  onChange: (id: T) => void;
  className?: string;
}) {
  return (
    <div role="tablist" className={`flex gap-1 overflow-x-auto border-b border-slate-200 dark:border-slate-800 ${className}`}>
      {tabs.map((t) => {
        const on = t.id === value;
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => onChange(t.id)}
            className={`-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm whitespace-nowrap transition-colors ${
              on
                ? "border-accent-600 font-medium text-accent-700 dark:border-accent-400 dark:text-accent-300"
                : "border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
            }`}
          >
            {t.label}
            {t.count !== undefined && (
              <span className="rounded-full bg-slate-100 px-1.5 text-[11px] tabular-nums text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                {t.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/** A small switch between a few exclusive choices (ranges, filters, modes). */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  size = "md",
  label,
}: {
  options: { id: T; label: ReactNode; title?: string; disabled?: boolean }[];
  value: T;
  onChange: (id: T) => void;
  size?: "sm" | "md";
  label?: string;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-lg bg-slate-100 p-0.5 dark:bg-slate-800">
      {options.map((o) => {
        const on = o.id === value;
        return (
          <button
            key={o.id}
            type="button"
            title={o.title}
            aria-pressed={on}
            disabled={o.disabled}
            onClick={() => !on && onChange(o.id)}
            className={`rounded-md whitespace-nowrap transition-colors disabled:opacity-50 ${size === "sm" ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-sm"} ${
              on
                ? "bg-white font-medium text-slate-900 shadow-sm dark:bg-slate-600 dark:text-white"
                : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
            }`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
