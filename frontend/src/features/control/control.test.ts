import { describe, expect, it } from "vitest";
import type { FlowNode, ProcessHealth } from "./api";
import { sortProcesses } from "./ControlPage";
import { formatAgo } from "./format";
import { buildGraph, checkLevel, trace } from "./layout";
import { compact, niceScale, stackBars } from "./tokens";

const node = (id: string, kind: "source" | "dataset"): FlowNode => ({
  id, label: id, kind, detail: "", folder: null, group: null, level: "ok", reason: "",
});
const proc = (id: string, inputs: string[], outputs: string[], extra: Partial<ProcessHealth> = {}): ProcessHealth => ({
  id, label: id, where: "server", trigger: "", enabled: true, level: "ok", reason: "", description: "",
  inputs, outputs, status: null, message: "", lastRun: null, lastSuccess: null, history: [], checks: [],
  runPath: null, canRequest: true, hint: "", mirror: false, system: false, foundation: false, reactsTo: [], ai: [], ...extra,
});

describe("buildGraph", () => {
  it("places sources, processes and targets in columns by longest path", () => {
    const nodes = [node("moodle", "source"), node("kb", "dataset"), node("remote", "dataset")];
    const processes = [
      proc("sync", ["moodle"], ["kb"]),
      proc("git", ["kb"], ["remote"]),
      proc("helper", [], [], { system: true }),
    ];
    const col = Object.fromEntries(buildGraph(nodes, processes).placed.map((p) => [p.id, p.column]));
    expect(col).toEqual({ moodle: 0, "p:sync": 1, kb: 2, "p:git": 3, remote: 4 });
  });

  it("orders columns so that edges run straight", () => {
    const nodes = [node("a", "source"), node("b", "source"), node("ta", "dataset"), node("tb", "dataset")];
    const processes = [proc("pb", ["b"], ["tb"]), proc("pa", ["a"], ["ta"])];
    const placed = buildGraph(nodes, processes).placed;
    const row = (id: string) => placed.find((p) => p.id === id)!.row;
    expect(row("p:pa")).toBeLessThan(row("p:pb"));
    expect(row("ta")).toBeLessThan(row("tb"));
  });

  it("boxes a group and collapses its edges into one", () => {
    const v = (id: string) => ({ ...node(id, "dataset"), group: "vault" });
    const nodes = [node("s1", "source"), node("s2", "source"), node("s3", "source"), v("f1"), node("cal", "dataset"), v("f2"), node("remote", "dataset")];
    const processes = [
      proc("p1", ["s1"], ["f1"]),
      proc("p2", ["s2"], ["cal"]),
      proc("p3", ["s3"], ["f2"]),
      proc("git", ["f1", "f2"], ["remote"]),
    ];
    const g = buildGraph(nodes, processes);
    const col2 = g.placed.filter((p) => p.column === 2).sort((a, b) => a.row - b.row).map((p) => p.id);
    expect(Math.abs(col2.indexOf("f1") - col2.indexOf("f2"))).toBe(1); // members stay together
    expect(g.groups).toHaveLength(1);
    expect(g.edges.filter((e) => e.target === "p:git").map((e) => e.source)).toEqual(["group:vault"]);
    const box = g.groups[0];
    for (const id of ["f1", "f2"]) {
      const p = g.placed.find((x) => x.id === id)!;
      expect(p.y).toBeGreaterThan(box.y);
      expect(p.y).toBeLessThan(box.y + box.height);
    }
  });

  it("traces a flow upstream and downstream", () => {
    const v = (id: string) => ({ ...node(id, "dataset"), group: "vault" });
    const nodes = [node("s1", "source"), node("s2", "source"), v("f1"), v("f2"), node("remote", "dataset")];
    const processes = [proc("p1", ["s1"], ["f1"]), proc("p2", ["s2"], ["f2"]), proc("git", ["f1", "f2"], ["remote"])];
    const lit = trace(buildGraph(nodes, processes).links, "s1");
    expect([...lit].sort()).toEqual(["f1", "group:vault", "p:git", "p:p1", "remote", "s1"]);
  });
});

