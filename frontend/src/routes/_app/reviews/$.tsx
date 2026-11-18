import { createFileRoute } from "@tanstack/react-router";
import { ReviewPage } from "../../../features/reviews/ReviewPage";

export const Route = createFileRoute("/_app/reviews/$")({
  component: Review,
});

function Review() {
  const { _splat } = Route.useParams();
  return <ReviewPage key={_splat} path={_splat ?? ""} />;
}
