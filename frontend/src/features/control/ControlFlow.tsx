import {
  Background,
  BaseEdge,
  Controls,
  type Edge,
  EdgeLabelRenderer,
  type EdgeProps,
  Handle,
  type Node,
  type NodeProps,
  Panel,
  Position,
  ReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Flag, Hand, Maximize2, Minimize2, Monitor, ScanSearch, Server, Sparkles, TriangleAlert, Wrench } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ControlStatus, Level, Where } from "./api";
import { buildFlows, drawnIds, FOUNDATION, LEVEL_STYLE, originalId, trace, VAULT_STUB, widthOf } from "./layout";

const WHERE_ICON: Record<Where, typeof Server> = { server: Server, pc: Monitor, manual: Hand, host: Wrench };
export { WHERE_ICON };
export const WHERE_LABEL: Record<Where, string> = {
  server: "Server, automatic",
  pc: "PC, automatic (PC must be on)",
  manual: "Manual",
  host: "Server host",
};

type ItemData = {
  label: string;
  width: number;
  ref?: string;
  sub: string;
  level: Level;
  kind: "source" | "process" | "dataset" | "actionable";
  where?: Where;
  mirror?: boolean;
  ai?: boolean;
  running?: boolean;
  selected: boolean;
  dimmed: boolean;
};

type BoxData = { label: string; width: number; height: number; dimmed: boolean };

function Item({ data }: NodeProps<Node<ItemData>>) {
  const style = LEVEL_STYLE[data.level];
  const Icon = data.where ? WHERE_ICON[data.where] : null;
  const shape =
    data.kind === "process" ? "rounded-full" : data.kind === "actionable" ? "rounded-r-lg border-l-[6px]" : "rounded-lg";
  const look = data.kind === "source" ? "border-slate-300 bg-white dark:border-slate-700 dark:bg-slate-900" : style.box;
  const Kind = data.kind === "actionable" ? Flag : null;
  return (
    <div
      style={{ width: data.width }}
      className={`border px-3 py-1.5 text-left shadow-sm transition-opacity ${shape} ${look} ${
        data.selected ? "ring-2 ring-accent-500" : ""
      } ${data.dimmed ? "opacity-25" : ""}`}
    >
      {data.kind !== "source" && <Handle type="target" position={Position.Left} className="!bg-slate-400" />}
      <div
        title={data.label}
        className="flex items-center gap-1.5 text-xs leading-tight font-medium text-slate-800 dark:text-slate-100"
      >
        {data.kind !== "source" &&
          (data.running ? (
            <span className="relative flex size-2 shrink-0" aria-label="running now">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-accent-400 opacity-75" />
              <span className="relative inline-flex size-2 rounded-full bg-accent-500" />
            </span>
          ) : (
            <span className={`size-2 shrink-0 rounded-full ${style.dot}`} />
          ))}
        <span className="line-clamp-2">{data.label}</span>
        <span className="ml-auto flex shrink-0 items-center gap-1">
          {Kind && <Kind className="size-3.5 text-slate-500" aria-label="actionable" />}
          {data.ai && <Sparkles className="size-3.5 text-violet-500" aria-label="calls an AI model" />}
          {Icon && <Icon className="size-3.5 text-slate-400" />}
        </span>
      </div>
      <div className="truncate text-[10px] text-slate-500">
        {data.ref && <span className="font-mono text-slate-600 dark:text-slate-300">{data.ref} · </span>}
        {data.mirror ? "mirror · " : ""}
        {data.running ? "running now · " : ""}
        {data.sub}
      </div>
      <Handle type="source" position={Position.Right} className="!bg-slate-400" />
    </div>
  );
}

/** The box around the vault folders; its right edge feeds the vault sync. */
function Box({ data }: NodeProps<Node<BoxData>>) {
  return (
    <div
      style={{ width: data.width, height: data.height }}
      className={`rounded-xl border-2 border-dashed border-accent-300 bg-accent-50/40 transition-opacity dark:border-accent-800 dark:bg-accent-950/20 ${
        data.dimmed ? "opacity-40" : ""
      }`}
    >
      <div className="px-3 pt-1.5 text-[11px] font-medium text-accent-700 dark:text-accent-300">{data.label}</div>
      <Handle type="source" position={Position.Right} style={{ top: 14 }} className="!bg-accent-500" />
    </div>
  );
}

type BandData = { label: string; width: number };

/** Title and rule above a row below the main flow (second order, technical foundation). */
function Band({ data }: NodeProps<Node<BandData>>) {
  return (
    <div style={{ width: data.width }} className="border-t-2 border-dotted border-slate-300 pt-1.5 dark:border-slate-700">
      <span className="text-[11px] font-medium tracking-wide text-slate-500 uppercase">{data.label}</span>
    </div>
  );
}

const nodeTypes = { item: Item, box: Box, band: Band };

type LoopData = { floor: number; lane: number; label?: string };

