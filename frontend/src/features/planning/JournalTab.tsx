import { useQuery } from "@tanstack/react-query";
import { journalQuery } from "./api";

/** The journal so far, newest first, with the scales side by side. */
export function JournalTab() {
  const data = useQuery(journalQuery);
  if (!data.data) return <p className="text-sm text-slate-400">{data.error ? data.error.message : "Loading…"}</p>;
  const entries = [...data.data].reverse();
  if (!entries.length) return <p className="text-sm text-slate-400">No journal entries yet – write one on the Evening tab.</p>;
  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-left text-xs text-slate-500 dark:bg-slate-900">
          <tr>
            <th className="px-3 py-2">Day</th>
            <th className="px-2 py-2 text-right">Mood</th>
            <th className="px-2 py-2 text-right">Energy</th>
            <th className="px-2 py-2 text-right">Sleep</th>
            <th className="px-2 py-2 text-right">Pain</th>
            <th className="px-3 py-2">Notes</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.date} className="border-t border-slate-100 align-top dark:border-slate-800">
              <td className="whitespace-nowrap px-3 py-2 tabular-nums">{e.date}</td>
              <td className="px-2 py-2 text-right tabular-nums">{e.mood ?? "–"}</td>
              <td className="px-2 py-2 text-right tabular-nums">{e.energy ?? "–"}</td>
              <td className="px-2 py-2 text-right tabular-nums">{e.sleep ?? "–"}</td>
              <td className="px-2 py-2 text-right tabular-nums">
                {e.pain ?? "–"}
                {e.pain_where && <span className="block text-xs text-slate-500">{e.pain_where}</span>}
              </td>
              <td className="px-3 py-2">
                {e.good && <p>+ {e.good}</p>}
                {e.bad && <p className="text-slate-500">− {e.bad}</p>}
                {e.notes && <p className="text-xs text-slate-500">{e.notes}</p>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
