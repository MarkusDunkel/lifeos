import { createFileRoute } from "@tanstack/react-router";
import { CareerPage } from "../../features/career/CareerPage";

export const Route = createFileRoute("/_app/career")({
  component: CareerPage,
});
