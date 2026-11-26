import { describe, expect, it } from "vitest";
import { type AgentQuota, type AgentSummary, onBoard, sparkBars, totals } from "./api";

const quota = (extra: Partial<AgentQuota>): AgentQuota => ({
  processes: [], runsToday: 0, dailyLimit: null, exhausted: false, tokensToday: 0, usdToday: 0, note: "", limits: [], ...extra,
});
const agent = (name: string, extra: Partial<AgentSummary> = {}): AgentSummary => ({
  name, title: name, board: null, boardTitle: null, model: "m", billing: "api", path: `agents/${name}.md`, revision: null, quota: null, ...extra,
});

describe("agents page", () => {
  it("adds up what the agents did today", () => {
    const sum = totals([
      agent("a", { quota: quota({ tokensToday: 1000, usdToday: 0.5, runsToday: 2, exhausted: true }) }),
      agent("b", { quota: quota({ tokensToday: 250, runsToday: 1 }) }),
      agent("c"), // counters unreadable
    ]);
    expect(sum).toEqual({ tokens: 1250, usd: 0.5, runs: 3, exhausted: 1 });
  });

  it("groups by the runner, or by the board for older servers", () => {
    expect(onBoard(agent("a", { runner: "workflow" }))).toBe(true);
    expect(onBoard(agent("b", { runner: "job", board: "tasks" }))).toBe(false);
    expect(onBoard(agent("c", { board: "tasks" }))).toBe(true);
    expect(onBoard(agent("d"))).toBe(false);
  });

  it("scales the small chart to its largest day", () => {
    const bars = sparkBars([0, 50, 100, 1], 70, 20, 2);
    expect(bars.map((b) => b.height)).toEqual([0, 10, 20, 1.5]); // a day with tokens stays visible
    expect(bars[2].y).toBe(0);
    expect(bars[1].x).toBeCloseTo(18.5);
    expect(sparkBars([0, 0], 70, 20).every((b) => b.height === 0)).toBe(true);
    expect(sparkBars([], 70, 20)).toEqual([]);
  });
});
