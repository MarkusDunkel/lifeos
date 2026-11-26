import { ChevronRight, Flag, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { useStored } from "../../lib/useMediaQuery";
import type { Level, Where } from "./api";
import { WHERE_ICON, WHERE_LABEL } from "./ControlFlow";
import { LEVEL_STYLE } from "./layout";

const LEVELS: [Level, string][] = [
  ["ok", "OK"],
  ["warn", "late, skipped or waiting while the worker is busy"],
  ["fail", "failed, precondition missing or overdue"],
  ["off", "not set up"],
];

function Entry({ sample, children }: { sample: ReactNode; children: ReactNode }) {
  return (
    <li className="flex items-center gap-2">
      <span className="flex w-14 shrink-0 justify-center">{sample}</span>
      <span>{children}</span>
    </li>
  );
}

/** What the page shows and what its shapes, colours and icons mean; folded away by default. */
export function Legend() {
  const [open, setOpen] = useStored<"open" | "closed">("lifeos.control.legend", "closed", ["open", "closed"]);
  return (
    <details
      open={open === "open"}
      onToggle={(e) => setOpen(e.currentTarget.open ? "open" : "closed")}
      className="group rounded-xl border border-slate-200 dark:border-slate-800"
    >
      <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded-xl px-4 py-2.5 text-sm font-medium text-slate-600 select-none hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-900">
        <ChevronRight className="size-4 transition-transform group-open:rotate-90" /> How to read this page
      </summary>
      <p className="px-4 pb-3 text-sm text-slate-500 dark:text-slate-400">
        Every process that feeds LifeOS: where it runs, when it last succeeded and whether it ran often enough. Red
        means failed, a missing precondition or overdue; yellow means late, skipped or waiting while the worker is
        busy with a long run. Hover a box to follow its flow, click it for details.
      </p>
      <LegendGrid />
    </details>
  );
}

function LegendGrid() {
  return (
    <section className="grid gap-4 border-t border-slate-100 p-4 text-xs text-slate-600 sm:grid-cols-2 lg:grid-cols-4 dark:border-slate-800 dark:text-slate-300">
      <div>
        <h3 className="mb-2 font-medium text-slate-800 dark:text-slate-100">Shapes</h3>
        <ul className="space-y-1.5">
          <Entry sample={<span className="h-4 w-12 rounded-md border border-slate-300 bg-white dark:border-slate-700 dark:bg-slate-900" />}>
            Source: read from, not monitored
          </Entry>
          <Entry sample={<span className="h-4 w-12 rounded-full border border-emerald-300 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/40" />}>
            Process: something that runs
          </Entry>
          <Entry sample={<span className="h-4 w-12 rounded-md border border-emerald-300 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/40" />}>
            Dataset: data LifeOS keeps; shows the worst light of the processes writing it (red: may be out of date)
          </Entry>
          <Entry sample={<span className="h-4 w-12 rounded-md border-2 border-dashed border-sky-300 dark:border-sky-800" />}>
            In the vault: synced to the vault repository as a whole
          </Entry>
          <Entry
            sample={
              <span className="flex h-4 w-12 items-center justify-end rounded-r-md border border-l-[6px] border-emerald-300 bg-emerald-50 pr-1 dark:border-emerald-800 dark:bg-emerald-950/40">
                <Flag className="size-2.5 text-slate-500" />
              </span>
            }
          >
            Actionable: a dataset you act on; its light says whether every process before it ran
          </Entry>
          <Entry sample={<span className="h-0 w-12 border-t-2 border-dotted border-slate-300 dark:border-slate-700" />}>
            Second order (below): actionables and the control room as data for new actionables
          </Entry>
          <Entry sample={<span className="h-0 w-12 border-t-2 border-dotted border-slate-300 dark:border-slate-700" />}>
            Technical foundation (bottom): carries everything, ends in no actionable
          </Entry>
        </ul>
      </div>
      <div>
        <h3 className="mb-2 font-medium text-slate-800 dark:text-slate-100">Lights</h3>
        <ul className="space-y-1.5">
          {LEVELS.map(([level, text]) => (
            <Entry key={level} sample={<span className={`size-2.5 rounded-full ${LEVEL_STYLE[level].dot}`} />}>
              {text}
            </Entry>
          ))}
        </ul>
      </div>
      <div>
        <h3 className="mb-2 font-medium text-slate-800 dark:text-slate-100">Where it runs</h3>
        <ul className="space-y-1.5">
          {(Object.keys(WHERE_ICON) as Where[]).map((where) => {
            const Icon = WHERE_ICON[where];
            return (
              <Entry key={where} sample={<Icon className="size-4 text-slate-400" />}>
                {WHERE_LABEL[where]}
              </Entry>
            );
          })}
        </ul>
      </div>
      <div>
        <h3 className="mb-2 font-medium text-slate-800 dark:text-slate-100">More</h3>
        <ul className="space-y-1.5">
          <Entry sample={<Sparkles className="size-4 text-violet-500" />}>
            Calls an AI model (when there is work); the rest is plain code
          </Entry>
          <Entry sample={<span className="text-[10px] text-slate-500">mirror ·</span>}>
            Mirror: hand edits in its target are overwritten
          </Entry>
          <Entry sample={<span className="h-0.5 w-10 bg-[repeating-linear-gradient(90deg,#ef4444_0_4px,transparent_4px_7px)]" />}>
            Moving dashes: the flow is broken here
          </Entry>
          <Entry sample={<span className="h-0 w-10 border-t-2 border-dashed border-violet-400" />}>
            Your reactions on an actionable (moved tickets, comments) trigger this process
          </Entry>
          <Entry
            sample={
              <span className="relative flex size-2.5">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-accent-400 opacity-75" />
                <span className="relative inline-flex size-2.5 rounded-full bg-accent-500" />
              </span>
            }
          >
            Running right now (the worker runs one job at a time)
          </Entry>
        </ul>
      </div>
    </section>
  );
}
