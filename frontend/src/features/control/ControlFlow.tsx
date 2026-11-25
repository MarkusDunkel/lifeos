import { Background, Controls, type Edge, Handle, type Node, type NodeProps, Position, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Hand, Monitor, Server, Sparkles, Wrench } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ControlStatus, Level, Where } from "./api";
import { buildGraph, LEVEL_STYLE, trace, WIDTH } from "./layout";

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
  kind: "source" | "process" | "target";
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
  const shape = data.kind === "process" ? "rounded-full" : "rounded-lg";
  const look = data.kind === "source" ? "border-slate-300 bg-white dark:border-slate-700 dark:bg-slate-900" : style.box;
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

const nodeTypes = { item: Item, box: Box };

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
  const graph = useMemo(() => buildGraph(status.nodes, status.processes), [status]);
  const focus = hovered ?? selected;
  const lit = useMemo(() => (focus ? trace(graph.links, focus) : null), [graph, focus]);

  const { nodes, edges } = useMemo(() => {
    const processes = new Map(status.processes.map((p) => [`p:${p.id}`, p]));
    const flowNodes = new Map(status.nodes.map((n) => [n.id, n]));
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
      const n = flowNodes.get(id)!;
      return {
        id, type: "item", position: { x, y },
        data: { label: n.label, sub: n.detail, level: n.level, kind: n.kind, ...common },
      };
    });

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
    return { nodes: [...boxes, ...items], edges };
  }, [status, graph, lit, selected]);

  return (
    <div className="h-[620px] rounded-xl border border-slate-200 dark:border-slate-800">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, node) => node.type === "item" && onSelect(node.id)}
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
