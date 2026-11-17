import { createFileRoute } from "@tanstack/react-router";
import { JobsPage } from "../../features/jobsearch/JobsPage";

export const Route = createFileRoute("/_app/jobs")({
  component: JobsPage,
});
