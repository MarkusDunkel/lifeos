import { createFileRoute, Outlet } from "@tanstack/react-router";
import { ReportList } from "../../features/workspaces/ReportList";

export const Route = createFileRoute("/_app/workspaces")({
  component: WorkspacesLayout,
});

function WorkspacesLayout() {
  return (
    <div className="flex h-full min-h-0">
      {/* On desktop the list is always visible; on phones the index page shows it instead. */}
      <aside className="hidden w-72 shrink-0 border-r border-slate-200 md:block dark:border-slate-800">
        <ReportList />
      </aside>
      <section className="min-h-0 min-w-0 flex-1">
        <Outlet />
      </section>
    </div>
  );
}
