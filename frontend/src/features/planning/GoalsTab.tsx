import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { type GoalInput, goalsQuery, saveGoals } from "./api";
import { btn } from "./CalendarsTab";

const input = "rounded border border-slate-300 bg-transparent px-2 py-1 text-sm dark:border-slate-700";
const PRIORITIES = ["hoch", "mittel", "niedrig"];
const PRIORITY_LABEL: Record<string, string> = { hoch: "high", mittel: "medium", niedrig: "low" };
// goal | amount | unit | priority | remove
const ROW = "grid gap-2 sm:grid-cols-[2fr_1.6fr_1fr_1fr_auto]";

/** A form field with its label on phones; on wider screens the column header names it. */
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <span className="mb-0.5 block text-xs text-slate-500 sm:hidden">{label}</span>
      {children}
    </div>
  );
}

/** Weekly quotas and the soft rules the planner follows (stored in the vault as Goals.md). */
export function GoalsTab() {
  const queryClient = useQueryClient();
  const data = useQuery(goalsQuery);
  const [goals, setGoals] = useState<GoalInput[] | null>(null);
  const [rules, setRules] = useState("");
  const [revision, setRevision] = useState<string | null>(null);

  useEffect(() => {
    if (data.data && goals === null) {
      setGoals(data.data.goals.map(({ name, category, amount, unit, priority, note }) => ({ name, category, amount, unit, priority, note })));
      setRules(data.data.rules);
      setRevision(data.data.revision);
    }
  }, [data.data, goals]);

  const save = useMutation({
    mutationFn: () => saveGoals((goals ?? []).filter((g) => g.name.trim()), rules, revision),
    onSuccess: (saved) => {
      setRevision(saved.revision);
      queryClient.setQueryData(goalsQuery.queryKey, saved);
      queryClient.invalidateQueries({ queryKey: ["planning"] });
    },
  });

  const update = (i: number, patch: Partial<GoalInput>) =>
    setGoals((cur) => (cur ?? []).map((g, j) => (j === i ? { ...g, ...patch } : g)));

  if (!data.data || goals === null) {
    return <p className="text-sm text-slate-400">{data.error ? data.error.message : "Loading…"}</p>;
  }
  const auto = data.data.autoStudy;
  const autoShort = auto.exams.length
    ? `${auto.exams.length} upcoming exam${auto.exams.length > 1 ? "s" : ""}`
    : "no upcoming exam";
  const autoReason = auto.exams.length
    ? "Computed from your upcoming exams and how ready you are (Study). Grows as an exam gets closer."
    : "Computed from upcoming exams (Study). None is entered right now, so the planner only plans time for Moodle deadlines. Enter exam dates or set a fixed amount.";
  return (
    <div className="space-y-4">
      <section className="space-y-3 rounded-xl border border-slate-200 p-4 dark:border-slate-800">
        <h2 className="font-semibold">Weekly quotas</h2>
        <p className="text-xs text-slate-500">
          What you want to reach each week (Monday to Sunday). The planner spreads it over the free time of the week.
        </p>
        <div className={`${ROW} hidden px-2 text-xs font-medium text-slate-500 sm:grid`}>
          <span title="Shown to the planner and in every calendar block">Goal</span>
          <span title="How much per week. 'auto': LifeOS computes it (study hours from upcoming exams and readiness)">
            Amount per week
          </span>
          <span title="Hours per week, or number of sessions per week">Unit</span>
          <span title="When the week is too full, the planner cuts low first, then medium">Priority</span>
          <span />
        </div>
        <div className="space-y-2">
          {goals.map((g, i) => (
            <div key={i} className={`${ROW} grid-cols-2 rounded-lg bg-slate-50 p-2 dark:bg-slate-900`}>
              <Field label="Goal">
                <input className={`${input} w-full`} placeholder="e.g. Programming" value={g.name} onChange={(e) => update(i, { name: e.target.value })} />
              </Field>
              <Field label="Amount per week">
                <div className="flex items-center gap-2">
                  {g.amount === null ? (
                    <span className="flex-1 text-sm text-slate-600 dark:text-slate-300" title={autoReason}>
                      = {auto.hours} h <span className="text-xs text-slate-400">({autoShort})</span>
                    </span>
                  ) : (
                    <input
                      className={`${input} w-full`}
                      type="number"
                      min={0}
                      step={0.5}
                      value={g.amount}
                      onChange={(e) => update(i, { amount: e.target.value === "" ? 0 : Number(e.target.value) })}
                    />
                  )}
                  <label className="flex items-center gap-1 text-xs text-slate-500" title={autoReason}>
                    <input
                      type="checkbox"
                      checked={g.amount === null}
                      onChange={(e) => update(i, { amount: e.target.checked ? null : auto.hours || 1, unit: "hours" })}
                    />
                    auto
                  </label>
                </div>
              </Field>
              <Field label="Unit">
                <select
                  className={`${input} w-full`}
                  value={g.unit}
                  disabled={g.amount === null}
                  onChange={(e) => update(i, { unit: e.target.value as GoalInput["unit"] })}
                >
                  <option value="hours">hours</option>
                  <option value="sessions">sessions</option>
                </select>
              </Field>
              <Field label="Priority">
                <select className={`${input} w-full`} value={g.priority} onChange={(e) => update(i, { priority: e.target.value })}>
                  {[...new Set([...PRIORITIES, g.priority])].map((p) => (
                    <option key={p} value={p}>
                      {PRIORITY_LABEL[p] ?? p}
                    </option>
                  ))}
                </select>
              </Field>
              <button
                aria-label="Remove goal"
                title="Remove goal"
                className="self-center justify-self-end text-slate-400 hover:text-red-600"
                onClick={() => setGoals(goals.filter((_, j) => j !== i))}
              >
                <Trash2 className="size-4" />
              </button>
              <input
                className={`${input} col-span-2 sm:col-span-5`}
                placeholder="Note for the planner, e.g. which project, how long one session is"
                value={g.note}
                onChange={(e) => update(i, { note: e.target.value })}
              />
            </div>
          ))}
        </div>
        <button
          className={btn}
          onClick={() => setGoals([...goals, { name: "", category: "", amount: 1, unit: "hours", priority: "mittel", note: "" }])}
        >
          <Plus className="size-4" /> Add goal
        </button>
      </section>

      <section className="space-y-2 rounded-xl border border-slate-200 p-4 dark:border-slate-800">
        <h2 className="font-semibold">Rules</h2>
        <p className="text-xs text-slate-500">
          Plain text, one rule per line. The planner reads them as soft rules, e.g. "Work 8–17", "Lunch break 12–13",
          "Focus blocks of 45 min, then 15 min break", "No sport right after eating".
        </p>
        <textarea value={rules} onChange={(e) => setRules(e.target.value)} rows={8} className={`${input} w-full font-mono`} />
      </section>

      {!!auto.exams.length && (
        <section className="space-y-1 rounded-xl border border-slate-200 p-4 text-sm dark:border-slate-800">
          <h2 className="font-semibold">Upcoming exams (for "auto")</h2>
          <ul>
            {auto.exams.map((x) => (
              <li key={x.subject + x.exam}>
                {x.subject}: {new Date(x.exam).toLocaleDateString()} (in {x.inDays} days), readiness {x.readiness ?? "?"}%,
                ~{x.minutesPerDay ?? "?"} min/day
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="flex items-center gap-3">
        <button className={btn} onClick={() => save.mutate()} disabled={save.isPending}>
          Save goals
        </button>
        <span className="text-xs text-slate-400">knowledge / {data.data.file} · saving re-plans the coming days</span>
        {save.isSuccess && <span className="text-sm text-emerald-600">Saved.</span>}
        {save.error && (
          <span className="text-sm text-red-600">
            {save.error.message === "file changed on disk" ? "The file changed meanwhile – reload the page." : save.error.message}
          </span>
        )}
      </div>
    </div>
  );
}
