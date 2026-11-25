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
  Position,
  ReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Flag, Hand, Monitor, Server, Sparkles, Wrench } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ControlStatus, Level, Where } from "./api";
import { buildFlows, FOUNDATION, LEVEL_STYLE, trace, VAULT_STUB, WIDTH } from "./layout";

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
  sub: string;
  level: Level;
  kind: "source" | "process" | "target" | "actionable";
  where?: Where;
  mirror?: boolean;
  ai?: boolean;
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
      style={{ width: WIDTH }}
      className={`border px-3 py-1.5 text-left shadow-sm transition-opacity ${shape} ${look} ${
        data.selected ? "ring-2 ring-sky-500" : ""
      } ${data.dimmed ? "opacity-25" : ""}`}
    >
      {data.kind !== "source" && <Handle type="target" position={Position.Left} className="!bg-slate-400" />}
      <div className="flex items-center gap-1.5 text-xs font-medium text-slate-800 dark:text-slate-100">
        {data.kind !== "source" && <span className={`size-2 shrink-0 rounded-full ${style.dot}`} />}
        <span className="truncate">{data.label}</span>
        <span className="ml-auto flex shrink-0 items-center gap-1">
          {Kind && <Kind className="size-3.5 text-slate-500" aria-label="actionable" />}
          {data.ai && <Sparkles className="size-3.5 text-violet-500" aria-label="calls an AI model" />}
          {Icon && <Icon className="size-3.5 text-slate-400" />}
        </span>
      </div>
      <div className="truncate text-[10px] text-slate-500">
        {data.mirror ? "mirror · " : ""}
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
      className={`rounded-xl border-2 border-dashed border-sky-300 bg-sky-50/40 transition-opacity dark:border-sky-800 dark:bg-sky-950/20 ${
        data.dimmed ? "opacity-40" : ""
      }`}
    >
      <div className="px-3 pt-1.5 text-[11px] font-medium text-sky-700 dark:text-sky-300">{data.label}</div>
      <Handle type="source" position={Position.Right} style={{ top: 14 }} className="!bg-sky-500" />
    </div>
  );
}

type BandData = { label: string; width: number };

/** Title and rule above the technical foundation. */
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
            style={{ transform: `translate(-50%, -50%) translate(${(right + left) / 2}px, ${floor}px)` }}
            className="absolute rounded bg-white/90 px-1 text-[10px] text-violet-600 dark:bg-slate-900/90 dark:text-violet-300"
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const edgeTypes = { loop: LoopEdge };

export function ControlFlow({
  status,
  selected,
  onSelect,
}: {
  status: ControlStatus;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const [hovered, setHovered] = useState<string | null>(null);
  // highlight a moment after the pointer arrives: re-rendering right away would swallow a
  // click that follows immediately (fast clicks, touch)
  const timer = useRef<number | undefined>(undefined);
  const hover = (id: string | null) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setHovered(id), id ? 120 : 60);
  };
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const graph = useMemo(() => buildFlows(status.nodes, status.processes), [status]);
  const focus = hovered ?? selected;
  const lit = useMemo(() => (focus ? trace(graph.links, focus) : null), [graph, focus]);

  const { nodes, edges } = useMemo(() => {
    const processes = new Map(status.processes.map((p) => [`p:${p.id}`, p]));
    const flowNodes = new Map(status.nodes.map((n) => [n.id, n]));
    const nodeOf = (id: string) =>
      id === VAULT_STUB
        ? { label: "Vault", detail: "ready/knowledge (all folders above)", level: "ok" as Level, kind: "source" as const }
        : flowNodes.get(id.startsWith(FOUNDATION) ? id.slice(FOUNDATION.length) : id)!;
    const dim = (id: string) => lit !== null && !lit.has(id);

    const boxes: Node<BoxData>[] = graph.groups.map((g) => ({
      id: g.id,
      type: "box",
      position: { x: g.x, y: g.y },
      data: { label: g.label, width: g.width, height: g.height, dimmed: dim(g.id) },
      selectable: false,
      zIndex: -1,
    }));
    const items: Node<ItemData>[] = graph.placed.map(({ id, x, y }) => {
      const p = processes.get(id);
      const common = { selected: selected === id, dimmed: dim(id) };
      if (p) {
        return {
          id, type: "item", position: { x, y },
          data: { label: p.label, sub: p.trigger, level: p.level, kind: "process", where: p.where, mirror: p.mirror, ai: p.ai.length > 0, ...common },
        };
      }
      const n = nodeOf(id);
      const kind = id.startsWith(FOUNDATION) && n.kind !== "target" ? "source" : n.kind;
      return {
        id, type: "item", position: { x, y },
        data: { label: n.label, sub: n.detail, level: n.level, kind, ...common },
      };
    });
    const width = Math.max(0, ...graph.placed.map((p) => p.x)) + WIDTH;
    const band: Node<BandData>[] = graph.band
      ? [{ id: "band", type: "band", position: { x: 0, y: graph.band.y }, data: { label: graph.band.label, width },
           selectable: false, draggable: false }]
      : [];

    const edges: Edge[] = graph.edges.map((e) => {
      const level = processes.get(`p:${e.process}`)?.level ?? "off";
      const on = lit === null || (lit.has(e.source) && lit.has(e.target));
      const strong = lit !== null && on;
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        type: "default", // bezier: every edge has its own curve, no shared channels
        animated: level === "fail" && on,
        style: {
          stroke: LEVEL_STYLE[level].edge,
          strokeWidth: strong ? 2.5 : level === "fail" ? 2 : 1.25,
          opacity: on ? 1 : 0.12,
        },
        zIndex: strong ? 1 : 0,
      };
    });
    const mainBottom = Math.max(
      0,
      ...graph.placed.filter((p) => !graph.band || p.y < graph.band.y).map((p) => p.y + 52),
    );
    const loops: Edge<LoopData>[] = graph.feedback.map((e, i) => {
      const on = lit === null || (lit.has(e.source) && lit.has(e.target));
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        type: "loop",
        data: { floor: mainBottom + 24, lane: i, label: i === 0 ? "your reactions" : undefined },
        style: { stroke: "#a78bfa", strokeDasharray: "6 4", strokeWidth: lit && on ? 2 : 1.25, opacity: lit ? (on ? 1 : 0.08) : 0.55 },
      };
    });
    return { nodes: [...band, ...boxes, ...items], edges: [...edges, ...loops] };
  }, [status, graph, lit, selected]);

  return (
    <div className="h-[760px] rounded-xl border border-slate-200 dark:border-slate-800">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodeClick={(_, node) => node.type === "item" && node.id !== VAULT_STUB && onSelect(node.id)}
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
      </ReactFlow>
    </div>
  );
}
