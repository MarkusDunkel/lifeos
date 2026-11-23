import { useQueryClient } from "@tanstack/react-query";
import { MessageSquare } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError } from "../../lib/api";
import { type DayThread, saveNote } from "./api";

/** The day's "💬 Planner" entry: the owner's note (saved into the calendar, planned on
 * "Plan again") and the planner's answers. Read-only for past days. */
export function PlannerNote({ day, thread, readOnly = false }: { day: string; thread: DayThread | null; readOnly?: boolean }) {
  const queryClient = useQueryClient();
  const saved = thread?.owner ?? "";
  const [draft, setDraft] = useState(saved);
  const [base, setBase] = useState(saved);
  const [open, setOpen] = useState(!!saved);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  // a newer note from the calendar replaces the shown one, unless the owner is typing
  useEffect(() => {
    if (draft === base) {
      setDraft(saved);
      setBase(saved);
      if (saved) setOpen(true);
    }
  }, [saved]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!thread) return null;
  const answers = thread.answers;
  if (readOnly && !saved && !answers.length) return null;
  if (!open && !readOnly && !answers.length) {
    return (
      <button onClick={() => setOpen(true)} className="flex items-center gap-1 text-xs text-teal-700 hover:underline dark:text-teal-400">
        <MessageSquare className="size-3.5" /> Write a note for the planner about this day
      </button>
    );
  }

  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const r = await saveNote(day, draft, base);
      setBase(r.owner);
      setDraft(r.owner);
      setMessage({ ok: true, text: "Saved – press “Plan again” to plan with it." });
      await queryClient.invalidateQueries({ queryKey: ["planning"] });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const current = (e.body as { owner?: string }).owner ?? "";
        setBase(current);
        setDraft(current);
        setMessage({ ok: false, text: "Changed in the calendar meanwhile – your text was dropped, please check again." });
      } else {
        setMessage({ ok: false, text: e instanceof Error ? e.message : String(e) });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2 rounded-lg border border-sky-200 bg-sky-50/60 p-2 text-sm dark:border-sky-900 dark:bg-sky-950/40">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{thread.title}</span>
        {thread.pending && <span className="text-xs text-amber-600">not planned yet</span>}
      </div>
      {readOnly ? (
        saved && <p className="whitespace-pre-line">{saved}</p>
      ) : (
        <>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={2}
            placeholder="What should the planner know about this day? e.g. “travelling all day, only sport”"
            className="w-full rounded border border-slate-300 bg-white px-2 py-1 dark:border-slate-600 dark:bg-slate-900"
          />
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={save}
              disabled={busy || draft.trim() === base.trim()}
              className="rounded bg-teal-600 px-3 py-1 text-white hover:bg-teal-700 disabled:opacity-40"
            >
              Save note
            </button>
            {message && <span className={`text-xs ${message.ok ? "text-emerald-600" : "text-red-600"}`}>{message.text}</span>}
          </div>
        </>
      )}
      {!!answers.length && (
        <ul className="space-y-0.5 border-t border-sky-200 pt-1 text-xs text-slate-600 dark:border-sky-900 dark:text-slate-300">
          {answers.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
