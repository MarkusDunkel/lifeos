import type { ControlStatus, ProcessHealth } from "./api";
import { LEVEL_RANK } from "./layout";

export const isProblem = (p: ProcessHealth) => p.enabled && (p.level === "warn" || p.level === "fail");

/** Problems first, then by name. */
export function sortProcesses(list: ProcessHealth[]): ProcessHealth[] {
  return [...list].sort((a, b) => LEVEL_RANK[b.level] - LEVEL_RANK[a.level] || a.label.localeCompare(b.label));
}

/** The rows of the process table: only problems if asked, and what the search words match. */
export function filterProcesses(
  list: ProcessHealth[],
  { query = "", problemsOnly = false }: { query?: string; problemsOnly?: boolean },
): ProcessHealth[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return list.filter((p) => {
    if (problemsOnly && !isProblem(p)) return false;
    if (!words.length) return true;
    const text = [p.label, p.ref, p.id, p.trigger, p.reason, ...p.ai.map((a) => a.title)].join(" ").toLowerCase();
    return words.every((w) => text.includes(w));
  });
}

/** Red and yellow lights of the whole page: the processes and the system checks. */
export function tally(s: Pick<ControlStatus, "counts" | "system">): { fail: number; warn: number } {
  return {
    fail: s.counts.fail + s.system.filter((c) => c.ok === false).length,
    warn: s.counts.warn + s.system.filter((c) => c.ok === null).length,
  };
}

/** "Run now" that nobody picked up for this long is no longer believed to be queued. */
export const STUCK_AFTER = 5 * 60_000;

export type RunState =
  | { kind: "idle" }
  | { kind: "running"; since: string }
  | { kind: "queued"; since: string; behind: string | null }
  | { kind: "stuck"; since: string };

/**
 * Where a worker job stands: running now, asked for and waiting (behind the job the worker
 * runs, if any), or asked for a while ago without the worker picking it up.
 */
export function runState(
  p: Pick<ProcessHealth, "id" | "runningSince">,
  s: Pick<ControlStatus, "requests" | "worker">,
  now = Date.now(),
): RunState {
  if (p.runningSince) return { kind: "running", since: p.runningSince };
  const asked = s.requests[p.id];
  if (!asked) return { kind: "idle" };
  const busy = s.worker?.running ?? null;
  if (busy) return { kind: "queued", since: asked, behind: busy.label };
  return now - Date.parse(asked) > STUCK_AFTER
    ? { kind: "stuck", since: asked }
    : { kind: "queued", since: asked, behind: null };
}
