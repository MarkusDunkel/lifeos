import { createFileRoute, redirect } from "@tanstack/react-router";
import { PRINT_PARTS, type PrintPart } from "../../../features/study/format";
import { PrintSubject } from "../../../features/study/PrintSubject";
import { sessionQuery } from "../../../lib/session";

// Outside the app shell: no navigation rail and no inner scroll container, so the
// print dialog gets the whole document.
export const Route = createFileRoute("/print/study/$subject")({
  validateSearch: (search: Record<string, unknown>): { part?: PrintPart } => ({
    part: PRINT_PARTS.includes(search.part as PrintPart) && search.part !== "all" ? (search.part as PrintPart) : undefined,
  }),
  beforeLoad: async ({ context, location }) => {
    const session = await context.queryClient.ensureQueryData(sessionQuery);
    if (!session.authenticated) {
      throw redirect({ to: "/login", search: { redirect: location.href } });
    }
  },
  component: PrintStudySubject,
});

function PrintStudySubject() {
  const { subject } = Route.useParams();
  const { part } = Route.useSearch();
  return <PrintSubject key={part} subject={subject} part={part ?? "all"} />;
}
