import { Check, Minus, Pencil, X } from "lucide-react";
import { type ReactNode, useState } from "react";
import type { BlockStatus, DayView, PlanBlock } from "./api";
import { categoryHue, timeline } from "./format";

const STATUS_STYLE: Record<BlockStatus, string> = {
  planned: "",
  done: "",
  partial: "opacity-80",
  skipped: "opacity-50 line-through",
  removed: "hidden",
};

const field = "w-full rounded border border-slate-300 bg-white px-2 py-1 text-sm dark:border-slate-600 dark:bg-slate-900";

/** One day: appointments (grey) and planned blocks (coloured by goal). With ``onStatus`` every
 * block gets check-in buttons (done / partly / skipped); with ``onEdit`` its title and
 * description can be changed (they are the Google event's title and notes). */
export function Timeline({
  day,
  onStatus,
  onEdit,
  compact = false,
}: {
  day: DayView;
  onStatus?: (block: PlanBlock, status: BlockStatus) => void;
  onEdit?: (block: PlanBlock, title: string, description: string) => Promise<unknown>;
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
            <span className="min-w-0 flex-1">{item.appointment.title}</span>
            {!compact && <span className="shrink-0 text-xs text-slate-400">{item.appointment.calendar}</span>}
          </li>
        ) : (
          <BlockItem key={item.block.id} block={item.block} compact={compact} onStatus={onStatus} onEdit={onEdit} />
        ),
      )}
    </ul>
  );
}

function BlockItem({
  block,
  compact,
  onStatus,
  onEdit,
}: {
  block: PlanBlock;
  compact: boolean;
  onStatus?: (block: PlanBlock, status: BlockStatus) => void;
  onEdit?: (block: PlanBlock, title: string, description: string) => Promise<unknown>;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(block.title);
  const [text, setText] = useState(block.reason);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hue = categoryHue(block.category);
  const style = { background: `hsl(${hue} 70% 50% / 0.14)`, borderLeft: `3px solid hsl(${hue} 65% 45%)` };

  if (editing && onEdit) {
    const save = async () => {
      setSaving(true);
      setError(null);
      try {
        await onEdit(block, title, text);
        setEditing(false);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setSaving(false);
      }
    };
    return (
      <li className="space-y-2 rounded px-2 py-2 text-sm" style={style}>
        <div className="flex items-center gap-2">
          <span className="w-24 shrink-0 tabular-nums">
            {block.start}–{block.end}
          </span>
          <input className={field} value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Title" autoFocus />
        </div>
        <textarea
          className={field}
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-label="Description"
          placeholder="What exactly do you want to do? (shown as notes in your calendar)"
        />
        <div className="flex items-center gap-2">
          <button
            onClick={save}
            disabled={saving || !title.trim()}
            className="rounded bg-accent-600 px-3 py-1 text-white hover:bg-accent-700 disabled:opacity-50"
          >
            Save
          </button>
          <button
            onClick={() => {
              setTitle(block.title);
              setText(block.reason);
              setEditing(false);
            }}
            className="rounded border border-slate-300 px-3 py-1 dark:border-slate-600"
          >
            Cancel
          </button>
          {error && <span className="text-xs text-red-600">{error}</span>}
        </div>
      </li>
    );
  }

  return (
    <li className={`flex items-start gap-2 rounded px-2 py-1 text-sm ${STATUS_STYLE[block.status]}`} style={style}>
      <span className="w-24 shrink-0 tabular-nums">
        {block.start}–{block.end}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block font-medium">
          {block.title}
          {block.pinned && (
            <span className="ml-1 text-xs" title="Time set by you in the calendar – the planner plans around it">
              📌
            </span>
          )}
          {block.edited && (
            <span className="ml-1 text-xs font-normal text-accent-700 dark:text-accent-400" title="Set by you – the planner keeps it">
              ✎
            </span>
          )}
        </span>
        {!compact && block.reason && <span className="block whitespace-pre-line text-xs text-slate-500">{block.reason}</span>}
      </span>
      {(onStatus || onEdit) && (
        <span className="flex shrink-0 gap-1">
          {onEdit && (
            <IconButton
              active={false}
              label="Edit title and description"
              onClick={() => {
                setTitle(block.title);
                setText(block.reason);
                setEditing(true);
              }}
            >
              <Pencil className="size-4" />
            </IconButton>
          )}
          {onStatus && (
            <>
              <IconButton active={block.status === "done"} label="Done" onClick={() => onStatus(block, "done")}>
                <Check className="size-4" />
              </IconButton>
              <IconButton active={block.status === "partial"} label="Partly" onClick={() => onStatus(block, "partial")}>
                <Minus className="size-4" />
              </IconButton>
              <IconButton active={block.status === "skipped"} label="Skipped" onClick={() => onStatus(block, "skipped")}>
                <X className="size-4" />
              </IconButton>
            </>
          )}
        </span>
      )}
    </li>
  );
}

function IconButton({
  active,
  label,
  onClick,
  children,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={active}
      title={label}
      onClick={onClick}
      className={`rounded border p-1 ${active ? "border-accent-600 bg-accent-600 text-white" : "border-slate-300 hover:bg-slate-100 dark:border-slate-600 dark:hover:bg-slate-800"}`}
    >
      {children}
    </button>
  );
}
