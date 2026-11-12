import { queryOptions } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { MirrorReport } from "../mirrors/mirror";

export type MoodleReport = MirrorReport & { skipped: string[]; courses: number };

export type MoodleStatus = {
  configured: boolean;
  missing: string[];
  connected: boolean;
  url: string | null;
  site?: string | null;
  user?: string | null;
  folder: string;
  files: number;
  selected: number[] | null;
  last: MoodleReport | null;
};

export type Course = {
  id: number;
  name: string;
  shortname: string;
  url: string;
  in_progress: boolean;
  selected: boolean;
};

export const moodleStatusQuery = queryOptions({
  queryKey: ["moodle", "status"],
  queryFn: () => api<MoodleStatus>("/api/moodle/status"),
});

export const coursesQuery = queryOptions({
  queryKey: ["moodle", "courses"],
  queryFn: () => api<Course[]>("/api/moodle/courses"),
});

export const connectMoodle = (body: { username?: string; password?: string; token?: string }) =>
  api("/api/moodle/connect", { method: "POST", json: body });

export const disconnectMoodle = () => api("/api/moodle/disconnect", { method: "POST" });

export const selectCourses = (ids: number[]) => api("/api/moodle/courses", { method: "PUT", json: { ids } });

export const syncMoodle = () => api<MoodleReport>("/api/moodle/sync", { method: "POST" });
