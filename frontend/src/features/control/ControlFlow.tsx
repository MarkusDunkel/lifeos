import { Background, Controls, type Edge, Handle, type Node, type NodeProps, Position, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Hand, Monitor, Server, Wrench } from "lucide-react";
import { useMemo } from "react";
import type { ControlStatus, Level, Where } from "./api";
import { LEVEL_STYLE, layout } from "./layout";

const COLUMN = 250;
const ROW = 66;
const WIDTH = 200;

const WHERE_ICON: Record<Where, typeof Server> = { server: Server, pc: Monitor, manual: Hand, host: Wrench };
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
  selected: boolean;
};

function Item({ data }: NodeProps<Node<ItemData>>) {
  const style = LEVEL_STYLE[data.level];
  const Icon = data.where ? WHERE_ICON[data.where] : null;
  const shape = data.kind === "process" ? "rounded-full" : "rounded-lg";
  const neutral = data.kind === "source" ? "border-slate-300 bg-white dark:border-slate-700 dark:bg-slate-900" : style.box;
  return (
    <div
      style={{ width: WIDTH }}
      className={`border px-3 py-1.5 text-left shadow-sm ${shape} ${neutral} ${data.selected ? "ring-2 ring-sky-500" : ""}`}
    >
      {data.kind !== "source" && <Handle type="target" position={Position.Left} className="!bg-slate-400" />}
      <div className="flex items-center gap-1.5 text-xs font-medium text-slate-800 dark:text-slate-100">
        {data.kind !== "source" && <span className={`size-2 shrink-0 rounded-full ${style.dot}`} />}
        <span className="truncate">{data.label}</span>
        {Icon && <Icon className="ml-auto size-3.5 shrink-0 text-slate-400" />}
      </div>
      <div className="truncate text-[10px] text-slate-500">
        {data.mirror ? "mirror · " : ""}
        {data.sub}
      </div>
      <Handle type="source" position={Position.Right} className="!bg-slate-400" />
    </div>
  );
}

const nodeTypes = { item: Item };

export function ControlFlow({
  status,
  selected,
  onSelect,
}: {
  status: ControlStatus;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const { nodes, edges } = useMemo(() => {
    const processes = status.processes.filter((p) => !p.system);
    const placed = layout(status.nodes, status.processes);
    const nodes: Node<ItemData>[] = placed.map(({ id, column, row }) => {
      const position = { x: column * COLUMN, y: row * ROW };
      if (id.startsWith("p:")) {
        const p = processes.find((x) => `p:${x.id}` === id)!;
        return {
          id,
          type: "item",
          position,
          data: {
            label: p.label, sub: p.trigger, level: p.level, kind: "process", where: p.where,
            mirror: p.mirror, selected: selected === id,
          },
        };
      }
      const n = status.nodes.find((x) => x.id === id)!;
      return {
        id,
        type: "item",
        position,
        data: { label: n.label, sub: n.detail, level: n.level, kind: n.kind, selected: selected === id },
      };
    });
    const edges: Edge[] = [];
    for (const p of processes) {
      const color = LEVEL_STYLE[p.level].edge;
      const style = { stroke: color, strokeWidth: p.level === "fail" ? 2.5 : 1.5 };
      const common = { type: "smoothstep", style, animated: p.level === "fail" };
      for (const i of p.inputs) edges.push({ id: `${i}>${p.id}`, source: i, target: `p:${p.id}`, ...common });
      for (const o of p.outputs) edges.push({ id: `${p.id}>${o}`, source: `p:${p.id}`, target: o, ...common });
    }
    return { nodes, edges };
  }, [status, selected]);

  return (
    <div className="h-[560px] rounded-xl border border-slate-200 dark:border-slate-800">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, node) => onSelect(node.id)}
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
