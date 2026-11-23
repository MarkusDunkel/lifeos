import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderPlus, Plus, Trash2 } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { type GoalInput, goalsQuery, saveGoals } from "./api";
import { btn } from "./CalendarsTab";
import { groupGoals, type ResolvedGoal, resolveGoals } from "./format";

const input = "rounded border border-slate-300 bg-transparent px-2 py-1 text-sm dark:border-slate-700";
const PRIORITIES = ["hoch", "mittel", "niedrig"];
const PRIORITY_LABEL: Record<string, string> = { hoch: "high", mittel: "medium", niedrig: "low" };
// goal | amount | unit | priority | remove
const ROW = "grid gap-2 sm:grid-cols-[2fr_1.6fr_1.2fr_1fr_auto]";

const newGoal = (group = ""): GoalInput => ({
  name: "",
  group,
  amount: group ? 10 : 1,
  unit: group ? "percent" : "hours",
  priority: "mittel",
  note: "",
});

/** Weekly quotas (optionally in groups) and the soft rules the planner follows (Goals.md). */
export function GoalsTab() {
  const queryClient = useQueryClient();
  const data = useQuery(goalsQuery);
  const [goals, setGoals] = useState<GoalInput[] | null>(null);
  const [rules, setRules] = useState("");
  const [revision, setRevision] = useState<string | null>(null);

  useEffect(() => {
    if (data.data && goals === null) {
      setGoals(data.data.goals.map(({ name, group, amount, unit, priority, note }) => ({ name, group, amount, unit, priority, note })));
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

  const autoHours = data.data?.autoStudy.hours ?? 0;
  const resolved = useMemo(() => resolveGoals(goals ?? [], autoHours), [goals, autoHours]);

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

  const { top, members } = groupGoals(goals);
  const indexOf = (g: GoalInput) => goals.indexOf(g);
  const update = (i: number, patch: Partial<GoalInput>) =>
    setGoals((cur) => {
      const list = [...(cur ?? [])];
      const before = list[i];
      list[i] = { ...before, ...patch };
      // renaming a group renames the members' reference too
      if (patch.name !== undefined && patch.name !== before.name) {
        for (let j = 0; j < list.length; j++) if (list[j].group === before.name) list[j] = { ...list[j], group: patch.name };
      }
      return list;
    });
  const remove = (i: number) =>
    setGoals((cur) => {
      const list = cur ?? [];
      const gone = list[i];
      // members of a removed group become goals of their own
      return list.filter((_, j) => j !== i).map((g) => (g.group === gone.name ? { ...g, group: "" } : g));
    });
  const groupNames = top.filter((g) => g.unit === "hours" && g.amount !== null && g.name).map((g) => g.name);

  const row = (g: GoalInput, member: boolean) => {
    const i = indexOf(g);
    const r = resolved.get(g.name);
    const isGroup = !!members.get(g.name)?.length;
    return (
      <div key={i} className={`${ROW} grid-cols-2 rounded-lg p-2 ${isGroup ? "bg-slate-100 dark:bg-slate-800" : "bg-slate-50 dark:bg-slate-900"}`}>
        <Field label="Goal">
          <input
            className={`${input} w-full ${isGroup ? "font-semibold" : ""}`}
            placeholder={member ? "e.g. LifeOS" : "e.g. Work or Sport"}
            value={g.name}
            onChange={(e) => update(i, { name: e.target.value })}
          />
        </Field>
        <Field label="Amount per week">
          <Amount
            goal={g}
            member={member}
            resolved={r}
            autoHours={auto.hours}
            autoShort={autoShort}
            autoReason={autoReason}
            onChange={(patch) => update(i, patch)}
          />
        </Field>
        <Field label="Unit">
          <select
            className={`${input} w-full`}
            value={g.unit}
            disabled={g.amount === null || isGroup}
            title={isGroup ? "A group is always hours: its goals share them" : undefined}
            onChange={(e) => update(i, { unit: e.target.value as GoalInput["unit"] })}
          >
            {member && <option value="percent">% of group</option>}
            <option value="hours">hours</option>
            {!isGroup && <option value="sessions">sessions</option>}
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
          title={isGroup ? "Remove group (its goals stay, without group)" : "Remove goal"}
          className="self-center justify-self-end text-slate-400 hover:text-red-600"
          onClick={() => remove(i)}
        >
          <Trash2 className="size-4" />
        </button>
        <div className="col-span-2 flex flex-col gap-2 sm:col-span-5 sm:flex-row">
          <input
            className={`${input} min-w-0 flex-1`}
            placeholder={isGroup ? "Note for the planner about the whole group" : "Note for the planner, e.g. which project, how long one session is"}
            value={g.note}
            onChange={(e) => update(i, { note: e.target.value })}
          />
          {!isGroup && (
            <select
              className={`${input} sm:w-44`}
              value={member ? g.group : ""}
              title="Put this goal into a group"
              onChange={(e) => {
                const group = e.target.value;
                update(i, { group, unit: group ? "percent" : g.unit === "percent" ? "hours" : g.unit });
              }}
            >
              <option value="">no group</option>
              {groupNames
                .filter((n) => n !== g.name)
                .map((n) => (
                  <option key={n} value={n}>
                    in group {n}
                  </option>
                ))}
            </select>
          )}
        </div>
        {isGroup && !!r?.warnings.length && (
          <ul className="col-span-2 list-disc pl-5 text-xs text-amber-600 sm:col-span-5">
            {r.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      <section className="space-y-3 rounded-xl border border-slate-200 p-4 dark:border-slate-800">
        <h2 className="font-semibold">Weekly quotas</h2>
        <p className="text-xs text-slate-500">
          What you want to reach each week (Monday to Sunday); the planner spreads it over the free time. A <b>group</b>{" "}
          (e.g. Work, 30 h) is shared by its goals: fixed hours and "auto" first, the rest by percent.
        </p>
        <div className={`${ROW} hidden px-2 text-xs font-medium text-slate-500 sm:grid`}>
          <span title="Shown to the planner and in every calendar block">Goal</span>
          <span title="How much per week. 'auto': LifeOS computes it (study hours from upcoming exams and readiness)">
            Amount per week
          </span>
          <span title="Hours or sessions per week; inside a group also % of what the group has left">Unit</span>
          <span title="When the week is too full, the planner cuts low first, then medium">Priority</span>
          <span />
        </div>
        <div className="space-y-2">
          {top.map((g) => (
            <div key={indexOf(g)} className="space-y-2">
              {row(g, false)}
              {!!members.get(g.name)?.length && (
                <div className="ml-4 space-y-2 border-l-2 border-slate-200 pl-3 dark:border-slate-700">
                  {members.get(g.name)?.map((m) => row(m, true))}
                  <button
                    className="inline-flex items-center gap-1 text-xs text-teal-700 hover:underline"
                    onClick={() => setGoals([...goals, newGoal(g.name)])}
                  >
                    <Plus className="size-3" /> Add goal to {g.name || "group"}
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          <button className={btn} onClick={() => setGoals([...goals, newGoal()])}>
            <Plus className="size-4" /> Add goal
          </button>
          <button
            className={btn}
            title="A group with a total of hours that its goals share"
            onClick={() => {
              const name = window.prompt("Name of the group (e.g. Work):")?.trim();
              if (!name) return;
              if (goals.some((g) => g.name === name)) {
                window.alert("A goal with this name already exists.");
                return;
              }
              setGoals([...goals, { ...newGoal(), name, amount: 30, unit: "hours" }, newGoal(name)]);
            }}
          >
            <FolderPlus className="size-4" /> Add group
          </button>
        </div>
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

      <div className="flex flex-wrap items-center gap-3">
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

function Amount({
  goal,
  member,
  resolved,
  autoHours,
  autoShort,
  autoReason,
  onChange,
}: {
  goal: GoalInput;
  member: boolean;
  resolved: ResolvedGoal | undefined;
  autoHours: number;
  autoShort: string;
  autoReason: string;
  onChange: (patch: Partial<GoalInput>) => void;
}) {
  const isAuto = goal.amount === null;
  return (
    <div className="flex items-center gap-2">
      {isAuto ? (
        <span className="flex-1 text-sm text-slate-600 dark:text-slate-300" title={autoReason}>
          = {autoHours} h <span className="text-xs text-slate-400">({autoShort})</span>
        </span>
      ) : (
        <div className="flex flex-1 items-center gap-1">
          <input
            className={`${input} w-full min-w-0`}
            type="number"
            min={0}
            max={goal.unit === "percent" ? 100 : undefined}
            step={goal.unit === "percent" ? 5 : 0.5}
            value={goal.amount ?? 0}
            onChange={(e) => onChange({ amount: e.target.value === "" ? 0 : Number(e.target.value) })}
          />
          {goal.unit === "percent" && resolved && (
            <span className="shrink-0 text-xs text-slate-500" title="Hours this share gives this week">
              = {resolved.target} h
            </span>
          )}
        </div>
      )}
      {!resolved?.isGroup && (
        <label className="flex items-center gap-1 text-xs text-slate-500" title={autoReason}>
          <input
            type="checkbox"
            checked={isAuto}
            onChange={(e) =>
              onChange(e.target.checked ? { amount: null, unit: "hours" } : { amount: autoHours || 1, unit: member ? "hours" : goal.unit })
            }
          />
          auto
        </label>
      )}
    </div>
  );
}

/** A form field with its label on phones; on wider screens the column header names it. */
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <span className="mb-0.5 block text-xs text-slate-500 sm:hidden">{label}</span>
      {children}
    </div>
  );
}
