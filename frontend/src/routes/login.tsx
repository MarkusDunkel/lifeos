import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { login } from "../lib/session";

export const Route = createFileRoute("/login")({
  validateSearch: (search: Record<string, unknown>): { redirect?: string } => ({
    redirect: typeof search.redirect === "string" ? search.redirect : undefined,
  }),
  component: LoginPage,
});

function LoginPage() {
  const { redirect } = Route.useSearch();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session = await login(password);
      queryClient.setQueryData(["session"], session);
      const target = redirect && redirect.startsWith("/") && !redirect.startsWith("//") ? redirect : "/";
      await navigate({ href: target });
    } catch {
      setError("Wrong password.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-full items-center justify-center p-6">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4">
        <div className="flex items-center gap-3">
          <img src="/icon.svg" alt="" className="size-10" />
          <h1 className="text-2xl font-semibold">LifeOS</h1>
        </div>
        <input
          type="password"
          autoFocus
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 outline-none focus:border-sky-500 dark:border-slate-700 dark:bg-slate-900"
        />
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button
          disabled={busy || !password}
          className="w-full rounded-lg bg-sky-600 py-2.5 font-medium text-white hover:bg-sky-700 disabled:opacity-50"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
