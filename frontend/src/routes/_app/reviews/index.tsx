import { createFileRoute } from "@tanstack/react-router";
import { PackageList } from "../../../features/reviews/ReviewPage";

export const Route = createFileRoute("/_app/reviews/")({
  component: () => (
    <>
      <div className="h-full md:hidden">
        <PackageList />
      </div>
      <div className="hidden p-8 text-slate-500 md:block">
        <h1 className="mb-2 text-xl font-semibold text-slate-900 dark:text-slate-100">Reviews</h1>
        <p>
          What agents hand over for review. Each package shows its documents: the content on the left to comment on,
          the rendered view on the right to check the layout.
        </p>
      </div>
    </>
  ),
});
