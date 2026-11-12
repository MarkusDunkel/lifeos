import { createFileRoute } from "@tanstack/react-router";
import { DocumentPage } from "../../../features/knowledge/DocumentPage";

export const Route = createFileRoute("/_app/knowledge/$")({
  validateSearch: (search: Record<string, unknown>): { edit?: boolean } => ({
    edit: search.edit === true || search.edit === "true" ? true : undefined,
  }),
  component: KnowledgeDocument,
});

function KnowledgeDocument() {
  const { _splat } = Route.useParams();
  const { edit } = Route.useSearch();
  return <DocumentPage path={_splat ?? ""} edit={edit ?? false} />;
}
