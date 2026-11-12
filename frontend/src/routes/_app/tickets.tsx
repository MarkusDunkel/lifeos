import { createFileRoute } from "@tanstack/react-router";
import { ComingSoon } from "../../components/AppShell";

export const Route = createFileRoute("/_app/tickets")({
  component: () => (
    <ComingSoon title="Tickets" gate="R5">
      GitHub Issues and Projects mirrored into the vault, with editor, diff and write-back.
    </ComingSoon>
  ),
});
