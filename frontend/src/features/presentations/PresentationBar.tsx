import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, ClipboardCopy, ExternalLink, Presentation, RefreshCw } from "lucide-react";
import { useState } from "react";
import { syncPresentations } from "./api";
import { type PresentationMeta, summarize, textForClaude } from "./presentation";

const btn =
  "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-40";

/** Card above a presentation mirror: open in PowerPoint, copy for Claude, sync. */
export function PresentationBar({ meta, body }: { meta: PresentationMeta; body: string }) {
  const queryClient = useQueryClient();
  const [copied, setCopied] = useState(false);
  const sync = useMutation({
    mutationFn: syncPresentations,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["knowledge"] }),
  });

  const copy = async () => {
    await navigator.clipboard.writeText(textForClaude(meta, body));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="mb-6 rounded-xl border border-orange-200 bg-orange-50 p-4 dark:border-orange-900 dark:bg-orange-950/40">
      <div className="flex flex-wrap items-center gap-2">
        <Presentation className="size-5 text-orange-600" />
        <div className="min-w-0 flex-1 text-sm">
          <div className="font-medium">{meta.title}</div>
          <div className="text-xs text-slate-500">
            {meta.slides} slides · changed {meta.modified ? new Date(meta.modified).toLocaleString() : "?"} · OneDrive:{" "}
            {meta.onedrivePath}
          </div>
        </div>
        {!meta.deleted && (
          <a
            href={meta.url}
            target="_blank"
            rel="noopener noreferrer"
            className={`${btn} bg-orange-600 text-white hover:bg-orange-700`}
          >
            <ExternalLink className="size-4" /> Open in PowerPoint
          </a>
        )}
        <button
          onClick={copy}
          className={`${btn} bg-white hover:bg-slate-100 dark:bg-slate-900 dark:hover:bg-slate-800`}
          title="Copy the slide text, e.g. to paste into Claude"
        >
          {copied ? <Check className="size-4" /> : <ClipboardCopy className="size-4" />} {copied ? "Copied" : "Copy for Claude"}
        </button>
        <button
          onClick={() => sync.mutate()}
          disabled={sync.isPending}
          className={`${btn} hover:bg-white dark:hover:bg-slate-900`}
          title="Fetch changes from OneDrive"
        >
          <RefreshCw className={`size-4 ${sync.isPending ? "animate-spin" : ""}`} /> Sync
        </button>
      </div>
      {meta.deleted && (
        <p className="mt-2 text-sm text-amber-700 dark:text-amber-400">
          This presentation was deleted or moved out of the OneDrive folder. The text below is the last known state.
        </p>
      )}
      <p className="mt-2 text-xs text-slate-500">
        Read-only mirror of the slide text. Edit the presentation in PowerPoint; this note follows on the next sync.
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
