import { createFileRoute } from "@tanstack/react-router";
import { ComingSoon } from "../../components/AppShell";

export const Route = createFileRoute("/_app/notes")({
  component: () => (
    <ComingSoon title="Notes" gate="R2">
      Tasks and info notes in a fast, filterable grid, migrated from the old note editor.
    </ComingSoon>
  ),
});
