import { createFileRoute } from "@tanstack/react-router";
import { ControlPage } from "../../features/control/ControlPage";

export const Route = createFileRoute("/_app/control")({
  component: ControlPage,
});
