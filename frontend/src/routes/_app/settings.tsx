import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { LogOut, Settings } from "lucide-react";
import { useEffect, useState } from "react";
import { Button, Notice, Page, PageHeader } from "../../components/ui";
import { PresentationsSettings } from "../../features/presentations/PresentationsSettings";
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
    <Page width="sm">
      <PageHeader
        icon={Settings}
        title="Settings"
        subtitle={`LifeOS ${version}`}
        actions={
          !session.data?.auth_disabled && (
            <Button icon={LogOut} onClick={signOut}>
              Sign out
            </Button>
          )
        }
      />
      {session.data?.auth_disabled && (
        <Notice tone="warn">Login is disabled (LIFEOS_AUTH_DISABLED). Only use this locally.</Notice>
      )}
      <PresentationsSettings />
    </Page>
  );
}
