import { describe, expect, it } from "vitest";
import type { DayView, Goal, PlanBlock } from "./api";
import { addDays, amountText, dayLabel, progress, timeline } from "./format";

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
});
