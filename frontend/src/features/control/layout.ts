import type { Check, FlowNode, Level, ProcessHealth } from "./api";

export const LEVEL_RANK: Record<Level, number> = { off: 0, ok: 1, warn: 2, fail: 3 };

export const LEVEL_STYLE: Record<Level, { dot: string; box: string; text: string; edge: string; label: string }> = {
  ok: {
    dot: "bg-emerald-500",
    box: "border-emerald-300 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/40",
    text: "text-emerald-700 dark:text-emerald-300",
    edge: "#10b981",
    label: "OK",
  },
  warn: {
    dot: "bg-amber-500",
    box: "border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/40",
    text: "text-amber-700 dark:text-amber-300",
    edge: "#f59e0b",
    label: "Warning",
  },
  fail: {
    dot: "bg-red-500",
    box: "border-red-400 bg-red-50 dark:border-red-800 dark:bg-red-950/40",
    text: "text-red-700 dark:text-red-300",
    edge: "#ef4444",
    label: "Problem",
  },
  off: {
    dot: "bg-slate-300 dark:bg-slate-600",
    box: "border-dashed border-slate-300 bg-white text-slate-400 dark:border-slate-700 dark:bg-slate-900",
    text: "text-slate-400",
    edge: "#cbd5e1",
    label: "Not set up",
  },
};

export const checkLevel = (c: Check): Level => (c.ok ? "ok" : c.ok === null ? "warn" : "fail");

export type Placed = { id: string; column: number; row: number };

/**
 * Columns by longest path: sources first, each process right of its inputs, each target
 * right of the processes writing it. Within a column, nodes follow the average row of
 * their predecessors (one barycenter pass), which keeps most edges short and straight.
 */
export function layout(nodes: FlowNode[], processes: ProcessHealth[]): Placed[] {
  const flow = processes.filter((p) => !p.system);
  const writers = new Map<string, ProcessHealth[]>();
  for (const p of flow) for (const out of p.outputs) writers.set(out, [...(writers.get(out) ?? []), p]);

  const column = new Map<string, number>();
  const nodeColumn = (id: string, seen: Set<string>): number => {
    const known = column.get(id);
    if (known !== undefined) return known;
    const node = nodes.find((n) => n.id === id);
    if (!node || node.kind === "source" || seen.has(id)) return 0;
    seen.add(id);
    const from = writers.get(id) ?? [];
    const c = from.length ? Math.max(...from.map((p) => procColumn(p, seen))) + 1 : 2;
    column.set(id, c);
    return c;
  };
  const procColumn = (p: ProcessHealth, seen: Set<string>): number => {
    const key = `p:${p.id}`;
    const known = column.get(key);
    if (known !== undefined) return known;
    const c = Math.max(0, ...p.inputs.map((i) => nodeColumn(i, seen))) + 1;
    column.set(key, c);
    return c;
  };
  for (const n of nodes) nodeColumn(n.id, new Set());
  for (const p of flow) procColumn(p, new Set());

  // predecessors of every placed item, for the ordering pass
  const preds = new Map<string, string[]>();
  for (const p of flow) {
    preds.set(`p:${p.id}`, p.inputs);
    for (const out of p.outputs) preds.set(out, [...(preds.get(out) ?? []), `p:${p.id}`]);
  }
  const items = [...nodes.map((n) => n.id), ...flow.map((p) => `p:${p.id}`)];
  const order = new Map(items.map((id, i) => [id, i]));
  const maxColumn = Math.max(0, ...column.values());
  const rows = new Map<string, number>();
  const placed: Placed[] = [];
  for (let c = 0; c <= maxColumn; c++) {
    const here = items.filter((id) => (column.get(id) ?? 0) === c);
    const score = (id: string) => {
      const ps = (preds.get(id) ?? []).map((p) => rows.get(p)).filter((r): r is number => r !== undefined);
      return ps.length ? ps.reduce((a, b) => a + b, 0) / ps.length : (order.get(id) ?? 0);
    };
    here.sort((a, b) => score(a) - score(b) || (order.get(a) ?? 0) - (order.get(b) ?? 0));
    here.forEach((id, row) => {
      rows.set(id, row);
      placed.push({ id, column: c, row });
    });
  }
  return placed;
}
