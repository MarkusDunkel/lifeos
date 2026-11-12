import type { MirrorReport } from "../mirrors/mirror";
import { queryOptions } from "@tanstack/react-query";
import { api } from "../../lib/api";

export type SyncReport = MirrorReport;

export type PresentationsStatus = {
  configured: boolean;
  missing: string[];
  connected: boolean;
  folder: string | null;
  target: string;
  presentations: number;
  account: string | null;
  last: SyncReport | null;
};

export type DeviceCode = { user_code: string; verification_uri: string; interval: number; expires_in: number };
export type PollResult = { status: "pending" | "connected" | "expired" | "denied" };

export const presentationsStatusQuery = queryOptions({
  queryKey: ["presentations", "status"],
  queryFn: () => api<PresentationsStatus>("/api/presentations/status"),
});

const post = <T>(path: string) => api<T>(path, { method: "POST" });

export const startConnect = () => post<DeviceCode>("/api/presentations/connect");
export const pollConnect = () => post<PollResult>("/api/presentations/connect/poll");
export const disconnect = () => post("/api/presentations/disconnect");
export const syncPresentations = () => post<SyncReport>("/api/presentations/sync");
