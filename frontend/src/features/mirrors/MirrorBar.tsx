import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, ClipboardCopy, ExternalLink, GraduationCap, Presentation, RefreshCw } from "lucide-react";
import { useState } from "react";
import { api } from "../../lib/api";
import { type MirrorMeta, type MirrorReport, summarize, textForClaude } from "./mirror";
import { buttonBase } from "../../components/ui";

const btn =
  buttonBase;

const STYLE = {
  presentation: { box: "border-orange-200 bg-orange-50 dark:border-orange-900 dark:bg-orange-950/40", accent: "bg-orange-600 hover:bg-orange-700", icon: "text-orange-600", Icon: Presentation },
  moodle: { box: "border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/40", accent: "bg-amber-600 hover:bg-amber-700", icon: "text-amber-600", Icon: GraduationCap },
};

/** Card above a mirror note: open the source, copy the text for Claude, sync. */
export function MirrorBar({ meta, body }: { meta: MirrorMeta; body: string }) {
  const queryClient = useQueryClient();
  const [copied, setCopied] = useState(false);
  const style = STYLE[meta.kind === "presentation" ? "presentation" : "moodle"];
  const sync = useMutation({
    mutationFn: () => api<MirrorReport>(meta.syncPath, { method: "POST" }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["knowledge"] }),
  });

  const copy = async () => {
    await navigator.clipboard.writeText(textForClaude(meta, body));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className={`mb-6 rounded-xl border p-4 ${style.box}`}>
      <div className="flex flex-wrap items-center gap-2">
        <style.Icon className={`size-5 ${style.icon}`} />
        <div className="min-w-0 flex-1 text-sm">
          <div className="font-medium">{meta.title}</div>
          <div className="text-xs text-slate-500">{meta.details.join(" · ")}</div>
        </div>
        {!meta.deleted && (
          <a href={meta.url} target="_blank" rel="noopener noreferrer" className={`${btn} text-white ${style.accent}`}>
            <ExternalLink className="size-4" /> {meta.openLabel}
          </a>
        )}
        <button
          onClick={copy}
          className={`${btn} bg-white hover:bg-slate-100 dark:bg-slate-900 dark:hover:bg-slate-800`}
          title="Copy the text, e.g. to paste into Claude"
        >
          {copied ? <Check className="size-4" /> : <ClipboardCopy className="size-4" />} {copied ? "Copied" : "Copy for Claude"}
        </button>
        <button
          onClick={() => sync.mutate()}
          disabled={sync.isPending}
          className={`${btn} hover:bg-white dark:hover:bg-slate-900`}
          title="Fetch changes from the source"
        >
          <RefreshCw className={`size-4 ${sync.isPending ? "animate-spin" : ""}`} /> Sync
        </button>
      </div>
      {meta.deleted && (
        <p className="mt-2 text-sm text-amber-700 dark:text-amber-400">
          This was removed at the source. The text below is the last known state.
        </p>
      )}
      <p className="mt-2 text-xs text-slate-500">
        Read-only mirror. Changes belong in {meta.kind === "presentation" ? "PowerPoint" : "Moodle"}; this note follows on
        the next sync.
      </p>
      {sync.data && <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">Synced: {summarize(sync.data)}</p>}
      {sync.data?.errors.map((e) => (
        <p key={e} className="mt-1 text-xs text-red-600">
          {e}
        </p>
      ))}
      {sync.error && <p className="mt-1 text-xs text-red-600">{sync.error.message}</p>}
    </div>
  );
}
