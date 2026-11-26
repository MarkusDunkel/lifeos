import { X } from "lucide-react";
import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { Button } from "./Button";
import { inputClass } from "./Controls";

/** Calls ``onClose`` on Escape while mounted. */
function useEscape(onClose: () => void) {
  const latest = useRef(onClose);
  latest.current = onClose;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") latest.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

/** Elements marked with this attribute may be clicked without closing an open drawer. */
export const KEEP_DRAWER = { "data-keep-drawer": "" } as const;

/**
 * A details panel on the right (a full-width sheet on phones). It does not block the page:
 * what is marked with ``KEEP_DRAWER`` (the list or diagram the selection comes from) stays
 * clickable; a click anywhere else, Escape or the close button closes it.
 */
export function Drawer({ label, onClose, children }: { label: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLElement>(null);
  const latest = useRef(onClose);
  latest.current = onClose;
  useEscape(onClose);
  useEffect(() => {
    ref.current?.focus({ preventScroll: true });
    const onDown = (e: PointerEvent) => {
      const target = e.target as Element | null;
      if (!target || ref.current?.contains(target) || target.closest("[data-keep-drawer]")) return;
      latest.current();
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, []);
  return (
    <aside
      ref={ref}
      role="dialog"
      aria-label={label}
      tabIndex={-1}
      className="fixed inset-x-0 bottom-0 z-40 flex max-h-[85dvh] flex-col rounded-t-2xl border-t border-slate-200 bg-white shadow-2xl outline-none md:inset-y-0 md:right-0 md:left-auto md:max-h-none md:w-[28rem] md:rounded-none md:border-t-0 md:border-l dark:border-slate-800 dark:bg-slate-950"
    >
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-slate-100 px-5 py-2.5 dark:border-slate-800/70">
        <span className="text-xs font-medium tracking-wide text-slate-400 uppercase">{label}</span>
        <button
          type="button"
          onClick={onClose}
          title="Close (Esc)"
          aria-label="Close"
          className="rounded-lg p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-800 dark:hover:bg-slate-800 dark:hover:text-slate-100"
        >
          <X className="size-5" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-5">{children}</div>
    </aside>
  );
}

/** A small window in the middle of the screen that has to be answered or dismissed. */
export function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEscape(onClose);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-[12vh]">
      <div className="absolute inset-0 bg-slate-950/40 backdrop-blur-[2px]" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="relative w-full max-w-md space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-2xl dark:border-slate-700 dark:bg-slate-900"
      >
        <h2 className="text-lg font-semibold">{title}</h2>
        {children}
      </div>
    </div>
  );
}

export type PromptField = { name: string; label: string; initial?: string; options?: string[]; required?: boolean };

/** Asks for one or a few values (the app's own replacement for ``window.prompt``). */
export function PromptDialog({
  title,
  fields,
  submitLabel = "OK",
  error,
  busy = false,
  onSubmit,
  onClose,
}: {
  title: string;
  fields: PromptField[];
  submitLabel?: string;
  error?: string | null;
  busy?: boolean;
  onSubmit: (values: Record<string, string>) => void;
  onClose: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map((f) => [f.name, f.initial ?? f.options?.[0] ?? ""])),
  );
  const ready = fields.every((f) => f.required === false || values[f.name]?.trim());
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (ready && !busy) onSubmit(Object.fromEntries(Object.entries(values).map(([k, v]) => [k, v.trim()])));
  };
  const firstText = fields.find((f) => !f.options)?.name;
  return (
    <Dialog title={title} onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        {fields.map((f) => (
          <label key={f.name} className="block space-y-1 text-sm">
            <span className="font-medium text-slate-600 dark:text-slate-300">{f.label}</span>
            {f.options ? (
              <select
                value={values[f.name]}
                onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
                className={`${inputClass} w-full`}
              >
                {f.options.map((o) => (
                  <option key={o}>{o}</option>
                ))}
              </select>
            ) : (
              <input
                autoFocus={f.name === firstText}
                value={values[f.name]}
                onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
                className={`${inputClass} w-full`}
              />
            )}
          </label>
        ))}
        {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={!ready} loading={busy}>
            {submitLabel}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
