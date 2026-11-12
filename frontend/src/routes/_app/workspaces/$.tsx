import { createFileRoute } from "@tanstack/react-router";
import { ReportPage } from "../../../features/workspaces/ReportPage";

export const Route = createFileRoute("/_app/workspaces/$")({
  component: WorkspaceReport,
});

function WorkspaceReport() {
  const { _splat } = Route.useParams();
  return <ReportPage key={_splat} path={_splat ?? ""} />;
}
