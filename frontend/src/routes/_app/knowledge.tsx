import { createFileRoute, Outlet } from "@tanstack/react-router";
import { KnowledgeSidebar } from "../../features/knowledge/Sidebar";

export const Route = createFileRoute("/_app/knowledge")({
  component: KnowledgeLayout,
});

function KnowledgeLayout() {
  return (
    <div className="flex h-full min-h-0">
      {/* On desktop the tree is always visible; on phones the index page shows it instead. */}
      <aside className="hidden w-80 shrink-0 border-r border-slate-200 md:block dark:border-slate-800">
        <KnowledgeSidebar />
      </aside>
      <section className="min-h-0 min-w-0 flex-1">
        <Outlet />
      </section>
    </div>
  );
}
