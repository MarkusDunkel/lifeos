/** The global id of a building block (``L-n``): unique across processes, datasets and actionables. */
export function RefBadge({ value, className = "" }: { value?: string; className?: string }) {
  if (!value) return null;
  return (
    <span
      title="Global id: unique across all processes, datasets and actionables"
      className={`shrink-0 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] font-normal text-slate-600 dark:bg-slate-800 dark:text-slate-300 ${className}`}
    >
      {value}
    </span>
  );
}
