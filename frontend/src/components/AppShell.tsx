import { useQuery } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";
import {
  Activity,
  BookOpen,
  Bot,
  Briefcase,
  CalendarCheck,
  ClipboardList,
  Compass,
  FileCheck,
  GraduationCap,
  KanbanSquare,
  type LucideIcon,
  NotebookPen,
  Settings,
  StickyNote,
} from "lucide-react";
import { Fragment, type ReactNode, useEffect, useRef } from "react";
import { controlQuery, type Level } from "../features/control/api";
import { LEVEL_STYLE } from "../features/control/layout";

type Entry = { to: string; label: string; icon: LucideIcon; hint: string };

/** The pages in their usual order; a thin rule separates the blocks on the desktop rail. */
const NAV: Entry[][] = [
  [{ to: "/plan", label: "Plan", icon: CalendarCheck, hint: "Day plan, goals and journal" }],
  [
    { to: "/knowledge", label: "Knowledge", icon: BookOpen, hint: "Notes and mirrored material" },
    { to: "/notes", label: "Notes", icon: StickyNote, hint: "Structured notes" },
    { to: "/workspaces", label: "Workspaces", icon: ClipboardList, hint: "Reports to give feedback on" },
    { to: "/reviews", label: "Reviews", icon: FileCheck, hint: "Documents waiting for your review" },
    { to: "/tickets", label: "Tickets", icon: KanbanSquare, hint: "GitHub tickets in the vault" },
  ],
  [
    { to: "/study", label: "Study", icon: NotebookPen, hint: "Exams and question banks" },
    { to: "/moodle", label: "Moodle", icon: GraduationCap, hint: "Course material mirror" },
  ],
  [
    { to: "/jobs", label: "Jobs", icon: Briefcase, hint: "Job postings and applications" },
    { to: "/career", label: "Career", icon: Compass, hint: "Job profiles and curriculum" },
  ],
  [
    { to: "/agents", label: "Agents", icon: Bot, hint: "The AI agents and what they use" },
    { to: "/control", label: "Control", icon: Activity, hint: "Every process and its light" },
    { to: "/settings", label: "Settings", icon: Settings, hint: "Version, sign out, connections" },
  ],
];

/** Id of the element that scrolls a page; the router resets and restores its position. */
export const MAIN_ID = "main";

/** Red or yellow dot on the Control entry while something needs attention. */
function Alert({ level }: { level: Level | undefined }) {
  if (level !== "warn" && level !== "fail") return null;
  return (
    <span
      aria-label={level === "fail" ? "a problem needs attention" : "a warning"}
      className={`absolute top-1 right-3 size-2 rounded-full ring-2 ring-slate-50 dark:ring-slate-900 ${LEVEL_STYLE[level].dot}`}
    />
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const bar = useRef<HTMLElement>(null);
  // read once for both navigation bars
  const level = useQuery(controlQuery).data?.level;
  const alertOn = (to: string) => (to === "/control" ? level : undefined);
  const path = useRouterState({ select: (s) => s.location.pathname });
  // the bottom bar scrolls sideways on narrow phones: keep the current page's entry in view
  useEffect(() => {
    bar.current?.querySelector<HTMLElement>("[data-status=active]")?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [path]);
  return (
    <div className="flex h-full flex-col md:flex-row">
      {/* Desktop rail */}
      <nav
        aria-label="Pages"
        className="hidden w-20 shrink-0 flex-col items-center gap-0.5 overflow-y-auto border-r border-slate-200 bg-slate-50 py-3 [scrollbar-width:none] md:flex dark:border-slate-800 dark:bg-slate-900"
      >
        <img src="/icon.svg" alt="LifeOS" className="mb-2 size-9 shrink-0" />
        {NAV.map((block, i) => (
          <Fragment key={block[0].to}>
            {i > 0 && <span aria-hidden className="my-1 h-px w-8 shrink-0 bg-slate-200 dark:bg-slate-800" />}
            {block.map(({ to, label, icon: Icon, hint }) => (
              <Link
                key={to}
                to={to}
                title={hint}
                className="relative flex w-16 shrink-0 flex-col items-center gap-0.5 rounded-xl py-1.5 text-[11px] text-slate-500 transition-colors hover:bg-slate-200/60 hover:text-slate-800 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
                activeProps={{
                  className:
                    "!bg-accent-100 !text-accent-800 font-medium dark:!bg-accent-900/50 dark:!text-accent-200 before:absolute before:inset-y-2 before:-left-2 before:w-1 before:rounded-r-full before:bg-accent-600 dark:before:bg-accent-400",
                }}
              >
                <Icon className="size-5" />
                {label}
                <Alert level={alertOn(to)} />
              </Link>
            ))}
          </Fragment>
        ))}
      </nav>

      {/* The page scrolls in here, so the navigation stays in place on long pages. */}
      <main id={MAIN_ID} data-scroll-restoration-id={MAIN_ID} className="min-h-0 min-w-0 flex-1 overflow-y-auto">
        {children}
      </main>

      {/* Mobile bottom bar */}
      <nav
        ref={bar}
        aria-label="Pages"
        className="flex shrink-0 [scrollbar-width:none] overflow-x-auto overscroll-x-contain border-t border-slate-200 bg-slate-50 pb-[env(safe-area-inset-bottom)] md:hidden dark:border-slate-800 dark:bg-slate-900"
      >
        {NAV.flat().map(({ to, label, icon: Icon }) => (
          <Link
            key={to}
            to={to}
            className="relative flex min-w-16 flex-1 shrink-0 flex-col items-center gap-0.5 py-2 text-[10px] whitespace-nowrap text-slate-500 dark:text-slate-400"
            activeProps={{
              className:
                "!text-accent-600 font-medium dark:!text-accent-400 before:absolute before:inset-x-4 before:top-0 before:h-0.5 before:rounded-b-full before:bg-accent-600 dark:before:bg-accent-400",
            }}
          >
            <Icon className="size-5" />
            {label}
            <Alert level={alertOn(to)} />
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
