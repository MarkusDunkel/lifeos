import { createFileRoute, redirect } from "@tanstack/react-router";
import { PrintSubject } from "../../../features/study/PrintSubject";
import { sessionQuery } from "../../../lib/session";

// Outside the app shell: no navigation rail and no inner scroll container, so the
// print dialog gets the whole document.
export const Route = createFileRoute("/print/study/$subject")({
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
  return <PrintSubject subject={subject} />;
}
