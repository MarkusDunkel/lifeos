import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import {
  type CalendarInput,
  calendarStatusQuery,
  connectGoogle,
  disconnectGoogle,
  googleCalendarsQuery,
  saveInputs,
  syncCalendars,
} from "./api";

export const btn =
  "inline-flex items-center gap-2 rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100 disabled:opacity-40 dark:border-slate-700 dark:hover:bg-slate-800";

/** Connect Google, pick the input calendars and mirror them into the LifeOS calendar. */
export function CalendarsTab({ notice }: { notice?: string }) {
  const queryClient = useQueryClient();
  const status = useQuery(calendarStatusQuery);
  const s = status.data;
  const calendars = useQuery({ ...googleCalendarsQuery, enabled: !!s?.connected });
  const [chosen, setChosen] = useState<CalendarInput[] | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["calendar"] });

  useEffect(() => {
    if (calendars.data && chosen === null) {
      setChosen(calendars.data.filter((c) => c.input).map((c) => ({ id: c.id, label: c.label ?? c.name })));
    }
  }, [calendars.data, chosen]);

  const connect = useMutation({
    mutationFn: connectGoogle,
    onSuccess: ({ url }) => {
      window.location.href = url;
    },
  });
  const sync = useMutation({ mutationFn: syncCalendars, onSettled: refresh });
  const save = useMutation({
    mutationFn: (inputs: CalendarInput[]) => saveInputs(inputs).then(() => syncCalendars()),
    onSettled: refresh,
  });

  const toggle = (id: string, name: string) =>
    setChosen((cur) => {
      const list = cur ?? [];
      return list.some((c) => c.id === id) ? list.filter((c) => c.id !== id) : [...list, { id, label: name }];
    });

  const last = s?.last;
  return (
    <section className="space-y-4 rounded-xl border border-slate-200 p-4 dark:border-slate-800">
      <h2 className="flex items-center gap-2 font-semibold">
        <CalendarDays className="size-5 text-sky-600" /> Calendars (Google)
      </h2>
      <p className="text-sm text-slate-500">
        LifeOS reads your input calendars and mirrors every appointment into its own calendar "LifeOS", together with
        the blocks the planner adds. Show only "LifeOS" on your phone. LifeOS cannot write to any other calendar.
      </p>
      {notice && (
        <p className={`text-sm ${notice.startsWith("error") ? "text-red-600" : "text-emerald-600"}`}>Google: {notice}</p>
      )}
      {!s ? (
        <p className="text-sm text-slate-400">{status.error ? status.error.message : "Loading…"}</p>
      ) : !s.configured ? (
        <p className="text-sm text-amber-600">Not set up. Add {s.missing.join(", ")} to the deployment's .env.</p>
      ) : !s.connected ? (
        <button className={btn} onClick={() => connect.mutate()} disabled={connect.isPending}>
          Connect Google account
        </button>
      ) : (
        <>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-slate-500">Account</dt>
            <dd>{s.account || "connected"}</dd>
            <dt className="text-slate-500">Last sync</dt>
            <dd>
              {last
                ? `${new Date(last.at).toLocaleString()} · ${last.events} events from ${last.inputs} calendar(s), mirrors +${last.created} ~${last.updated} −${last.deleted}`
                : "never"}
            </dd>
          </dl>
          {last?.errors.length ? (
            <ul className="list-disc pl-5 text-sm text-red-600">
              {last.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          ) : null}

          <div>
            <h3 className="mb-2 text-sm font-medium">Input calendars</h3>
            {calendars.isLoading && <p className="text-sm text-slate-400">Loading…</p>}
            {calendars.error && <p className="text-sm text-red-600">{calendars.error.message}</p>}
            <ul className="space-y-1">
              {calendars.data
                ?.filter((c) => !c.target)
                .map((c) => {
                  const pick = chosen?.find((x) => x.id === c.id);
                  return (
                    <li key={c.id} className="flex items-center gap-2 text-sm">
                      <input type="checkbox" checked={!!pick} onChange={() => toggle(c.id, c.name)} />
                      <span className="size-3 shrink-0 rounded-full" style={{ background: c.color ?? "#999" }} />
                      <span className="min-w-0 flex-1 truncate">{c.name}</span>
                      {pick && (
                        <input
                          className="w-32 rounded border border-slate-300 bg-transparent px-2 py-0.5 text-xs dark:border-slate-700"
                          value={pick.label}
                          title="Label shown on mirrored events"
                          onChange={(e) =>
                            setChosen((cur) =>
                              (cur ?? []).map((x) => (x.id === c.id ? { ...x, label: e.target.value } : x)),
                            )
                          }
                        />
                      )}
                    </li>
                  );
                })}
            </ul>
          </div>

          <div className="flex flex-wrap gap-2">
            <button className={btn} onClick={() => chosen && save.mutate(chosen)} disabled={!chosen || save.isPending}>
              Save selection
            </button>
            <button className={btn} onClick={() => sync.mutate()} disabled={sync.isPending}>
              <RefreshCw className={`size-4 ${sync.isPending ? "animate-spin" : ""}`} /> Sync now
            </button>
            <button
              className={btn}
              onClick={async () => {
                if (!window.confirm("Disconnect Google? The LifeOS calendar stays in your account.")) return;
                await disconnectGoogle();
                await refresh();
              }}
            >
              Disconnect
            </button>
          </div>
          {(save.error || sync.error) && <p className="text-sm text-red-600">{(save.error ?? sync.error)?.message}</p>}
        </>
      )}
    </section>
  );
}
