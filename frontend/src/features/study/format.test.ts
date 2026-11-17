import { describe, expect, it } from "vitest";
import type { Bank, Progress, StudyCard, SubjectSummary, TopicProgress } from "./api";
import { banksByExam, countdown, examRows, filterCards, topicStats } from "./format";

const topic = (over: Partial<TopicProgress>): TopicProgress => ({
  cards: 10,
  secure: 2,
  seen: 4,
  dueNow: 3,
  exam: null,
  examInDays: null,
  minutesNeeded: 5,
  minutesPerDay: null,
  ...over,
});

const progress = (topics: Record<string, TopicProgress>): Progress => ({
  cards: 0,
  seen: 0,
  secure: 0,
  new: 0,
  readiness: 0,
  dueNow: 0,
  nextExam: null,
  examInDays: null,
  minutesNeeded: 0,
  minutesPerDay: null,
  weak: [],
  topics,
});

const subject = (key: string, exams: SubjectSummary["exams"], topics: Record<string, TopicProgress>): SubjectSummary => ({
  key,
  name: key,
  exam: null,
  examFormat: "",
  language: "en",
  cards: 0,
  topics: Object.keys(topics),
  exams,
  banks: [],
  progress: progress(topics),
});

describe("examRows", () => {
  it("lists upcoming exams soonest first and past ones latest first, with topic readiness", () => {
    const a = subject(
      "A",
      [
        { date: "2026-12-15T09:00:00+01:00", format: "oral", topics: ["Phrases"], past: false },
        { date: "2026-11-21T12:30:00+01:00", format: "test", topics: ["Vocab"], past: false },
      ],
      { Vocab: topic({ minutesPerDay: 4 }), Phrases: topic({ cards: 5, secure: 5, dueNow: 0 }) },
    );
    const b = subject(
      "B",
      [
        { date: "2026-10-10T08:00:00+02:00", format: "oral", topics: ["X"], past: true },
        { date: "2026-09-01T08:00:00+02:00", format: "oral", topics: ["X"], past: true },
      ],
      { X: topic({}) },
    );
    const { upcoming, past } = examRows([b, a]);
    expect(upcoming.map((r) => r.topics[0])).toEqual(["Vocab", "Phrases"]);
    expect(upcoming[0]).toMatchObject({ subject: "A", cards: 10, secure: 2, dueNow: 3, minutesPerDay: 4 });
    expect(upcoming[1]).toMatchObject({ cards: 5, secure: 5, minutesPerDay: null });
    expect(past.map((r) => r.date.slice(0, 10))).toEqual(["2026-10-10", "2026-09-01"]);
  });

  it("uses all topics for an exam that names none", () => {
    expect(topicStats(progress({ a: topic({}), b: topic({ cards: 1 }) }), []).cards).toBe(11);
  });
});

describe("countdown", () => {
  const now = new Date(2026, 10, 17, 22, 0);
  it("counts calendar days", () => {
    expect(countdown(new Date(2026, 10, 17, 23, 0).toISOString(), now)).toBe("today");
    expect(countdown(new Date(2026, 10, 18, 1, 0).toISOString(), now)).toBe("tomorrow");
    expect(countdown(new Date(2026, 10, 21, 12, 30).toISOString(), now)).toBe("in 4 days");
    expect(countdown(new Date(2026, 9, 10, 8, 0).toISOString(), now)).toBe("38 days ago");
  });
});

const card = (id: string, status: StudyCard["status"]): StudyCard => ({
  id,
  title: id,
  fields: {},
  status,
  box: 0,
  seen: 0,
  wrong: 0,
  partial: 0,
  lastAt: null,
  lastResult: null,
});

describe("filterCards", () => {
  const cards = [card("a", "new"), card("b", "wrong"), card("c", "partial"), card("d", "learning"), card("e", "secure")];
  it("filters shaky and new cards", () => {
    expect(filterCards(cards, "all")).toHaveLength(5);
    expect(filterCards(cards, "shaky").map((c) => c.id)).toEqual(["b", "c", "d"]);
    expect(filterCards(cards, "new").map((c) => c.id)).toEqual(["a"]);
  });
});

describe("banksByExam", () => {
  const bank = (topic: string, exam: string | null): Bank => ({
    topic,
    file: `${topic}.md`,
    code: "",
    exam,
    ownExam: false,
    examFormat: "",
    cards: [],
  });
  it("groups banks by exam date, soonest first, undated last", () => {
    const groups = banksByExam([
      bank("late", "2026-12-15T09:00:00+01:00"),
      bank("none", null),
      bank("soon1", "2026-11-21T12:30:00+01:00"),
      bank("soon2", "2026-11-21T12:30:00+01:00"),
    ]);
    expect(groups.map((g) => g.banks.map((b) => b.topic))).toEqual([["soon1", "soon2"], ["late"], ["none"]]);
  });
});