/**
 * The owner's reactions on an actionable flowing back into a process: out to the right of
 * the actionable, back along the floor below the whole flow, up in the free gap before the
 * process and into it from the left. Lanes keep several loops apart; no box is crossed.
 */
function LoopEdge({ id, sourceX, sourceY, targetX, targetY, style, data }: EdgeProps<Edge<LoopData>>) {
  const lane = (data?.lane ?? 0) * 7;
  const right = sourceX + 14 + lane;
  const floor = (data?.floor ?? sourceY + 80) + lane;
  const left = targetX - 16 - lane;
  const path = `M ${sourceX} ${sourceY} H ${right} V ${floor} H ${left} V ${targetY} H ${targetX}`;
  return (
    <>
      <BaseEdge id={id} path={path} style={style} />
      {data?.label && (
        <EdgeLabelRenderer>
          <div
            style={{ transform: `translate(-100%, 0) translate(${left - 4}px, ${targetY + 10}px)` }}
            className="absolute rounded bg-white/90 px-1 text-[10px] text-violet-600 dark:bg-slate-900/90 dark:text-violet-300"
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

type RoutedData = { via: { x: number; y: number; width: number }[] };

/**
 * An edge that skips columns: it crosses each column in its own free lane (a straight
 * stretch where no box stands) and curves only in the gaps between columns.
 */
function RoutedEdge({ id, sourceX, sourceY, targetX, targetY, style, data }: EdgeProps<Edge<RoutedData>>) {
  const points = [{ x: sourceX, y: sourceY }];
  for (const v of data?.via ?? []) points.push({ x: v.x, y: v.y }, { x: v.x + v.width, y: v.y });
  points.push({ x: targetX, y: targetY });
  let path = `M ${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1];
    const b = points[i];
    if (i % 2 === 0) {
      path += ` L ${b.x} ${b.y}`; // through a lane
    } else {
      const dx = (b.x - a.x) / 2; // across a gap
      path += ` C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`;
    }
  }
  return <BaseEdge id={id} path={path} style={style} />;
}

const edgeTypes = { loop: LoopEdge, routed: RoutedEdge };

/** The part of the React Flow instance the toolbar uses. */
type Viewer = {
  fitView: (options?: { nodes?: { id: string }[]; padding?: number; maxZoom?: number; duration?: number }) => unknown;
};

const toolBtn =
  "inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white/95 px-2.5 py-1 text-xs font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-45 dark:border-slate-700 dark:bg-slate-900/95 dark:text-slate-200 dark:hover:bg-slate-800";

export function ControlFlow({
  status,
  selected,
  onSelect,
  fullscreen,
  onFullscreen,
}: {
  status: ControlStatus;
  selected: string | null;
  onSelect: (id: string | null) => void;
  fullscreen: boolean;
  onFullscreen: (on: boolean) => void;
}) {
  const [hovered, setHovered] = useState<string | null>(null);
  const flow = useRef<Viewer | null>(null);
  // highlight a moment after the pointer arrives: re-rendering right away would swallow a
  // click that follows immediately (fast clicks, touch)
  const timer = useRef<number | undefined>(undefined);
  const hover = (id: string | null) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setHovered(id), id ? 120 : 60);
  };
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const graph = useMemo(() => buildFlows(status.nodes, status.processes), [status]);
  // a selection from a link or the table names the catalog id: light every box drawn for it
  const chosen = useMemo(() => drawnIds(graph.placed, selected), [graph, selected]);
  const lit = useMemo(() => {
    const foci = hovered ? [hovered] : chosen;
    if (!foci.length) return null;
    return new Set(foci.flatMap((id) => [...trace(graph.links, id)]));
  }, [graph, hovered, chosen]);
  // the view fits the new size after the switch to or from the full screen
  useEffect(() => {
    const t = window.setTimeout(() => flow.current?.fitView({ duration: 200 }), 60);
    return () => window.clearTimeout(t);
  }, [fullscreen]);
  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onFullscreen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen, onFullscreen]);

  const { nodes, edges } = useMemo(() => {
    const processes = new Map(status.processes.map((p) => [`p:${p.id}`, p]));
    const flowNodes = new Map(status.nodes.map((n) => [n.id, n]));
    const nodeOf = (id: string) =>
      id === VAULT_STUB
        ? { label: "Vault", detail: "ready/knowledge (all folders above)", level: "ok" as Level, kind: "source" as const }
        : flowNodes.get(originalId(id))!;
    const dim = (id: string) => lit !== null && !lit.has(id);

    // a group can span columns (e.g. a vault folder further right): one box per column, the
    // first keeps the group id that edges from the whole group start at
    const boxIds = new Set<string>();
    const boxes: Node<BoxData>[] = graph.groups.map((g, i) => ({
      id: boxIds.has(g.id) ? `${g.id}@${i}` : (boxIds.add(g.id), g.id),
      type: "box",
      position: { x: g.x, y: g.y },
      data: { label: g.label, width: g.width, height: g.height, dimmed: dim(g.id) },
      selectable: false,
      zIndex: -1,
    }));
    const items: Node<ItemData>[] = graph.placed.map(({ id, x, y }) => {
      const p = processes.get(id);
      const common = { selected: chosen.includes(id), dimmed: dim(id) };
      if (p) {
        return {
          id, type: "item", position: { x, y },
          data: { label: p.label, width: widthOf(id), ref: p.ref, sub: p.trigger, level: p.level, kind: "process", where: p.where, mirror: p.mirror, ai: p.ai.length > 0, running: !!p.runningSince, ...common },
        };
      }
      const n = nodeOf(id);
      const kind = id.startsWith(FOUNDATION) && n.kind !== "dataset" ? "source" : n.kind;
      return {
        id, type: "item", position: { x, y },
        data: { label: n.label, width: widthOf(id), ref: "ref" in n ? n.ref : undefined, sub: n.detail, level: n.level, kind, ...common },
      };
    });
    const width = Math.max(0, ...graph.placed.map((p) => p.x + p.width));
    const band: Node<BandData>[] = graph.bands.map((b, i) => ({
      id: `band:${i}`, type: "band", position: { x: 0, y: b.y }, data: { label: b.label, width },
      selectable: false, draggable: false,
    }));

    const edges: Edge[] = graph.edges.map((e) => {
      const level = processes.get(`p:${e.process}`)?.level ?? "off";
      const on = lit === null || (lit.has(e.source) && lit.has(e.target));
      const strong = lit !== null && on;
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        // bezier between neighbouring columns; through free lanes when it skips columns
        type: e.via ? "routed" : "default",
        data: e.via ? { via: e.via } : undefined,
        animated: level === "fail" && on,
        style: {
          stroke: LEVEL_STYLE[level].edge,
          strokeWidth: strong ? 2.5 : level === "fail" ? 2 : 1.25,
          opacity: on ? 1 : 0.12,
        },
        zIndex: strong ? 1 : 0,
      };
    });
    const firstBand = graph.bands[0]?.y ?? Infinity;
    const mainBottom = Math.max(
      0,
      ...graph.placed.filter((p) => p.y < firstBand).map((p) => p.y + 52),
      ...graph.edges.flatMap((e) => (e.via ?? []).filter((v) => v.y < firstBand).map((v) => v.y + 12)),
    );
    const loops: Edge<LoopData>[] = graph.feedback.map((e, i) => {
      const on = lit === null || (lit.has(e.source) && lit.has(e.target));
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        type: "loop",
        data: { floor: e.floor ?? mainBottom + 24, lane: i, label: processes.get(e.target)?.reaction || "your reactions" },
        style: { stroke: "#a78bfa", strokeDasharray: "6 4", strokeWidth: lit && on ? 2 : 1.25, opacity: lit ? (on ? 1 : 0.08) : 0.55 },
      };
    });
    return { nodes: [...band, ...boxes, ...items], edges: [...edges, ...loops] };
  }, [status, graph, lit, chosen]);

  const problems = useMemo(
    () => nodes.filter((n) => n.type === "item" && ["warn", "fail"].includes((n.data as ItemData).level)).map((n) => ({ id: n.id })),
    [nodes],
  );

  return (
    <div
      className={
        fullscreen
          ? "fixed inset-0 z-30 bg-white dark:bg-slate-950"
          : "h-[min(760px,calc(100dvh-13rem))] min-h-[26rem] overflow-hidden rounded-xl border border-slate-200 dark:border-slate-800"
      }
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onInit={(instance) => {
          flow.current = instance;
        }}
        onNodeClick={(_, node) => node.type === "item" && node.id !== VAULT_STUB && onSelect(node.id)}
        onPaneClick={() => selected && onSelect(null)}
        onNodeMouseEnter={(_, node) => node.type === "item" && hover(node.id)}
        onNodeMouseLeave={() => hover(null)}
        nodesDraggable={false}
        nodesConnectable={false}
        fitView
        minZoom={0.3}
        colorMode="system"
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={24} size={1} />
        <Controls showInteractive={false} />
        <Panel position="top-right" className="flex gap-1.5">
          <button
            type="button"
            className={toolBtn}
            disabled={problems.length === 0}
            title={problems.length ? "Zoom to the yellow and red boxes" : "Nothing is yellow or red"}
            onClick={() => flow.current?.fitView({ nodes: problems, padding: 0.5, maxZoom: 1, duration: 400 })}
          >
            <TriangleAlert className="size-3.5" /> Problems
          </button>
          <button type="button" className={toolBtn} title="Show the whole map" onClick={() => flow.current?.fitView({ duration: 400 })}>
            <ScanSearch className="size-3.5" /> Fit
          </button>
          <button
            type="button"
            className={toolBtn}
            title={fullscreen ? "Leave the full screen (Esc)" : "Use the full screen"}
            onClick={() => onFullscreen(!fullscreen)}
          >
            {fullscreen ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
            {fullscreen ? "Close" : "Full screen"}
          </button>
        </Panel>
      </ReactFlow>
    </div>
  );
}
