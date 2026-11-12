import { createFileRoute } from "@tanstack/react-router";
import { useCallback } from "react";
import type { TicketView } from "../../features/tickets/board";
import { TicketsPage } from "../../features/tickets/TicketsPage";

const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);

function validateSearch(search: Record<string, unknown>): TicketView {
  const state = str(search.state);
  return {
    view: search.view === "list" ? "list" : undefined,
    q: str(search.q),
    repo: str(search.repo),
    state: state === "closed" || state === "all" ? state : undefined,
    ticket: str(search.ticket),
    changes: search.changes === true || search.changes === "true" ? true : undefined,
  };
}

export const Route = createFileRoute("/_app/tickets")({
  validateSearch,
  component: Tickets,
});

function Tickets() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const onChange = useCallback(
    (next: TicketView, replace = false) => navigate({ search: validateSearch(next), replace }),
    [navigate],
  );
  return <TicketsPage search={search} onChange={onChange} />;
}
