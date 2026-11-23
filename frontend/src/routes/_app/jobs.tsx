import { createFileRoute } from "@tanstack/react-router";
import { type JobsView, JobsPage } from "../../features/jobsearch/JobsPage";

export const Route = createFileRoute("/_app/jobs")({
  validateSearch: (search: Record<string, unknown>): { view?: JobsView } => ({
    view: search.view === "dashboard" ? "dashboard" : undefined,
  }),
  component: Jobs,
});

function Jobs() {
  const { view } = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <JobsPage
      view={view ?? "postings"}
      onView={(v) => navigate({ search: { view: v === "dashboard" ? v : undefined } })}
    />
  );
}
