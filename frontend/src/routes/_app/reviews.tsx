import { createFileRoute, Outlet } from "@tanstack/react-router";
import { PackageList } from "../../features/reviews/ReviewPage";

export const Route = createFileRoute("/_app/reviews")({
  component: ReviewsLayout,
});

function ReviewsLayout() {
  return (
    <div className="flex h-full min-h-0">
      {/* On desktop the list is always visible; on phones the index page shows it instead. */}
      <aside className="hidden w-64 shrink-0 border-r border-slate-200 md:block dark:border-slate-800">
        <PackageList />
      </aside>
      <section className="min-h-0 min-w-0 flex-1">
        <Outlet />
      </section>
    </div>
  );
}
