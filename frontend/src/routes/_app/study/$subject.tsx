import { createFileRoute } from "@tanstack/react-router";
import { SubjectPage, type StudyTab } from "../../../features/study/SubjectPage";

const TABS: StudyTab[] = ["material", "questions", "overview"];

export const Route = createFileRoute("/_app/study/$subject")({
  validateSearch: (search: Record<string, unknown>): { tab?: StudyTab } => ({
    tab: TABS.includes(search.tab as StudyTab) && search.tab !== "material" ? (search.tab as StudyTab) : undefined,
  }),
  component: StudySubject,
});

function StudySubject() {
  const { subject } = Route.useParams();
  const { tab } = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <SubjectPage
      key={subject}
      subject={subject}
      tab={tab ?? "material"}
      onTab={(next) => navigate({ search: { tab: next === "material" ? undefined : next }, replace: true })}
    />
  );
}
