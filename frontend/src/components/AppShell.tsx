import { useQuery } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";
import { Activity, Bot, BookOpen, Briefcase, CalendarCheck, ClipboardList, Compass, FileCheck, GraduationCap, KanbanSquare, NotebookPen, Settings, StickyNote } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";
import { controlQuery } from "../features/control/api";
import { LEVEL_STYLE } from "../features/control/layout";

const NAV = [
  { to: "/plan", label: "Plan", icon: CalendarCheck },
  { to: "/knowledge", label: "Knowledge", icon: BookOpen },
  { to: "/notes", label: "Notes", icon: StickyNote },
  { to: "/workspaces", label: "Workspaces", icon: ClipboardList },
  { to: "/reviews", label: "Reviews", icon: FileCheck },
  { to: "/tickets", label: "Tickets", icon: KanbanSquare },
  { to: "/study", label: "Study", icon: NotebookPen },
  { to: "/moodle", label: "Moodle", icon: GraduationCap },
  { to: "/jobs", label: "Jobs", icon: Briefcase },
  { to: "/career", label: "Career", icon: Compass },
  { to: "/agents", label: "Agents", icon: Bot },
  { to: "/control", label: "Control", icon: Activity },
  { to: "/settings", label: "Settings", icon: Settings },
] as const;

/** Red or yellow dot on the Control entry while something needs attention. */
function Alert({ to }: { to: string }) {
  const control = useQuery({ ...controlQuery, enabled: true });
  const level = control.data?.level;
  if (to !== "/control" || !level || (level !== "warn" && level !== "fail")) return null;
  return <span className={`absolute top-1 right-3 size-2 rounded-full ${LEVEL_STYLE[level].dot}`} />;
}

export function AppShell({ children }: { children: ReactNode }) {
  const bar = useRef<HTMLElement>(null);
  const path = useRouterState({ select: (s) => s.location.pathname });
  // the bottom bar scrolls sideways on narrow phones: keep the current page's entry in view
  useEffect(() => {
    bar.current?.querySelector<HTMLElement>("[data-status=active]")?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [path]);
  return (
    <div className="flex h-full flex-col md:flex-row">
      {/* Desktop rail */}
      <nav className="hidden w-20 shrink-0 flex-col items-center gap-1 border-r border-slate-200 bg-slate-50 py-4 md:flex dark:border-slate-800 dark:bg-slate-900">
        <img src="/icon.svg" alt="LifeOS" className="mb-4 size-9" />
        {NAV.map(({ to, label, icon: Icon }) => (
          <Link
            key={to}
            to={to}
            className="relative flex w-16 flex-col items-center gap-1 rounded-xl py-2 text-[11px] text-slate-500 hover:bg-slate-200/60 dark:hover:bg-slate-800"
            activeProps={{ className: "!bg-sky-100 !text-sky-800 dark:!bg-sky-900/50 dark:!text-sky-200" }}
          >
            <Icon className="size-5" />
            {label}
            <Alert to={to} />
          </Link>
        ))}
      </nav>

      <main className="min-h-0 min-w-0 flex-1">{children}</main>

      {/* Mobile bottom bar */}
      <nav
        ref={bar}
        className="flex shrink-0 [scrollbar-width:none] overflow-x-auto overscroll-x-contain border-t border-slate-200 bg-slate-50 pb-[env(safe-area-inset-bottom)] md:hidden dark:border-slate-800 dark:bg-slate-900">
        {NAV.map(({ to, label, icon: Icon }) => (
          <Link
            key={to}
            to={to}
            className="relative flex min-w-16 flex-1 shrink-0 flex-col items-center gap-0.5 py-2 text-[10px] whitespace-nowrap text-slate-500"
            activeProps={{ className: "!text-sky-600 dark:!text-sky-400" }}
          >
            <Icon className="size-5" />
            {label}
            <Alert to={to} />
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
