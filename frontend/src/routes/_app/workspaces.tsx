import { createFileRoute } from "@tanstack/react-router";
import { ComingSoon } from "../../components/AppShell";

export const Route = createFileRoute("/_app/workspaces")({
  component: () => (
    <ComingSoon title="Workspaces" gate="R3">
      Read AI-generated reports, highlight passages and leave feedback rounds (md4h-compatible).
    </ComingSoon>
  ),
});
