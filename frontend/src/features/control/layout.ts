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


export const COLUMN = 260;
export const ROW = 66;
export const WIDTH = 200;
const GROUP_HEAD = 30; // space for the box title above the first member
const GROUP_PAD = 14; // space below the last member
const NODE_HEIGHT = 46; // rendered height of a box (two text lines)
const GROUP_LABELS: Record<string, string> = { vault: "Vault · ready/knowledge" };

export const groupId = (group: string) => `group:${group}`;

export type Placed = { id: string; column: number; row: number; x: number; y: number };
export type GroupBox = { id: string; label: string; x: number; y: number; width: number; height: number };
export type GraphEdge = { id: string; source: string; target: string; process: string };
export type Graph = { placed: Placed[]; groups: GroupBox[]; edges: GraphEdge[]; links: [string, string][] };

const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;

/**
 * The flow diagram's geometry.
 *
 * - Columns by longest path: sources first, each process right of its inputs, each target
 *   right of the processes writing it.
 * - Rows by barycenter sweeps (forward, backward, forward): every item moves towards the
 *   average row of its neighbours, so most edges run short and nearly straight.
 * - Targets of a group (the vault folders) stay together in one box; a process reading
 *   from the group (the vault sync) gets a single edge from the box instead of one per folder.
 */
