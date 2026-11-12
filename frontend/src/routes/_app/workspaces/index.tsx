import { createFileRoute } from "@tanstack/react-router";
import { ReportList } from "../../../features/workspaces/ReportList";

export const Route = createFileRoute("/_app/workspaces/")({
  component: () => (
    <>
      <div className="h-full md:hidden">
        <ReportList />
      </div>
      <div className="hidden p-8 text-slate-500 md:block">
        <h1 className="mb-2 text-xl font-semibold text-slate-900 dark:text-slate-100">Workspaces</h1>
        <p>
          Short-lived reports written by agents. Open one to read it and leave a feedback round; finishing a round
          seals it and copies a prompt that hands the feedback to an agent.
        </p>
      </div>
    </>
  ),
});
