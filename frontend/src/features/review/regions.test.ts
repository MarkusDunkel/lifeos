import { describe, expect, it } from "vitest";
import { dragRegion, type TextBox, textIn } from "./regions";

describe("dragRegion", () => {
  it("turns a drag in any direction into percent of the page", () => {
    expect(dragRegion(2, { x: 300, y: 400 }, { x: 100, y: 200 }, 400, 800)).toEqual({
      page: 2,
      x: 25,
      y: 25,
      w: 50,
      h: 25,
    });
  });

  it("clamps to the page and ignores clicks", () => {
    expect(dragRegion(1, { x: -50, y: -10 }, { x: 500, y: 80 }, 400, 800)).toEqual({ page: 1, x: 0, y: 0, w: 100, h: 10 });
    expect(dragRegion(1, { x: 10, y: 10 }, { x: 12, y: 11 }, 400, 800)).toBeNull();
  });
});

describe("textIn", () => {
  const run = (str: string, x: number, y: number, eol = false): TextBox => ({ str, x, y, w: 10, h: 2, eol });
  const page = [run("Sehr geehrte", 10, 10), run(" ", 20, 10), run("Frau Muster,", 21, 10, true), run("Fußzeile", 10, 95)];

  it("collects the runs whose centre lies in the area", () => {
    expect(textIn(page, { x: 0, y: 5, w: 50, h: 10 })).toBe("Sehr geehrte Frau Muster,");
    expect(textIn(page, { x: 0, y: 90, w: 50, h: 10 })).toBe("Fußzeile");
    expect(textIn(page, { x: 60, y: 50, w: 10, h: 10 })).toBe("");
  });

  it("keeps a space where runs in between were left out", () => {
    const glued = [run("A", 10, 10), run("B", 50, 10), run("C", 12, 10)];
    expect(textIn(glued, { x: 0, y: 0, w: 30, h: 30 })).toBe("A C");
  });
});