export function buildGraph(nodes: FlowNode[], processes: ProcessHealth[]): Graph {
  const flow = processes.filter((p) => !p.system);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const pid = (p: ProcessHealth) => `p:${p.id}`;

  const writers = new Map<string, ProcessHealth[]>();
  for (const p of flow) for (const out of p.outputs) writers.set(out, [...(writers.get(out) ?? []), p]);

  const column = new Map<string, number>();
  const nodeColumn = (id: string, seen: Set<string>): number => {
    const known = column.get(id);
    if (known !== undefined) return known;
    const node = byId.get(id);
    if (!node || node.kind === "source" || seen.has(id)) return 0;
    seen.add(id);
    const from = writers.get(id) ?? [];
    const c = from.length ? Math.max(...from.map((p) => procColumn(p, seen))) + 1 : 2;
    column.set(id, c);
    return c;
  };
  const procColumn = (p: ProcessHealth, seen: Set<string>): number => {
    const known = column.get(pid(p));
    if (known !== undefined) return known;
    const c = Math.max(0, ...p.inputs.map((i) => nodeColumn(i, seen))) + 1;
    column.set(pid(p), c);
    return c;
  };
  for (const n of nodes) column.set(n.id, nodeColumn(n.id, new Set()));
  for (const p of flow) procColumn(p, new Set());

  // neighbours for the sweeps
  const preds = new Map<string, string[]>();
  const succs = new Map<string, string[]>();
  const link = (a: string, b: string) => {
    succs.set(a, [...(succs.get(a) ?? []), b]);
    preds.set(b, [...(preds.get(b) ?? []), a]);
  };
  for (const p of flow) {
    for (const i of p.inputs) link(i, pid(p));
    for (const o of p.outputs) link(pid(p), o);
  }

  const items = [...nodes.map((n) => n.id), ...flow.map(pid)];
  const catalogOrder = new Map(items.map((id, i) => [id, i]));
  const maxColumn = Math.max(0, ...column.values());
  const columns: string[][] = Array.from({ length: maxColumn + 1 }, (_, c) =>
    items.filter((id) => (column.get(id) ?? 0) === c),
  );
  // y position of every item; starts packed, then follows the neighbours
  const pos = new Map<string, number>();
  for (const col of columns) col.forEach((id, i) => pos.set(id, i * ROW));
  const meanPos = (ids: string[] | undefined) => {
    const ys = (ids ?? []).map((n) => pos.get(n)).filter((y): y is number => y !== undefined);
    return ys.length ? mean(ys) : null;
  };

  const sortColumn = (c: number, neighbours: Map<string, string[]>) => {
    const own = new Map(columns[c].map((id) => [id, meanPos(neighbours.get(id)) ?? pos.get(id) ?? 0]));
    const groupScore = new Map<string, number>();
    for (const id of columns[c]) {
      const g = byId.get(id)?.group;
      if (g && !groupScore.has(g)) {
        groupScore.set(g, mean(columns[c].filter((x) => byId.get(x)?.group === g).map((x) => own.get(x)!)));
      }
    }
    // members of a group share the group's score, so they stay together
    const key = (id: string) => {
      const g = byId.get(id)?.group;
      return g ? groupScore.get(g)! : own.get(id)!;
    };
    columns[c].sort(
      (a, b) =>
        key(a) - key(b) ||
        Number(!byId.get(a)?.group) - Number(!byId.get(b)?.group) ||
        own.get(a)! - own.get(b)! ||
        catalogOrder.get(a)! - catalogOrder.get(b)!,
    );
  };

  // place a column top-down: each item as close to the average height of its predecessors
  // as the items above allow (``align``), or packed (the sources)
  const boxes = new Map<number, GroupBox[]>();
  const placeColumn = (c: number, align: boolean) => {
    const found: GroupBox[] = [];
    let cursor = 0;
    let open: { group: string; top: number; bottom: number } | null = null;
    const close = () => {
      if (!open) return;
      found.push({
        id: groupId(open.group), label: GROUP_LABELS[open.group] ?? open.group,
        x: c * COLUMN - 12, y: open.top, width: WIDTH + 24, height: open.bottom + GROUP_PAD - open.top,
      });
      cursor = open.bottom + GROUP_PAD + (ROW - NODE_HEIGHT);
      open = null;
    };
    for (const id of columns[c]) {
      const g = byId.get(id)?.group ?? null;
      if (open && open.group !== g) close();
      const desired = align ? (meanPos(preds.get(id)) ?? cursor) : cursor;
      if (g && !open) {
        const top = Math.max(cursor, desired - GROUP_HEAD);
        open = { group: g, top, bottom: top };
        cursor = top + GROUP_HEAD;
      }
      const y = Math.max(cursor, desired);
      pos.set(id, y);
      cursor = y + ROW;
      if (open) open.bottom = y + NODE_HEIGHT;
    }
    close();
    boxes.set(c, found);
  };

  for (let round = 0; round < 2; round++) {
    for (let c = 1; c <= maxColumn; c++) {
      sortColumn(c, preds);
      placeColumn(c, true);
    }
    sortColumn(0, succs); // sources next to what reads them
    placeColumn(0, false);
  }
  for (let c = 1; c <= maxColumn; c++) {
    sortColumn(c, preds);
    placeColumn(c, true);
  }

  const placed: Placed[] = columns.flatMap((col, c) =>
    col.map((id, row) => ({ id, column: c, row, x: c * COLUMN, y: pos.get(id) ?? 0 })),
  );
  const groups = [...boxes.values()].flat();

  // edges; inputs from a group member collapse into one edge from the group box
  const edges: GraphEdge[] = [];
  const links: [string, string][] = [];
  const seen = new Set<string>();
  for (const p of flow) {
    for (const i of p.inputs) {
      const g = byId.get(i)?.group;
      const source = g ? groupId(g) : i;
      const id = `${source}>${p.id}`;
      if (seen.has(id)) continue;
      seen.add(id);
      edges.push({ id, source, target: pid(p), process: p.id });
      links.push([source, pid(p)]);
    }
    for (const o of p.outputs) {
      edges.push({ id: `${p.id}>${o}`, source: pid(p), target: o, process: p.id });
      links.push([pid(p), o]);
    }
  }
  for (const n of nodes) if (n.group) links.push([n.id, groupId(n.group)]);
  return { placed, groups, edges, links };
}

/** Everything upstream and downstream of ``focus``: the flows it belongs to. */
export function trace(links: [string, string][], focus: string): Set<string> {
  const walk = (forward: boolean) => {
    const found = new Set<string>([focus]);
    const queue = [focus];
    while (queue.length) {
      const at = queue.shift()!;
      for (const [a, b] of links) {
        const [from, to] = forward ? [a, b] : [b, a];
        if (from === at && !found.has(to)) {
          found.add(to);
          queue.push(to);
        }
      }
    }
    return found;
  };
  return new Set([...walk(true), ...walk(false)]);
}
