const KIND: Record<string, string> = { S: "source", D: "dataset", A: "actionable", P: "process" };

/** The global id of a building block (e.g. ``P-46``): kind letter + a number unique across all kinds. */
export function RefBadge({ value, className = "" }: { value?: string; className?: string }) {
  if (!value) return null;
  const kind = KIND[value.split("-")[0]] ?? "block";
  return (
    <span
      title={`Global id of this ${kind} (S source, D dataset, A actionable, P process; the number is unique across all)`}
      className={`shrink-0 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] font-normal text-slate-600 dark:bg-slate-800 dark:text-slate-300 ${className}`}
    >
      {value}
    </span>
  );
}
