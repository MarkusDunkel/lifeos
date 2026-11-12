import { createFileRoute } from "@tanstack/react-router";
import { useCallback } from "react";
import { NotesPage, type NotesSearch } from "../../features/notes/NotesPage";

const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);

function validateSearch(search: Record<string, unknown>): NotesSearch {
  const f =
    search.f && typeof search.f === "object"
      ? Object.fromEntries(Object.entries(search.f).filter(([, v]) => typeof v === "string" && v))
      : {};
  return {
    q: str(search.q),
    sort: str(search.sort),
    desc: search.desc === true || search.desc === "true" ? true : undefined,
    f: Object.keys(f).length ? (f as Record<string, string>) : undefined,
    note: str(search.note),
  };
}

export const Route = createFileRoute("/_app/notes")({
  validateSearch,
  component: Notes,
});

function Notes() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const onChange = useCallback(
    (next: NotesSearch, replace = false) => navigate({ search: validateSearch(next), replace }),
    [navigate],
  );
  return <NotesPage search={search} onChange={onChange} />;
}
