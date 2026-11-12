import { createFileRoute } from "@tanstack/react-router";
import { BookOpen } from "lucide-react";
import { KnowledgeSidebar } from "../../../features/knowledge/Sidebar";

export const Route = createFileRoute("/_app/knowledge/")({
  component: KnowledgeHome,
});

function KnowledgeHome() {
  return (
    <>
      <div className="h-full md:hidden">
        <KnowledgeSidebar />
      </div>
      <div className="hidden h-full flex-col items-center justify-center gap-3 text-slate-400 md:flex">
        <BookOpen className="size-10" />
        <p>Pick a note on the left, or search across the knowledge hub.</p>
      </div>
    </>
  );
}
