import { describe, expect, it } from "vitest";
import { ago, formatEmployment, formatSalary } from "./format";

describe("formatSalary", () => {
  it("shows the minimum with its unit", () => {
    expect(formatSalary({ amount: 54000, unit: "YEAR", currency: "EUR" })).toBe("from €54,000 / year");
    expect(formatSalary({ amount: 3500, unit: "MONTH", currency: "EUR" })).toBe("from €3,500 / month");
  });

  it("is empty without a salary", () => {
    expect(formatSalary(null)).toBe("");
  });
});

describe("ago", () => {
  const now = new Date(2026, 10, 17, 15, 0);

  it("names recent days", () => {
    expect(ago(new Date(2026, 10, 17, 8, 0).toISOString(), now)).toBe("today");
    expect(ago(new Date(2026, 10, 16, 23, 0).toISOString(), now)).toBe("yesterday");
    expect(ago(new Date(2026, 10, 14, 12, 0).toISOString(), now)).toBe("3 days ago");
  });

  it("is empty without a date", () => {
    expect(ago(null, now)).toBe("");
  });
});

describe("formatEmployment", () => {
  it("translates schema.org values", () => {
    expect(formatEmployment(["FULL_TIME", "PART_TIME"])).toBe("full-time, part-time");
  });
});
