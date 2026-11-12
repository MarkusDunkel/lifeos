import { describe, expect, it } from "vitest";
import { bodyLineOffset } from "../knowledge/links";
import type { FeedbackItem } from "./api";
import { itemsAt, merge, rangeLabel, shorten, sourceSlice, splitReportPath } from "./review";

const item = (id: string, start: number, end: number): FeedbackItem => ({
  id,
  start_line: start,
  end_line: end,
  scope: "blocks",
  quote: "",
  feedback: "",
  target: "",
});

describe("line ranges", () => {
  it("labels, merges and finds overlapping items", () => {
    expect(rangeLabel({ start: 3, end: 3 })).toBe("line 3");
    expect(rangeLabel({ start: 3, end: 5 })).toBe("lines 3-5");
    expect(merge({ start: 4, end: 6 }, { start: 2, end: 5 })).toEqual({ start: 2, end: 6 });
    const items = [item("F1", 1, 2), item("F2", 5, 9), item("F3", 10, 10)];
    expect(itemsAt(items, { start: 2, end: 5 }).map((i) => i.id)).toEqual(["F1", "F2"]);
  });

  it("slices source lines like the server", () => {
    expect(sourceSlice("a\r\nb\r\nc\n", { start: 2, end: 3 })).toBe("b\nc");
  });
});

describe("bodyLineOffset", () => {
  it("counts the frontmatter lines", () => {
    const content = "---\ntitle: x\n---\n# Heading\n";
    expect(bodyLineOffset(content, "# Heading\n")).toBe(3);
    expect(bodyLineOffset("# Heading\n", "# Heading\n")).toBe(0);
  });
});

describe("helpers", () => {
  it("shortens and splits paths", () => {
    expect(shorten("a\n\n  b", 10)).toBe("a b");
    expect(shorten("x".repeat(20), 10)).toBe(`${"x".repeat(9)}…`);
    expect(splitReportPath("archive/r.md")).toEqual({ folder: "archive", name: "r.md" });
    expect(splitReportPath("r.md")).toEqual({ folder: null, name: "r.md" });
  });
});
