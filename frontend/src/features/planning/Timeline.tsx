import { Check, Minus, X } from "lucide-react";
import type { BlockStatus, DayView, PlanBlock } from "./api";
import { categoryHue, timeline } from "./format";

const STATUS_STYLE: Record<BlockStatus, string> = {
  planned: "",
  done: "",
  partial: "opacity-80",
  skipped: "opacity-50 line-through",
  removed: "hidden",
};

/** One day: appointments (grey) and planned blocks (coloured by category). With ``onStatus``
 * every block gets check-in buttons (done / partly / skipped). */
export function Timeline({
  day,
  onStatus,
  compact = false,
}: {
  day: DayView;
  onStatus?: (block: PlanBlock, status: BlockStatus) => void;
  compact?: boolean;
}) {
  const items = timeline(day);
  return (
    <ul className="space-y-1">
      {day.allDay.map((t) => (
        <li key={`all-${t}`} className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
          All day: {t}
        </li>
      ))}
      {items.length === 0 && <li className="text-sm text-slate-400">Nothing planned.</li>}
      {items.map((item) =>
        item.kind === "appointment" ? (
          <li
            key={`a-${item.start}-${item.appointment.title}`}
            className="flex gap-2 rounded border border-slate-200 px-2 py-1 text-sm text-slate-600 dark:border-slate-700 dark:text-slate-300"
          >
            <span className="w-24 shrink-0 tabular-nums text-slate-400">
              {item.start}–{item.end}
            </span>
            <span className="min-w-0 flex-1 truncate">{item.appointment.title}</span>
            {!compact && <span className="shrink-0 text-xs text-slate-400">{item.appointment.calendar}</span>}
          </li>
        ) : (
          <li
            key={item.block.id}
            className={`flex items-start gap-2 rounded px-2 py-1 text-sm ${STATUS_STYLE[item.block.status]}`}
            style={{
              background: `hsl(${categoryHue(item.block.category)} 70% 50% / 0.14)`,
              borderLeft: `3px solid hsl(${categoryHue(item.block.category)} 65% 45%)`,
            }}
            title={item.block.reason}
          >
            <span className="w-24 shrink-0 tabular-nums">
              {item.start}–{item.end}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">{item.block.title}</span>
              {!compact && item.block.reason && (
                <span className="block truncate text-xs text-slate-500">{item.block.reason}</span>
              )}
            </span>
            {onStatus && (
              <span className="flex shrink-0 gap-1">
                <StatusButton active={item.block.status === "done"} label="Done" onClick={() => onStatus(item.block, "done")}>
                  <Check className="size-4" />
                </StatusButton>
                <StatusButton
                  active={item.block.status === "partial"}
                  label="Partly"
                  onClick={() => onStatus(item.block, "partial")}
                >
                  <Minus className="size-4" />
                </StatusButton>
                <StatusButton
                  active={item.block.status === "skipped"}
                  label="Skipped"
                  onClick={() => onStatus(item.block, "skipped")}
                >
                  <X className="size-4" />
                </StatusButton>
              </span>
            )}
          </li>
        ),
      )}
    </ul>
  );
}

function StatusButton({
  active,
  label,
  onClick,
  children,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={active}
      title={label}
      onClick={onClick}
      className={`rounded border p-1 ${active ? "border-teal-600 bg-teal-600 text-white" : "border-slate-300 hover:bg-slate-100 dark:border-slate-600 dark:hover:bg-slate-800"}`}
    >
      {children}
    </button>
  );
}
