import { Link } from "@tanstack/react-router";
import { Bot, BookOpen, Briefcase, ClipboardList, FileCheck, GraduationCap, KanbanSquare, NotebookPen, Settings, StickyNote } from "lucide-react";
import type { ReactNode } from "react";

const NAV = [
  { to: "/knowledge", label: "Knowledge", icon: BookOpen },
  { to: "/notes", label: "Notes", icon: StickyNote },
  { to: "/workspaces", label: "Workspaces", icon: ClipboardList },
  { to: "/reviews", label: "Reviews", icon: FileCheck },
  { to: "/tickets", label: "Tickets", icon: KanbanSquare },
  { to: "/study", label: "Study", icon: NotebookPen },
  { to: "/moodle", label: "Moodle", icon: GraduationCap },
  { to: "/jobs", label: "Jobs", icon: Briefcase },
  { to: "/agents", label: "Agents", icon: Bot },
  { to: "/settings", label: "Settings", icon: Settings },
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full flex-col md:flex-row">
      {/* Desktop rail */}
      <nav className="hidden w-20 shrink-0 flex-col items-center gap-1 border-r border-slate-200 bg-slate-50 py-4 md:flex dark:border-slate-800 dark:bg-slate-900">
        <img src="/icon.svg" alt="LifeOS" className="mb-4 size-9" />
        {NAV.map(({ to, label, icon: Icon }) => (
          <Link
            key={to}
            to={to}
            className="flex w-16 flex-col items-center gap-1 rounded-xl py-2 text-[11px] text-slate-500 hover:bg-slate-200/60 dark:hover:bg-slate-800"
            activeProps={{ className: "!bg-sky-100 !text-sky-800 dark:!bg-sky-900/50 dark:!text-sky-200" }}
          >
            <Icon className="size-5" />
            {label}
          </Link>
        ))}
      </nav>

      <main className="min-h-0 min-w-0 flex-1">{children}</main>

      {/* Mobile bottom bar */}
      <nav className="flex shrink-0 justify-around border-t border-slate-200 bg-slate-50 pb-[env(safe-area-inset-bottom)] md:hidden dark:border-slate-800 dark:bg-slate-900">
        {NAV.map(({ to, label, icon: Icon }) => (
          <Link
            key={to}
            to={to}
            className="flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px] text-slate-500"
            activeProps={{ className: "!text-sky-600 dark:!text-sky-400" }}
          >
            <Icon className="size-5" />
            {label}
          </Link>
        ))}
      </nav>
    </div>
  );
}

export function ComingSoon({ title, gate, children }: { title: string; gate: string; children: ReactNode }) {
  return (
    <div className="mx-auto max-w-xl p-8">
      <h1 className="mb-2 text-2xl font-semibold">{title}</h1>
      <p className="mb-4 inline-block rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-500 dark:bg-slate-800">
        Coming in review gate {gate}
      </p>
      <div className="text-slate-600 dark:text-slate-400">{children}</div>
    </div>
  );
}
