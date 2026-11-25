import { Mic } from "lucide-react";
import { CONTROL_HINTS, CONTROL_WORDS, controlsOpen, rememberControlsOpen } from "./controls";

/** Collapsible cheat sheet of the voice coach's control words (state kept per browser). */
export function VoiceControls() {
  return (
    <details
      open={controlsOpen()}
      onToggle={(e) => rememberControlsOpen((e.currentTarget as HTMLDetailsElement).open)}
      className="rounded-xl border border-violet-200 bg-violet-50/50 p-3 text-sm dark:border-violet-900 dark:bg-violet-950/30"
    >
      <summary className="flex cursor-pointer items-center gap-2 font-medium select-none">
        <Mic className="size-4 text-violet-600" /> Voice coach controls
      </summary>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5">
        {CONTROL_WORDS.map((c) => (
          <div key={c.word} className="contents">
            <dt className="font-semibold whitespace-nowrap text-violet-700 dark:text-violet-300">„{c.word}“</dt>
            <dd className="text-slate-600 dark:text-slate-300">{c.meaning}</dd>
          </div>
        ))}
      </dl>
      <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-slate-500">
        {CONTROL_HINTS.map((h) => (
          <li key={h}>{h}</li>
        ))}
      </ul>
    </details>
  );
}
