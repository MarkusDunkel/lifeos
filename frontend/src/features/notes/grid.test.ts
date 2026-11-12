import { describe, expect, it } from "vitest";
import {
  applyView,
  columns,
  EMPTY,
  type Field,
  filterOptions,
  matchesQuery,
  newCells,
  type Note,
  preview,
  sameCells,
} from "./grid";

const field = (key: string, kind: Field["kind"], extra: Partial<Field> = {}): Field => ({
  key,
  label: key,
  kind,
  options: [],
  readonly: kind === "datetime",
  hidden: false,
  ...extra,
});

const fields = [
  field("body", "text"),
  field("type", "select", { options: ["Info", "Task"] }),
  field("status", "select"),
  field("due", "date"),
  field("created", "datetime"),
  field("lastChanged", "datetime"),
];

const note = (id: string, cells: Note["cells"]): Note => ({ id, file: "n.jsonl", order: null, cells, revision: id });

const notes = [
  note("a", { body: "Café order\nsecond line", type: "Task", status: "open", due: "2026-03-01", lastChanged: "2026-01-02" }),
  note("b", { body: "Reading list", type: "Info", status: null, due: null, lastChanged: "2026-01-05" }),
  note("c", { body: "Call the bank", type: "Task", status: "done", due: "2026-02-01", lastChanged: "2026-01-01" }),
];

const ids = (list: Note[]) => list.map((n) => n.id).join("");

describe("applyView", () => {
  it("sorts by last change, newest first, by default", () => {
    expect(ids(applyView(notes, {}))).toBe("bac");
  });
  it("sorts by a column with empty values last in both directions", () => {
    expect(ids(applyView(notes, { sort: "due" }))).toBe("cab");
    expect(ids(applyView(notes, { sort: "due", desc: true }))).toBe("acb");
  });
  it("filters by field values and by empty values", () => {
    expect(ids(applyView(notes, { f: { type: "Task" } }))).toBe("ac");
    expect(ids(applyView(notes, { f: { status: EMPTY } }))).toBe("b");
  });
  it("combines filters and text search", () => {
    expect(ids(applyView(notes, { f: { type: "Task" }, q: "bank" }))).toBe("c");
  });
});

describe("matchesQuery", () => {
  it("ignores case and accents and needs every word", () => {
    expect(matchesQuery(notes[0], "CAFE second")).toBe(true);
    expect(matchesQuery(notes[0], "cafe bank")).toBe(false);
  });
});

describe("display helpers", () => {
  it("previews the first line of the main text", () => {
    expect(preview(notes[0], fields)).toBe("Café order");
  });
  it("hides the main text and the creation date from the columns", () => {
    expect(columns(fields).map((f) => f.key)).toEqual(["type", "status", "due", "lastChanged"]);
  });
  it("offers configured and occurring filter values", () => {
    expect(filterOptions(fields[2], notes)).toEqual(["done", "open"]);
    expect(filterOptions(fields[1], [])).toEqual(["Info", "Task"]);
  });
  it("prefills new notes from active filters", () => {
    expect(newCells(fields, { type: "Task", status: EMPTY })).toEqual({
      body: null,
      type: "Task",
      status: null,
      due: null,
    });
  });
  it("treats null and empty strings as equal", () => {
    expect(sameCells({ a: null, b: "x" }, { a: "", b: "x" })).toBe(true);
    expect(sameCells({ a: "x" }, { a: "y" })).toBe(false);
  });
});
