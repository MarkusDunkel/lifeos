import { describe, expect, it } from "vitest";
import type { DayView, Goal, GoalInput, PlanBlock } from "./api";
import { addDays, amountText, dayLabel, progress, resolveGoals, timeline } from "./format";

const block = (start: string, end: string, status: PlanBlock["status"] = "planned"): PlanBlock => ({
  id: start,
  date: "2026-11-24",
  start,
  end,
  category: "uni",
  goal: "Uni",
  title: `B ${start}`,
  reason: "",
  status,
  minutes_done: null,
  minutes: 60,
  edited: false,
  pinned: false,
});

describe("planning format", () => {
  it("adds days across month ends", () => {
    expect(addDays("2026-11-30", 1)).toBe("2026-12-01");
    expect(addDays("2026-01-01", -1)).toBe("2025-12-31");
  });

  it("labels today and tomorrow", () => {
    expect(dayLabel("2026-11-23", "2026-11-23")).toBe("Today");
    expect(dayLabel("2026-11-24", "2026-11-23")).toBe("Tomorrow");
    expect(dayLabel("2026-11-25", "2026-11-23")).toBe("Wed 25.11.");
  });

  it("merges blocks and appointments in time order, without removed blocks", () => {
    const day: DayView = {
      date: "2026-11-24",
      blocks: [block("10:00", "11:00"), block("08:00", "09:00"), block("12:00", "13:00", "removed")],
      appointments: [{ start: "09:00", end: "10:00", title: "Dentist", calendar: "Private" }],
      allDay: [],
    };
    expect(timeline(day).map((i) => i.start)).toEqual(["08:00", "09:00", "10:00"]);
  });

  it("computes progress shares capped at 1", () => {
    const goal = { target: 8, done: 2, planned: 10, unit: "hours" } as Goal;
    expect(progress(goal)).toEqual({ done: 0.25, planned: 1 });
    expect(amountText(2.5, "hours")).toBe("2.5 h");
    expect(amountText(6, "sessions")).toBe("6×");
  });

  it("resolves groups like the backend: percentages of what fixed members leave", () => {
    const goal = (name: string, amount: number | null, unit: GoalInput["unit"], group = ""): GoalInput => ({
      name,
      amount,
      unit,
      group,
      priority: "mittel",
      note: "",
    });
    const goals = [
      goal("Sport", 6, "sessions"),
      goal("LifeOS", 20, "percent", "Arbeit"),
      goal("Arbeit", 30, "hours"),
      goal("Uni", null, "hours", "Arbeit"),
      goal("Bewerben", 60, "percent", "Arbeit"),
      goal("Portfolio", 20, "percent", "Arbeit"),
    ];
    const r = resolveGoals(goals, 6);
    expect(r.get("Arbeit")).toMatchObject({ target: 30, isGroup: true, warnings: [] });
    expect(r.get("Uni")?.target).toBe(6);
    expect(r.get("LifeOS")?.target).toBe(4.8);
    expect(r.get("Bewerben")?.target).toBe(14.4);
    expect(r.get("Sport")).toMatchObject({ target: 6, unit: "sessions" });
    goals[4] = goal("Bewerben", 10, "percent", "Arbeit");
    expect(resolveGoals(goals, 0).get("Arbeit")?.warnings[0]).toMatch(/only 50 %/);
  });
});
