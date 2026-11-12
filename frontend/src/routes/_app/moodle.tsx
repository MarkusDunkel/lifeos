import { createFileRoute } from "@tanstack/react-router";
import { MoodlePage } from "../../features/moodle/MoodlePage";

export const Route = createFileRoute("/_app/moodle")({
  component: MoodlePage,
});