describe("helpers", () => {
  it("formats ages", () => {
    const now = Date.parse("2026-03-10T12:00:00Z");
    expect(formatAgo(null, now)).toBe("never");
    expect(formatAgo("2026-03-10T11:58:00Z", now)).toBe("2 min ago");
    expect(formatAgo("2026-03-08T12:00:00Z", now)).toBe("2 days ago");
  });

  it("lists problems first", () => {
    const list = [proc("b", [], []), proc("a", [], [], { level: "fail" }), proc("c", [], [], { level: "warn" })];
    expect(sortProcesses(list).map((p) => p.id)).toEqual(["a", "c", "b"]);
  });

  it("maps checks to levels", () => {
    expect(checkLevel({ id: "x", label: "", ok: true, message: "", fix: "" })).toBe("ok");
    expect(checkLevel({ id: "x", label: "", ok: null, message: "", fix: "" })).toBe("warn");
    expect(checkLevel({ id: "x", label: "", ok: false, message: "", fix: "" })).toBe("fail");
  });
});

describe("mirrorFor", () => {
  it("finds the most specific mirror folder", async () => {
    const { mirrorFor } = await import("./MirrorBadge");
    const nodes: FlowNode[] = [
      { ...node("teams", "dataset"), folder: "Teams" },
      { ...node("chats", "dataset"), folder: "Teams/Chats" },
      { ...node("plan", "dataset"), folder: "Planning" },
    ];
    const processes = [
      proc("files", [], ["teams"], { mirror: true }),
      proc("mail", [], ["chats"], { mirror: true }),
      proc("planner", [], ["plan"]),
    ];
    const status = { nodes, processes } as unknown as Parameters<typeof mirrorFor>[1];
    expect(mirrorFor("Teams/Chats/a.md", status)?.process.id).toBe("mail");
    expect(mirrorFor("Teams/Course/b.md", status)?.process.id).toBe("files");
    expect(mirrorFor("Planning/Goals.md", status)).toBeNull();
    expect(mirrorFor("Teams/Course", status, true)).toBeNull();
  });
});

describe("buildFlows", () => {
  it("puts actionables last, their makers before them, and the foundation below", async () => {
    const { buildFlows, VAULT_STUB } = await import("./layout");
    const nodes: FlowNode[] = [
      node("moodle", "source"),
      { ...node("kb", "dataset"), group: "vault" },
      { ...node("act", "dataset"), kind: "actionable" },
      node("cal", "source"),
      { ...node("plan", "dataset"), kind: "actionable" },
      node("remote", "dataset"),
    ];
    const processes = [
      proc("sync", ["moodle"], ["kb"]),
      proc("planner", ["kb"], ["act"]),
      proc("day", ["cal"], ["plan"], { reactsTo: ["plan"] }),
      proc("git", ["kb"], ["remote"], { foundation: true }),
    ];
    const g = buildFlows(nodes, processes);
    const at = (id: string) => g.placed.find((p) => p.id === id)!;
    expect(at("act").column).toBe(at("plan").column);
    expect(at("p:day").column).toBe(at("p:planner").column); // makers share a column
    expect(at("p:day").column).toBe(at("act").column - 1);
    expect(at("p:git").y).toBeGreaterThan(at("p:day").y);
    expect(g.bands).toHaveLength(1);
    expect(g.edges.find((e) => e.target === "p:git")?.source).toBe(VAULT_STUB);
    expect(trace(g.links, "moodle").has("p:git")).toBe(true); // tracing crosses into the band
    expect(g.feedback).toEqual([{ id: "plan~>day", source: "plan", target: "p:day", process: "day" }]);
    expect(g.edges.some((e) => e.source === "plan")).toBe(false); // the loop is no input edge
  });

  it("draws the second order between the main flow and the foundation", async () => {
    const { buildFlows } = await import("./layout");
    const nodes: FlowNode[] = [
      node("moodle", "source"),
      { ...node("kb", "dataset"), group: "vault" },
      node("posts", "dataset"),
      node("rated", "dataset"),
      { ...node("uni", "dataset"), kind: "actionable" },
      { ...node("tasks", "dataset"), kind: "actionable" },
      { ...node("boards", "dataset"), derivedFrom: ["uni", "tasks"] },
      { ...node("room", "dataset"), local: true },
      node("remote", "dataset"),
    ];
    const processes = [
      proc("sync", ["moodle"], ["kb"]),
      proc("rate", ["kb"], ["posts"]),
      proc("more", ["posts"], ["rated"]),
      proc("planner", ["rated"], ["uni"]),
      proc("agents", [], ["tasks"]),
      proc("manager", ["boards", "room"], ["tasks"], { secondOrder: true, reactsTo: ["tasks"] }),
      proc("git", ["kb"], ["remote"], { foundation: true }),
    ];
    const g = buildFlows(nodes, processes);
    const at = (id: string) => g.placed.find((p) => p.id === id)!;
    expect(g.bands.map((b) => b.label.split(" ·")[0])).toEqual(["Second order", "Technical foundation"]);
    expect(at("s:boards").column).toBe(2); // starts under the vault box, left of the makers
    expect(at("s:boards").column).toBeLessThan(at("p:planner").column);
    expect(at("s:tasks").column).toBe(at("uni").column); // actionables stay in the last column
    expect(at("s:tasks").y).toBeGreaterThan(at("tasks").y);
    expect(at("p:git").y).toBeGreaterThan(at("p:manager").y); // foundation stays at the bottom
    expect(at("boards")).toBeUndefined(); // only the copy is drawn
    expect(trace(g.links, "uni").has("p:manager")).toBe(true); // boards are made of the actionables
    const loop = g.feedback.find((e) => e.target === "p:manager")!;
    expect(loop.source).toBe("s:tasks");
    expect(loop.floor).toBeGreaterThan(at("s:tasks").y);
  });
});

