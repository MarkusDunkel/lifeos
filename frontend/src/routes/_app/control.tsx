import { createFileRoute } from "@tanstack/react-router";
import { useCallback } from "react";
import { ControlPage } from "../../features/control/ControlPage";

/** ``?sel=``: the block whose details are open (``p:<process>``, ``c:<check>`` or a node id). */
export const Route = createFileRoute("/_app/control")({
  validateSearch: (search: Record<string, unknown>): { sel?: string } => ({
    sel: typeof search.sel === "string" && search.sel ? search.sel : undefined,
  }),
  component: Control,
});

function Control() {
  const { sel } = Route.useSearch();
  const navigate = Route.useNavigate();
  const onSelect = useCallback(
    // opening the panel is a step back can undo; moving on to another block replaces it
    (id: string | null) => navigate({ search: id ? { sel: id } : {}, replace: Boolean(sel && id) }),
    [navigate, sel],
  );
  return <ControlPage selected={sel ?? null} onSelect={onSelect} />;
}
