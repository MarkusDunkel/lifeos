import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { LogOut } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { logout, sessionQuery } from "../../lib/session";

export const Route = createFileRoute("/_app/settings")({
  component: SettingsPage,
});

function SettingsPage() {
  const session = useQuery(sessionQuery);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [version, setVersion] = useState("");

  useEffect(() => {
    api<{ version: string }>("/api/health").then((h) => setVersion(h.version), () => {});
  }, []);

  const signOut = async () => {
    await logout();
    queryClient.clear();
    navigate({ to: "/login" });
  };

  return (
    <div className="mx-auto max-w-xl space-y-6 p-8">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <p className="text-sm text-slate-500">LifeOS {version}</p>
      {session.data?.auth_disabled ? (
        <p className="text-sm text-amber-600">Login is disabled (LIFEOS_AUTH_DISABLED). Only use this locally.</p>
      ) : (
        <button
          onClick={signOut}
          className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
        >
          <LogOut className="size-4" /> Sign out
        </button>
      )}
    </div>
  );
}