describe("lanes", () => {
  it("never lets an edge run behind a box", async () => {
    const { buildFlows } = await import("./layout");
    const nodes: FlowNode[] = [
      node("cal", "source"),
      node("moodle", "source"),
      { ...node("kb", "dataset"), group: "vault" },
      { ...node("plan", "dataset"), group: "vault" },
      { ...node("act", "dataset"), kind: "actionable" },
      { ...node("day", "dataset"), kind: "actionable" },
    ];
    const processes = [
      proc("sync", ["moodle"], ["kb"]),
      proc("uni", ["kb"], ["act"]),
      proc("planner", ["cal", "plan"], ["day"]), // cal skips the vault column
    ];
    const g = buildFlows(nodes, processes);
    const col = (id: string) => g.placed.find((p) => p.id === id)!.column;
    for (const e of g.edges) {
      const source = e.source.startsWith("group:") ? "kb" : e.source;
      const span = col(e.target) - col(source);
      expect(e.via?.length ?? 0).toBe(Math.max(0, span - 1));
      for (const v of e.via ?? []) {
        const c = Math.round(v.x / 260);
        for (const p of g.placed.filter((x) => x.column === c)) {
          expect(v.y < p.y - 2 || v.y > p.y + 48).toBe(true); // the lane is free
        }
        for (const box of g.groups.filter((b) => Math.round((b.x + 12) / 260) === c)) {
          expect(v.y < box.y || v.y > box.y + box.height).toBe(true);
        }
      }
    }
  });
});

describe("token chart", () => {
  const bucket = (auto: number, semi: number, interactive: number) => ({ start: "2026-03-10T10:00:00Z", auto, semi, interactive });

  it("rounds the axis up to a nice maximum", () => {
    expect(niceScale(0)).toEqual({ top: 1, step: 0.25 });
    expect(niceScale(870)).toEqual({ top: 1000, step: 250 });
    expect(niceScale(1_300_000)).toEqual({ top: 1_500_000, step: 500_000 });
  });

  it("stacks auto, semi, interactive from the base with a gap and rounds only the top", () => {
    const segs = stackBars([bucket(50, 0, 50), bucket(0, 0, 0)], 100, 100, 100, 2);
    expect(segs.map((s) => s.mode)).toEqual(["auto", "interactive"]);
    expect(segs[0]).toMatchObject({ x: 1, width: 48, y: 52, height: 48, top: false });
    expect(segs[1]).toMatchObject({ y: 0, height: 50, top: true });
  });

  it("formats compact numbers", () => {
    expect(compact(950)).toBe("950");
    expect(compact(12_400)).toBe("12k");
    expect(compact(1_400_000)).toBe("1.4M");
  });
});
