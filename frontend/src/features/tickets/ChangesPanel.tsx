import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FileCheck2, Send, Trash2, X } from "lucide-react";
import { useState } from "react";
import { MarkdownView } from "../knowledge/MarkdownView";
import { api, qs } from "../../lib/api";
import { applyPlan, createPlan, discardPlan, planQuery, revertTicket, type TicketDoc, type TicketsData } from "./api";
import { changeName, isPending, ticketLabel } from "./list";
import { buttonBase, buttonClass } from "../../components/ui";

const btn =
  buttonBase;

/** Strip the machine-readable block from the plan for display; it stays in the file. */
const readable = (plan: string) => plan.split("\n## Operations (machine-readable)")[0];

export function ChangesPanel({
  data,
  onOpen,
  onClose,
}: {
  data: TicketsData;
  onOpen: (path: string) => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const pendingPlan = data.plans.find((p) => p.status === "pending");
  const [shown, setShown] = useState<string | null>(pendingPlan?.id ?? null);
  const planId = shown ?? pendingPlan?.id ?? null;
  const plan = useQuery({ ...planQuery(planId ?? ""), enabled: planId !== null });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const changed = data.tickets.filter(isPending);
  const conflicted = data.tickets.filter((t) => t.conflicts.length > 0);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      await queryClient.invalidateQueries({ queryKey: ["tickets"] });
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-white dark:bg-slate-950">
      <header className="flex items-center gap-2 border-b border-slate-200 px-4 py-2 dark:border-slate-800">
        <h2 className="flex-1 text-sm font-semibold">Write back to GitHub</h2>
        <button onClick={onClose} className={`${btn} px-2 hover:bg-slate-100 dark:hover:bg-slate-800`} aria-label="Close">
          <X className="size-4" />
        </button>
      </header>
      {error && (
        <p className="border-b border-red-200 bg-red-50 px-4 py-2 text-sm whitespace-pre-line text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {error}
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        <section className="p-4">
          <h3 className="mb-2 text-xs font-semibold tracking-wide text-slate-500 uppercase">Local changes</h3>
          {changed.length === 0 ? (
            <p className="text-sm text-slate-500">Everything matches GitHub.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {changed.map((t) => (
                <li key={t.path}>
                  <button onClick={() => onOpen(t.path)} className="text-left hover:underline">
                    <span className="text-slate-400">{ticketLabel(t)}</span> {t.title}
                  </button>
                  <span className="ml-2 text-xs text-slate-400">{t.changes.map(changeName).join(", ")}</span>
                  {t.number !== null && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          if (!window.confirm(`Discard the local changes to #${t.number} and use the GitHub version?`)) return;
                          const fresh = await api<TicketDoc>(`/api/tickets/file?${qs({ path: t.path })}`);
                          await revertTicket(t.path, fresh.revision);
                        })
                      }
                      className="ml-2 text-xs text-slate-400 underline hover:text-red-600"
                    >
                      revert
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {conflicted.length > 0 && (
            <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">
              {conflicted.length} ticket(s) have conflicts; resolve them before creating a plan.
            </p>
          )}
          {!pendingPlan && (
            <button
              disabled={busy || changed.length === 0}
              onClick={() =>
                run(async () => {
                  const created = await createPlan();
                  setShown(created.id);
                })
              }
              className={`${buttonClass("primary", "md", "mt-3")}`}
            >
              <FileCheck2 className="size-4" /> Create plan
            </button>
          )}
        </section>

        {planId && plan.data && (
          <section className="border-t border-slate-200 p-4 dark:border-slate-800">
            <div className="mb-2 flex items-center gap-2">
              <h3 className="flex-1 text-xs font-semibold tracking-wide text-slate-500 uppercase">
                {plan.data.status === "pending" ? "Plan to review" : "Applied plan"}
              </h3>
              {plan.data.status === "pending" && (
                <>
                  <button
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await discardPlan(planId);
                        setShown(null);
                      })
                    }
                    className={`${btn} px-2 text-slate-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950`}
                    title="Discard plan"
                  >
                    <Trash2 className="size-4" />
                  </button>
                  <button
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        if (!window.confirm("Write these changes to GitHub now?")) return;
                        const result = await applyPlan(planId);
                        queryClient.setQueryData(planQuery(planId).queryKey, result);
                      })
                    }
                    className={`${btn} bg-emerald-600 text-white hover:bg-emerald-700`}
                  >
                    <Send className="size-4" /> {busy ? "Applying…" : "Apply"}
                  </button>
                </>
              )}
            </div>
            {plan.data.result && (
              <div className="mb-4 rounded-lg bg-slate-50 p-3 dark:bg-slate-900">
                <MarkdownView content={plan.data.result} path="" files={[]} compact />
              </div>
            )}
            <MarkdownView content={readable(plan.data.plan)} path="" files={[]} compact />
          </section>
        )}

        {data.plans.length > 0 && (
          <section className="border-t border-slate-200 p-4 dark:border-slate-800">
            <h3 className="mb-2 text-xs font-semibold tracking-wide text-slate-500 uppercase">Plans</h3>
            <ul className="space-y-1 text-sm">
              {data.plans.map((p) => (
                <li key={p.id}>
                  <button onClick={() => setShown(p.id)} className={`hover:underline ${p.id === planId ? "font-medium" : ""}`}>
                    {p.id}
                  </button>
                  <span className={`ml-2 text-xs ${p.status === "pending" ? "text-amber-600" : "text-slate-400"}`}>{p.status}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-slate-400">Stored in the vault under outbox/github/.</p>
          </section>
        )}
      </div>
    </div>
  );
}
