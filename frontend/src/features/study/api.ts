import { queryOptions } from "@tanstack/react-query";
import { api } from "../../lib/api";

export type Exam = { date: string; format: string; topics: string[]; past: boolean };

export type TopicProgress = {
  cards: number;
  secure: number;
  seen: number;
  dueNow: number;
  exam: string | null;
  examInDays: number | null;
  minutesNeeded: number;
  minutesPerDay: number | null;
};

export type Progress = {
  cards: number;
  seen: number;
  secure: number;
  new: number;
  readiness: number;
  dueNow: number;
  nextExam: string | null;
  examInDays: number | null;
  minutesNeeded: number;
  minutesPerDay: number | null;
  weak: { id: string; title: string; wrong: number; partial: number }[];
  topics: Record<string, TopicProgress>;
};

export type BankSummary = {
  topic: string;
  file: string;
  code: string;
  exam: string | null;
  ownExam: boolean;
  examFormat: string;
  cards: number;
};

export type SubjectSummary = {
  key: string;
  name: string;
  exam: string | null;
  examFormat: string;
  language: string;
  cards: number;
  topics: string[];
  exams: Exam[];
  banks: BankSummary[];
  progress: Progress;
};

export type CardStatus = "new" | "wrong" | "partial" | "learning" | "secure";

export type StudyCard = {
  id: string;
  title: string;
  fields: Record<string, string>;
  status: CardStatus;
  box: number;
  seen: number;
  wrong: number;
  partial: number;
  known: number;
  lastAt: string | null;
  lastResult: string | null;
};

export type Bank = Omit<BankSummary, "cards"> & { cards: StudyCard[] };

export type Material = { file: string; title: string; topic: string; sections: string[] };

export type SubjectDetail = Omit<SubjectSummary, "banks"> & {
  code: string;
  subjectFile: string;
  banks: Bank[];
  material: Material[];
};

export const subjectsQuery = queryOptions({
  queryKey: ["study", "subjects"],
  queryFn: () => api<SubjectSummary[]>("/api/study/subjects"),
});

export const subjectQuery = (key: string) =>
  queryOptions({
    queryKey: ["study", "subject", key],
    queryFn: () => api<SubjectDetail>(`/api/study/subjects/${encodeURIComponent(key)}`),
  });

export type Grade = "correct" | "partial" | "wrong" | "known";

export type GradeResult = { corrected: boolean; card: string; previous: string | null; result: Grade; status: CardStatus; box: number };

/** Correct the latest grade of a card, or mark it as known (top box). */
export const gradeCard = (subject: string, cardId: string, result: Grade) =>
  api<GradeResult>(
    `/api/study/${encodeURIComponent(subject)}/cards/${encodeURIComponent(cardId)}/grade`,
    { method: "POST", json: { result } },
  );
