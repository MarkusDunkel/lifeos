import { describe, expect, it } from "vitest";
import { resolveRelative, resolveWikilink, splitFrontmatter } from "./links";

const files = [
  "Course/Economics.md",
  "Course/Costs.md",
  "Costs.md",
  "Projects/Alpha/Plan.md",
  "Course/img/diagram.png",
];

describe("resolveWikilink", () => {
  it("prefers a file in the same folder", () => {
    expect(resolveWikilink("Costs", files, "Course/Economics.md")).toBe("Course/Costs.md");
    expect(resolveWikilink("Costs", files, "Projects/Alpha/Plan.md")).toBe("Costs.md");
  });
  it("matches path suffixes, headings and attachments", () => {
    expect(resolveWikilink("Alpha/Plan#Goal", files, "x.md")).toBe("Projects/Alpha/Plan.md");
    expect(resolveWikilink("diagram.png", files, "x.md")).toBe("Course/img/diagram.png");
    expect(resolveWikilink("missing", files, "x.md")).toBeNull();
  });
});

describe("resolveRelative", () => {
  it("handles ./ and ../", () => {
    expect(resolveRelative("../Costs.md", "Course/Economics.md")).toBe("Costs.md");
    expect(resolveRelative("./img/a%20b.png", "Course/Economics.md")).toBe("Course/img/a b.png");
  });
});

describe("splitFrontmatter", () => {
  it("separates YAML frontmatter", () => {
    expect(splitFrontmatter("---\nsubject: Economics\n---\n# T\n")).toEqual({
      frontmatter: "subject: Economics",
      body: "# T\n",
    });
    expect(splitFrontmatter("# T").frontmatter).toBeNull();
  });
});
