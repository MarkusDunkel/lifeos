import { describe, expect, it } from "vitest";
import { mirrorOf, summarize, textForClaude } from "./mirror";

const presentation = [
  "type: presentation",
  "source: onedrive",
  "title: Q3 'review'",
  "url: https://example.invalid/x",
  "onedrive_path: Decks/Q3.pptx",
  "slides: 2",
  "mirror: true",
].join("\n");

const moodleFile = [
  "type: moodle-file",
  "title: Lecture script",
  "course: 'Cost Accounting: Basics'",
  "section: Week 1",
  "url: https://moodle.example.invalid/mod/resource/view.php?id=3",
  "file: script.pdf",
  "moodle_id: 3:/script.pdf",
  "pages: 12",
].join("\n");

describe("mirrorOf", () => {
  it("reads presentation mirrors", () => {
    const m = mirrorOf(presentation)!;
    expect(m.kind).toBe("presentation");
    expect(m.title).toBe("Q3 'review'");
    expect(m.details).toEqual(["2 slides", "OneDrive: Decks/Q3.pptx"]);
    expect(m.syncPath).toBe("/api/presentations/sync");
    expect(mirrorOf(presentation + "\nstatus: deleted")!.deleted).toBe(true);
  });
  it("reads Moodle files and course overviews", () => {
    const m = mirrorOf(moodleFile)!;
    expect(m.kind).toBe("moodle-file");
    expect(m.details).toEqual(["Cost Accounting: Basics", "Week 1", "script.pdf", "12 pages"]);
    expect(m.openLabel).toBe("Open in Moodle");
    const course = mirrorOf("type: moodle-course\ntitle: CA\nshortname: CA1\nurl: https://moodle.example.invalid/course/view.php?id=1")!;
    expect(course.kind).toBe("moodle-course");
    expect(course.details).toEqual(["Course overview", "CA1"]);
  });
  it("ignores other notes and unsafe links", () => {
    expect(mirrorOf(null)).toBeNull();
    expect(mirrorOf("type: note\nurl: https://example.invalid")).toBeNull();
    expect(mirrorOf(presentation.replace("https://example.invalid/x", "javascript:alert(1)"))).toBeNull();
  });
});

describe("helpers", () => {
  it("builds the text for Claude without title and notice", () => {
    const meta = mirrorOf(presentation)!;
    const body = "\n# Q3\n\n> Mirror of [Q3](x) in OneDrive …\n\n## Slide 1 · Intro\n\nHello\n";
    expect(textForClaude(meta, body)).toBe(
      "\"Q3 'review'\" (2 slides, OneDrive: Decks/Q3.pptx)\n\n## Slide 1 · Intro\n\nHello\n",
    );
  });
  it("summarizes a sync", () => {
    const r = { created: ["a"], updated: [], renamed: [], deleted: [], unchanged: 3, skipped: ["v"], errors: ["x"], synced_at: "" };
    expect(summarize(r)).toBe("1 new · 3 unchanged · 1 not mirrored · 1 failed");
  });
});
