import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Repeat } from "lucide-react";
import { type ControlStatus, controlQuery, type FlowNode, type ProcessHealth } from "./api";
import { formatAgo } from "./format";
import { LEVEL_STYLE } from "./layout";

export type MirrorInfo = { node: FlowNode; process: ProcessHealth };

/** The mirror process that owns a knowledge path (longest matching folder wins). */
export function mirrorFor(path: string, status: ControlStatus | undefined, exact = false): MirrorInfo | null {
  if (!status) return null;
  let best: MirrorInfo | null = null;
  for (const node of status.nodes) {
    const folder = node.folder;
    if (!folder || node.kind !== "target") continue;
    const hit = exact ? path === folder : path === folder || path.startsWith(`${folder}/`);
    if (!hit || (best && (best.node.folder ?? "").length >= folder.length)) continue;
    const process = status.processes.find((p) => p.mirror && p.enabled && p.outputs.includes(node.id));
    if (process) best = { node, process };
  }
  return best;
}

/** Small marker on a folder in the knowledge tree. */
export function MirrorDot({ path }: { path: string }) {
  const { data } = useQuery(controlQuery);
  const m = mirrorFor(path, data, true);
  if (!m) return null;
  return (
    <span
      className="ml-auto inline-flex shrink-0 items-center gap-1 text-[10px] text-slate-400"
      title={`Mirror of ${m.process.label}: hand edits are overwritten. ${m.process.reason}`}
    >
      <Repeat className="size-3" />
      <span className={`size-1.5 rounded-full ${LEVEL_STYLE[m.process.level].dot}`} />
    </span>
  );
}

/** Notice above a note that lives in a mirror folder. */
export function MirrorNotice({ path }: { path: string }) {
  const { data } = useQuery(controlQuery);
  const m = mirrorFor(path, data);
  if (!m) return null;
  const style = LEVEL_STYLE[m.process.level];
  return (
    <Link
      to="/control"
      className={`mb-4 flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs ${style.box}`}
      title="Open the control room"
    >
      <Repeat className="size-3.5 shrink-0" />
      <span className={`size-2 shrink-0 rounded-full ${style.dot}`} />
      <span>
        Mirror of <b>{m.process.label}</b> – edits here are overwritten by the next sync · last success{" "}
        {formatAgo(m.process.lastSuccess)}
        {m.process.level === "warn" || m.process.level === "fail" ? ` · ${m.process.reason}` : ""}
      </span>
    </Link>
  );
}
