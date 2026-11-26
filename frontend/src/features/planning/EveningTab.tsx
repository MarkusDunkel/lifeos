import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import {
  type BlockStatus,
  type JournalEntry,
  type PlanBlock,
  dayQuery,
  editBlock,
  plannerStatusQuery,
  replan,
  saveJournal,
  setBlockStatus,
} from "./api";
import { btn } from "./CalendarsTab";
import { addDays, dayLabel, isoDay } from "./format";
import { PlannerNote } from "./PlannerNote";
import { Timeline } from "./Timeline";
import { cardClass, QueryState } from "../../components/ui";

const card = `${cardClass} space-y-3 p-4`;

/** The evening screen: check off today, write the journal, plan tomorrow. */
export function EveningTab() {
  const today = isoDay(new Date());
  const tomorrow = addDays(today, 1);
  return (
    <div className="space-y-4">
      <PlannerCard />
      <div className="grid gap-4 lg:grid-cols-2">
        <TodayCard day={today} />
        <DayCard day={tomorrow} today={today} />
      </div>
      <JournalCard day={today} />
    </div>
  );
}

function useRefresh() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["planning"] });
}

export function PlannerCard() {
  const refresh = useRefresh();
  const status = useQuery({
    ...plannerStatusQuery,
    // while something is queued or running, look again regularly
    refetchInterval: (q) => {
      const d = q.state.data;
      return d && (d.running || (d.queue.length && !d.limitReached)) ? 10_000 : false;
    },
  });
  const s = status.data;
  const waiting = !!s?.queue.length;
  const blocked = waiting && !!s?.limitReached && !s?.running;
  const busy = !!s?.running || (waiting && !blocked);
  const [wasBusy, setWasBusy] = useState(false);
  useEffect(() => {
    if (wasBusy && !busy) refresh();
    setWasBusy(busy);
  }, [busy]); // eslint-disable-line react-hooks/exhaustive-deps
  const again = useMutation({ mutationFn: replan, onSettled: refresh });
  const last = s?.runs[0];
  return (
    <section className={card}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">Planner</h2>
        <button
          className={btn}
          onClick={() => again.mutate()}
          disabled={again.isPending || busy || !!s?.limitReached}
          title={s?.limitReached ? "Daily limit reached – runs again tomorrow" : undefined}
        >
          {busy ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          {busy ? "Planning…" : "Plan again"}
        </button>
      </div>
      {s?.running && (
        <p className="text-xs text-slate-500">
          Planning now ({s.running.reason}), started {new Date(s.running.since).toLocaleTimeString()} – usually 1–2
          minutes.
        </p>
      )}
      {!s?.running && waiting && !blocked && (
        <p className="text-xs text-slate-500">
          Waiting: {s?.queue.map((q) => q.reason).join(", ")} — the planner picks this up within a minute.
        </p>
      )}
      {!!s?.pendingNotes.length && !busy && (
        <p className="rounded bg-accent-50 px-2 py-1 text-xs text-accent-800 dark:bg-accent-950 dark:text-accent-200">
          {s.pendingNotes.length === 1 ? "1 note is" : `${s.pendingNotes.length} notes are`} not planned yet (
          {s.pendingNotes.join(", ")}) – press “Plan again”.
        </p>
      )}
      {s?.limitReached && (
        <p className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-700 dark:bg-amber-950 dark:text-amber-300">
          Daily limit reached ({s.runsToday}/{s.dailyLimit} runs).{" "}
          {blocked
            ? `Waiting until tomorrow: ${s.queue.map((q) => q.reason).join(", ")}.`
            : "New changes are planned tomorrow."}{" "}
          The limit is <code>daily_limit</code> in agents/calendar-planner.md.
        </p>
      )}
      {s?.summary ? <p className="text-sm">{s.summary}</p> : <p className="text-sm text-slate-400">No plan yet.</p>}
      {!!s?.changes.length && (
        <ul className="list-disc pl-5 text-xs text-slate-500">
          {s.changes.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      )}
      {!!s?.rejected.length && (
        <details className="text-xs text-amber-600">
          <summary>{s.rejected.length} block(s) were rejected by the checks</summary>
          <ul className="list-disc pl-5">
            {s.rejected.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </details>
      )}
      {last && (
        <p className="text-xs text-slate-400">
          Last run {new Date(last.at).toLocaleString()} ({last.reason}, {last.seconds}s): {last.message}
        </p>
      )}
    </section>
  );
}

function TodayCard({ day }: { day: string }) {
  const refresh = useRefresh();
  const data = useQuery(dayQuery(day));
  const check = useMutation({
    mutationFn: ({ block, status }: { block: PlanBlock; status: BlockStatus }) => {
      let minutes: number | undefined;
      if (status === "partial") {
        const answer = window.prompt(`How many of the ${block.minutes} minutes did you do?`, String(Math.round(block.minutes / 2)));
        if (answer === null) return Promise.resolve(block);
        minutes = Math.max(0, Math.min(block.minutes, Number(answer) || 0));
      }
      return setBlockStatus(block.id, status, minutes);
    },
    onSettled: refresh,
  });
  return (
    <section className={card}>
      <h2 className="font-semibold">Today – how did it go?</h2>
      <p className="text-xs text-slate-500">Unmarked blocks count as done.</p>
      {data.data ? (
        <>
          <PlannerNote day={day} thread={data.data.thread} />
          <Timeline day={data.data} onStatus={(block, status) => check.mutate({ block, status })} />
        </>
      ) : (
        <QueryState query={data} />
      )}
    </section>
  );
}

function DayCard({ day, today }: { day: string; today: string }) {
  const refresh = useRefresh();
  const data = useQuery(dayQuery(day));
  return (
    <section className={card}>
      <h2 className="font-semibold">{dayLabel(day, today)} – what exactly will you do?</h2>
      <p className="text-xs text-slate-500">
        Edit a block (✎) to say what you plan for it. Title and text go to your calendar; the planner keeps them and
        lets them guide the next sessions.
      </p>
      {data.data ? (
        <>
          <PlannerNote day={day} thread={data.data.thread} />
          <Timeline day={data.data} onEdit={(block, title, text) => editBlock(block.id, title, text).then(refresh)} />
        </>
      ) : (
        <QueryState query={data} />
      )}
    </section>
  );
}

const EMPTY: Omit<JournalEntry, "date"> = {
  mood: null,
  energy: null,
  sleep: null,
  pain: null,
  pain_where: "",
  good: "",
  bad: "",
  notes: "",
};

function JournalCard({ day }: { day: string }) {
  const refresh = useRefresh();
  const data = useQuery(dayQuery(day));
  const [entry, setEntry] = useState<Omit<JournalEntry, "date"> | null>(null);
  useEffect(() => {
    if (data.data && entry === null) {
      const { date: _, ...rest } = data.data.journal;
      setEntry(rest);
    }
  }, [data.data, entry]);
  const save = useMutation({ mutationFn: () => saveJournal(day, entry ?? EMPTY), onSuccess: refresh });
  const e = entry ?? EMPTY;
  const set = (patch: Partial<typeof e>) => setEntry({ ...e, ...patch });

  return (
    <section className={card}>
      <h2 className="font-semibold">Journal</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <Scale label="Mood" value={e.mood} max={5} onChange={(mood) => set({ mood })} />
        <Scale label="Energy" value={e.energy} max={5} onChange={(energy) => set({ energy })} />
        <label className="flex items-center gap-2 text-sm">
          <span className="w-16 text-slate-500">Sleep</span>
          <input
            type="number"
            min={0}
            max={24}
            step={0.5}
            value={e.sleep ?? ""}
            onChange={(ev) => set({ sleep: ev.target.value === "" ? null : Number(ev.target.value) })}
            className="w-20 rounded border border-slate-300 bg-transparent px-2 py-1 dark:border-slate-700"
          />
          <span className="text-slate-500">h</span>
        </label>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Scale label="Pain" value={e.pain} max={10} min={0} onChange={(pain) => set({ pain })} />
          {!!e.pain && (
            <input
              placeholder="where?"
              value={e.pain_where}
              onChange={(ev) => set({ pain_where: ev.target.value })}
              className="w-28 rounded border border-slate-300 bg-transparent px-2 py-1 dark:border-slate-700"
            />
          )}
        </div>
      </div>
      <TextArea label="What went well?" value={e.good} onChange={(good) => set({ good })} />
      <TextArea label="What did not?" value={e.bad} onChange={(bad) => set({ bad })} />
      <TextArea label="Notes (sport, body, anything)" value={e.notes} onChange={(notes) => set({ notes })} />
      <div className="flex items-center gap-3">
        <button className={btn} onClick={() => save.mutate()} disabled={save.isPending || entry === null}>
          Save journal
        </button>
        {save.isSuccess && <span className="text-sm text-emerald-600">Saved.</span>}
        {save.error && <span className="text-sm text-red-600">{save.error.message}</span>}
      </div>
    </section>
  );
}

function Scale({
  label,
  value,
  max,
  min = 1,
  onChange,
}: {
  label: string;
  value: number | null;
  max: number;
  min?: number;
  onChange: (v: number | null) => void;
}) {
  const values = Array.from({ length: max - min + 1 }, (_, i) => i + min);
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="w-16 text-slate-500">{label}</span>
      <div className="flex flex-wrap gap-1">
        {values.map((v) => (
          <button
            key={v}
            type="button"
            aria-pressed={value === v}
            onClick={() => onChange(value === v ? null : v)}
            className={`size-7 rounded border text-xs ${value === v ? "border-accent-600 bg-accent-600 text-white" : "border-slate-300 hover:bg-slate-100 dark:border-slate-600 dark:hover:bg-slate-800"}`}
          >
            {v}
          </button>
        ))}
      </div>
    </div>
  );
}

function TextArea({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="block text-sm">
      <span className="text-slate-500">{label}</span>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={2}
        className="mt-1 w-full rounded border border-slate-300 bg-transparent px-2 py-1 dark:border-slate-700"
      />
    </label>
  );
}
