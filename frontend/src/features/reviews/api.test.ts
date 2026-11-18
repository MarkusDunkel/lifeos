import { describe, expect, it } from "vitest";
import type { RoundInfo } from "../workspaces/api";
import { pendingComments, type ReviewDocument, ticketUrl } from "./api";

const round = (editable: boolean, items: number): RoundInfo => ({
  round: "20260101T000000Z-abcd",
  state: editable ? "draft" : "sealed",
  items,
  created_at: "",
  sealed_at: null,
  stale: false,
  editable,
});

const doc = (index: number, source: string, rounds: RoundInfo[]): ReviewDocument => ({
  index,
  label: source,
  source,
  exists: true,
  content: "",
  sha256: "",
  view: "markdown",
  rounds,
});

describe("pendingComments", () => {
  it("counts open drafts once per file and ignores sealed rounds", () => {
    const docs = [
      doc(0, "a.md", [round(true, 2), round(false, 5)]),
      doc(1, "b.md", [round(true, 1)]),
      doc(2, "b.md", [round(true, 1)]), // same file shown twice
      doc(3, "c.md", [round(false, 3)]),
    ];
    expect(pendingComments(docs)).toBe(3);
  });
});

describe("ticketUrl", () => {
  it("links owner/repo#n to the issue", () => {
    expect(ticketUrl("someone/board#7")).toBe("https://github.com/someone/board/issues/7");
    expect(ticketUrl("#7")).toBeNull();
  });
});
