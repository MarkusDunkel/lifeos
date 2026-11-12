import { createFileRoute } from "@tanstack/react-router";
import { ComingSoon } from "../../components/AppShell";

export const Route = createFileRoute("/_app/agents")({
  component: () => (
    <ComingSoon title="Agents" gate="R4">
      Run, schedule and watch Claude agents working on the vault.
    </ComingSoon>
  ),
});
