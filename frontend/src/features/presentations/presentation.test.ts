import { describe, expect, it } from "vitest";
import { presentationOf, summarize, textForClaude } from "./presentation";

const front = [
  "type: presentation",
  "source: onedrive",
  "title: Q3 'review'",
  "url: https://example.invalid/x",
  "onedrive_path: Decks/Q3.pptx",
  "modified: '2026-01-01T00:00:00Z'",
  "slides: 2",
  "mirror: true",
].join("\n");

describe("presentationOf", () => {
  it("reads the mirror frontmatter", () => {
    expect(presentationOf(front)).toEqual({
      title: "Q3 'review'",
      url: "https://example.invalid/x",
      onedrivePath: "Decks/Q3.pptx",
      modified: "2026-01-01T00:00:00Z",
      slides: 2,
      deleted: false,
    });
    expect(presentationOf(front + "\nstatus: deleted")!.deleted).toBe(true);
  });
  it("ignores other notes and unsafe links", () => {
    expect(presentationOf(null)).toBeNull();
    expect(presentationOf("type: note")).toBeNull();
    expect(presentationOf(front.replace("https://example.invalid/x", "javascript:alert(1)"))).toBeNull();
  });
});

describe("helpers", () => {
  it("builds the text for Claude without the notice", () => {
    const meta = presentationOf(front)!;
    const body = "\n# Q3\n\n> Mirror of …\n\n## Slide 1 · Intro\n\nHello\n";
    expect(textForClaude(meta, body)).toBe(
      "Presentation \"Q3 'review'\" (2 slides, OneDrive: Decks/Q3.pptx)\n\n## Slide 1 · Intro\n\nHello\n",
    );
  });
  it("summarizes a sync", () => {
    const r = { created: ["a"], updated: [], renamed: [], deleted: [], unchanged: 3, errors: ["x"], synced_at: "" };
    expect(summarize(r)).toBe("1 new · 3 unchanged · 1 failed");
  });
});
