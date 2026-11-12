import { queryOptions } from "@tanstack/react-query";
import { api } from "./api";

export type Session = { authenticated: boolean; auth_disabled: boolean };

export const sessionQuery = queryOptions({
  queryKey: ["session"],
  queryFn: () => api<Session>("/api/auth/session"),
  staleTime: 60_000,
});

export const login = (password: string) =>
  api<Session>("/api/auth/login", { method: "POST", json: { password } });

export const logout = () => api<Session>("/api/auth/logout", { method: "POST" });
