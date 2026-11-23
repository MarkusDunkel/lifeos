import { createFileRoute } from "@tanstack/react-router";
import { PLAN_TABS, type PlanTab, PlanPage } from "../../features/planning/PlanPage";

type PlanSearch = { tab?: PlanTab; google?: string };

export const Route = createFileRoute("/_app/plan")({
  validateSearch: (search: Record<string, unknown>): PlanSearch => ({
    tab: PLAN_TABS.includes(search.tab as PlanTab) ? (search.tab as PlanTab) : undefined,
    google: typeof search.google === "string" ? search.google : undefined,
  }),
  component: Plan,
});

function Plan() {
  const { tab, google } = Route.useSearch();
  const navigate = Route.useNavigate();
  return <PlanPage tab={tab ?? PLAN_TABS[0]} notice={google} onTab={(t) => navigate({ search: { tab: t } })} />;
}
