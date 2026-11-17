import { createFileRoute } from "@tanstack/react-router";
import { StudyPage } from "../../../features/study/StudyPage";

export const Route = createFileRoute("/_app/study/")({
  component: StudyPage,
});
