import { queryOptions } from "@tanstack/react-query";
import { api } from "../../lib/api";

export type Bucket = "now" | "soon" | "later" | "covered";

export type CareerProfile = {
  slug: string;
  title: string;
  status: "active" | "paused" | "draft" | "dropped";
  weight: number;
  share: number;
  skills: number;
  problems: string[];
};

export type CurriculumItem = {
  key: string;
  name: string;
  importance: number;
  level: number | null;
  priority: number;
  bucket: Bucket;
  sources: { slug: string; title: string; share: "core" | "secondary" | "edge" }[];
};

export type CareerOverview = {
  profilesDir: string;
  overviewFile: string;
  overviewExists: boolean;
  profiles: CareerProfile[];
  items: CurriculumItem[];
  warnings: string[];
};

export const careerQuery = queryOptions({
  queryKey: ["career", "overview"],
  queryFn: () => api<CareerOverview>("/api/career/overview"),
});

export const refreshCurriculum = () =>
  api<{ written: boolean }>("/api/career/curriculum/refresh", { method: "POST" });
