import { describe, expect, it } from "vitest";
import type { Meta, TicketSummary } from "./api";
import { changeName, filterTickets, NO_PROJECT, projectStatuses, statusField, ticketLabel, toggle } from "./list";

const select = (options: string[]) => ({ type: "SINGLE_SELECT" as const, options });

const meta: Meta = {
  configured: true,
  repos: ["me/board"],
  primary: "Tasks",
  projects: {
    Tasks: { ref: "me/1", url: "u1", fields: { Status: select(["Todo", "Done"]), Due: { type: "DATE", options: [] } } },
    Apps: { ref: "me/2", url: "u2", fields: { Status: select(["New", "Active"]) } },
  },
  labels: {},
  assignees: {},
  synced_at: null,
};

const t = (number: number | null, title: string, extra: Partial<TicketSummary> = {}): TicketSummary => ({
  path: `me/board/${number ?? "new"}-${title}.md`,
  repo: "me/board",
  number,
  title,
  state: "open",
  labels: [],
  assignees: [],
  projects: {},
  url: null,
  updated: null,
  conflicts: [],
  changes: [],
  ...extra,
});

const tickets = [
  t(2, "Second", { projects: { Tasks: { Status: "Done" } }, labels: ["bug"] }),
  t(1, "First", { projects: { Tasks: { Status: "Todo" } } }),
  t(3, "Closed one", { state: "closed" }),
  t(null, "Draft"),
  t(5, "Job at X", { projects: { Apps: { Status: "New" } }, labels: ["application"] }),
  t(6, "Both", { projects: { Tasks: { Status: "Todo" }, Apps: {} } }),
];

describe("filterTickets", () => {
  it("shows open tickets and drafts by default, sorted by number", () => {
    expect(filterTickets(tickets, {}).map((x) => x.title)).toEqual(["First", "Second", "Job at X", "Both", "Draft"]);
    expect(filterTickets(tickets, { state: "closed" }).map((x) => x.title)).toEqual(["Closed one", "Draft"]);
  });

  it("filters by project, its status and tickets on no project", () => {
    expect(filterTickets(tickets, { project: "Apps" }, meta).map((x) => x.number)).toEqual([5, 6]);
    expect(filterTickets(tickets, { project: "Tasks", status: "Todo" }, meta).map((x) => x.number)).toEqual([1, 6]);
    expect(filterTickets(tickets, { project: NO_PROJECT, state: "all" }, meta).map((x) => x.title)).toEqual(["Closed one", "Draft"]);
  });

  it("filters by label and searches titles, numbers, labels and project values", () => {
    expect(filterTickets(tickets, { label: "bug" }).map((x) => x.number)).toEqual([2]);
    expect(filterTickets(tickets, { q: "5", state: "all" }).map((x) => x.number)).toEqual([5]);
    expect(filterTickets(tickets, { q: "active apps" }).map((x) => x.number)).toEqual([]);
    expect(filterTickets(tickets, { q: "apps new" }).map((x) => x.number)).toEqual([5]);
  });
});

describe("helpers", () => {
  it("finds the status field of a project", () => {
    expect(statusField(meta, "Tasks")).toBe("Status");
    expect(statusField(meta, "Unknown")).toBeNull();
    expect(projectStatuses(tickets[5], meta)).toEqual([
      { project: "Apps", status: null },
      { project: "Tasks", status: "Todo" },
    ]);
  });

  it("labels tickets and changes", () => {
    expect(ticketLabel({ number: 5, repo: "me/board" })).toBe("#5");
    expect(ticketLabel({ number: null, repo: "me/board" }, true)).toBe("me/board · new");
    expect(changeName("project:Tasks:Status")).toBe("Tasks · Status");
    expect(changeName("project:Apps")).toBe("Apps (project)");
    expect(changeName("new")).toBe("new ticket");
    expect(toggle(["b"], "a")).toEqual(["a", "b"]);
    expect(toggle(["a", "b"], "a")).toEqual(["b"]);
  });
});
