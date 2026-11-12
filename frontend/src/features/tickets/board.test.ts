import { describe, expect, it } from "vitest";
import type { Meta, TicketSummary } from "./api";
import { boardField, changeName, columnsFor, filterTickets, groupTickets, ticketLabel, toggle } from "./board";

const meta = (fields: Meta["fields"]): Meta => ({
  configured: true,
  repos: ["me/board"],
  project: "me/1",
  fields,
  labels: {},
  assignees: {},
  synced_at: null,
  project_url: null,
});

const status = { Status: { type: "SINGLE_SELECT" as const, options: ["Todo", "Done"] } };

const t = (number: number | null, title: string, extra: Partial<TicketSummary> = {}): TicketSummary => ({
  path: `me/board/${number ?? "new"}-${title}.md`,
  repo: "me/board",
  number,
  title,
  state: "open",
  labels: [],
  assignees: [],
  project: {},
  url: null,
  updated: null,
  conflicts: [],
  changes: [],
  ...extra,
});

const tickets = [
  t(2, "Second", { project: { Status: "Done" }, labels: ["bug"] }),
  t(1, "First", { project: { Status: "Todo" } }),
  t(3, "Closed one", { state: "closed" }),
  t(null, "Draft"),
  t(4, "Odd", { project: { Status: "Removed option" } }),
];

describe("board", () => {
  it("groups by Status with an empty column and keeps unknown values", () => {
    const field = boardField(meta(status));
    expect(field).toBe("Status");
    expect(columnsFor(meta(status), field)).toEqual(["Todo", "Done", null]);
    const groups = groupTickets(tickets, meta(status), field);
    expect(groups.get("Todo")!.map((x) => x.number)).toEqual([1]);
    expect(groups.get(null)!.map((x) => x.title)).toEqual(["Closed one", "Draft"]);
    expect(groups.get("Removed option")!.length).toBe(1);
  });

  it("falls back to open/closed without a project", () => {
    expect(boardField(meta({}))).toBeNull();
    expect(columnsFor(meta({}), null)).toEqual(["open", "closed"]);
  });
});

describe("filterTickets", () => {
  it("shows open tickets and drafts by default, sorted by number", () => {
    expect(filterTickets(tickets, {}).map((x) => x.title)).toEqual(["First", "Second", "Odd", "Draft"]);
    expect(filterTickets(tickets, { state: "closed" }).map((x) => x.title)).toEqual(["Closed one", "Draft"]);
  });
  it("searches titles, numbers and labels", () => {
    expect(filterTickets(tickets, { q: "bug" }).map((x) => x.number)).toEqual([2]);
    expect(filterTickets(tickets, { q: "4", state: "all" }).map((x) => x.number)).toEqual([4]);
  });
});

describe("helpers", () => {
  it("labels tickets and changes", () => {
    expect(ticketLabel({ number: 5, repo: "me/board" })).toBe("#5");
    expect(ticketLabel({ number: null, repo: "me/board" }, true)).toBe("me/board · new");
    expect(changeName("project.Status")).toBe("Status");
    expect(toggle(["b"], "a")).toEqual(["a", "b"]);
    expect(toggle(["a", "b"], "a")).toEqual(["b"]);
  });
});
