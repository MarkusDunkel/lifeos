import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowLeft, CalendarClock, Pencil } from "lucide-react";
import { useRef, useState } from "react";
import { DocEditor, docBtn } from "../knowledge/DocEditor";
import { type Bank, type Material, type StudyCard, subjectQuery, type SubjectDetail } from "./api";
import {
  banksByExam,
  type CardFilter,
  countdown,
  filterCards,
  formatExamDate,
  STATUS_COLOR,
  STATUS_LABEL,
  topicStats,
} from "./format";
import { ReadinessBar } from "./StudyPage";

export type StudyTab = "material" | "questions" | "overview";

const TABS: [StudyTab, string][] = [
  ["material", "Material"],
  ["questions", "Questions"],
  ["overview", "Overview"],
];

export function SubjectPage({
  subject,
  tab,
  onTab,
}: {
  subject: string;
  tab: StudyTab;
  onTab: (tab: StudyTab) => void;
}) {
  const detail = useQuery(subjectQuery(subject));
  const queryClient = useQueryClient();
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["study"] });

  if (detail.isError)
    return (
      <div className="p-8">
        <BackLink />
        <p className="mt-4 text-slate-500">This subject does not exist (anymore).</p>
      </div>
    );
  if (!detail.data) return <p className="p-8 text-slate-400">Loading…</p>;
  const d = detail.data;

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-4 sm:p-8">
      <BackLink />
      <Head d={d} />
      <nav className="flex gap-1 border-b border-slate-200 dark:border-slate-800">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            onClick={() => onTab(key)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${
              tab === key
                ? "border-sky-600 text-sky-700 dark:text-sky-300"
                : "border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
            }`}
          >
            {label}
            {key === "questions" && <span className="ml-1.5 text-xs text-slate-400">{d.cards}</span>}
          </button>
        ))}
      </nav>
      {tab === "material" && <MaterialTab d={d} onSaved={refresh} />}
      {tab === "questions" && <QuestionsTab d={d} onSaved={refresh} />}
      {tab === "overview" && (
        <DocEditor key={d.subjectFile} path={d.subjectFile} title="Subject overview (_Fach.md)" onSaved={refresh} />
      )}
    </div>
  );
}

function BackLink() {
  return (
    <Link to="/study" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800 dark:hover:text-slate-200">
      <ArrowLeft className="size-4" /> All exams
    </Link>
  );
}

function Head({ d }: { d: SubjectDetail }) {
  return (
    <header className="space-y-3">
      <h1 className="text-2xl font-semibold">{d.name}</h1>
      <div className="grid gap-2 sm:grid-cols-2">
        {d.exams.map((e) => {
          const stats = topicStats(d.progress, e.topics);
          return (
            <div
              key={e.date}
              className={`space-y-2 rounded-xl border p-3 text-sm ${e.past ? "border-slate-200 opacity-70 dark:border-slate-800" : "border-sky-200 dark:border-sky-900"}`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <CalendarClock className="size-4 text-slate-500" />
                <span className="font-medium">{formatExamDate(e.date)}</span>
                <span className="text-xs text-slate-500">{countdown(e.date)}</span>
              </div>
              {e.format && <div className="text-slate-600 dark:text-slate-300">{e.format}</div>}
              {d.exams.length > 1 && <div className="text-xs text-slate-500">{e.topics.join(" · ")}</div>}
              <ReadinessBar secure={stats.secure} cards={stats.cards} />
              {!e.past && (
                <div className="flex flex-wrap gap-x-4 text-xs text-slate-500">
                  <span>{stats.dueNow} due now</span>
                  {stats.minutesPerDay !== null && <span>≈ {stats.minutesPerDay} min/day</span>}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </header>
  );
}

// -- material -------------------------------------------------------------------

function MaterialTab({ d, onSaved }: { d: SubjectDetail; onSaved: () => void }) {
  const [selected, setSelected] = useState(0);
  if (d.material.length === 0)
    return <p className="text-sm text-slate-500">No learning text yet. Every note in the subject folder that is not a question bank counts as one.</p>;
  const material = d.material[Math.min(selected, d.material.length - 1)];
  return (
    <div className="space-y-3">
      {d.material.length > 1 && (
        <div className="flex flex-wrap gap-1.5">
          {d.material.map((m, i) => (
            <button
              key={m.file}
              onClick={() => setSelected(i)}
              className={`rounded-full px-3 py-1 text-sm ${m === material ? "bg-sky-600 text-white" : "bg-slate-100 dark:bg-slate-800"}`}
            >
              {m.title}
            </button>
          ))}
        </div>
      )}
      <MaterialFile key={material.file} material={material} onSaved={onSaved} />
    </div>
  );
}

function MaterialFile({ material, onSaved }: { material: Material; onSaved: () => void }) {
  const viewRef = useRef<HTMLDivElement>(null);
  const jump = (section: string) => {
    const headings = viewRef.current?.querySelectorAll("h2") ?? [];
    const target = [...headings].find((h) => h.textContent?.trim() === section);
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  return (
    <div className="grid gap-4 lg:grid-cols-[13rem_1fr]">
      {material.sections.length > 0 && (
        <aside className="lg:sticky lg:top-4 lg:self-start">
          <h3 className="mb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase">Contents</h3>
          <ul className="flex flex-wrap gap-1 text-sm lg:block lg:space-y-0.5">
            {material.sections.map((s) => (
              <li key={s}>
                <button
                  onClick={() => jump(s)}
                  className="rounded px-1.5 py-0.5 text-left text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
                >
                  {s}
                </button>
              </li>
            ))}
          </ul>
        </aside>
      )}
      <div className="min-w-0">
        <DocEditor path={material.file} title={material.title} onSaved={onSaved} viewRef={viewRef} />
      </div>
    </div>
  );
}

// -- questions ------------------------------------------------------------------

const FILTERS: [CardFilter, string][] = [
  ["all", "All"],
  ["shaky", "Shaky"],
  ["new", "New"],
];

function QuestionsTab({ d, onSaved }: { d: SubjectDetail; onSaved: () => void }) {
  const [filter, setFilter] = useState<CardFilter>("all");
  const [editing, setEditing] = useState<string | null>(null);
  const all = d.banks.flatMap((b) => b.cards);

  if (editing !== null)
    return (
      <div className="space-y-3">
        <button onClick={() => setEditing(null)} className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800 dark:hover:text-slate-200">
          <ArrowLeft className="size-4" /> Back to the cards
        </button>
        <DocEditor key={editing} path={editing} startInEdit onSaved={onSaved} />
      </div>
    );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-1.5">
        {FILTERS.map(([key, label]) => (
          <button
            key={key}
            onClick={() => setFilter(key)}
            className={`rounded-full px-3 py-1 text-sm ${filter === key ? "bg-sky-600 text-white" : "bg-slate-100 dark:bg-slate-800"}`}
          >
            {label} <span className="opacity-70">{filterCards(all, key).length}</span>
          </button>
        ))}
        <span className="ml-auto flex flex-wrap gap-3 text-xs text-slate-500">
          {(Object.keys(STATUS_LABEL) as (keyof typeof STATUS_LABEL)[]).map((s) => (
            <span key={s} className="inline-flex items-center gap-1">
              <span className={`size-2 rounded-full ${STATUS_COLOR[s]}`} /> {STATUS_LABEL[s]}
            </span>
          ))}
        </span>
      </div>
      {d.banks.length === 0 && <p className="text-sm text-slate-500">No question bank yet (files named “… - Prüfen.md”).</p>}
      {banksByExam(d.banks).map((group) => (
        <section key={group.exam ?? "none"} className="space-y-4">
          {(d.exams.length > 1 || group.exam === null) && (
            <h2 className="flex items-center gap-2 text-sm font-semibold tracking-wide text-slate-500 uppercase">
              <CalendarClock className="size-4" />
              {group.exam ? `Exam ${formatExamDate(group.exam)} · ${countdown(group.exam)}` : "No exam date"}
            </h2>
          )}
          {group.banks.map((bank) => (
            <BankCards key={bank.file} bank={bank} filter={filter} onEdit={() => setEditing(bank.file)} />
          ))}
        </section>
      ))}
    </div>
  );
}

function BankCards({ bank, filter, onEdit }: { bank: Bank; filter: CardFilter; onEdit: () => void }) {
  const cards = filterCards(bank.cards, filter);
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 dark:border-slate-800">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-800 dark:bg-slate-900">
        <div className="min-w-0 flex-1">
          <span className="font-medium">{bank.topic}</span>
          <span className="ml-2 text-xs text-slate-500">
            {cards.length === bank.cards.length ? `${bank.cards.length} cards` : `${cards.length} of ${bank.cards.length} cards`}
            {bank.ownExam && " · own exam date"}
          </span>
        </div>
        <Link
          to="/knowledge/$"
          params={{ _splat: bank.file }}
          className={`${docBtn} text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800`}
        >
          <span className="hidden sm:inline">Open in</span> Knowledge
        </Link>
        <button onClick={onEdit} className={`${docBtn} hover:bg-slate-100 dark:hover:bg-slate-800`}>
          <Pencil className="size-4" /> Edit
        </button>
      </div>
      {cards.length === 0 ? (
        <p className="p-4 text-sm text-slate-500">No cards match this filter.</p>
      ) : (
        <ul className="divide-y divide-slate-100 dark:divide-slate-800/70">
          {cards.map((c) => (
            <CardRow key={c.id} card={c} />
          ))}
        </ul>
      )}
    </div>
  );
}

function practised(card: StudyCard): string {
  if (!card.lastAt) return "never practised";
  const when = new Date(card.lastAt).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  const tally = [card.wrong && `${card.wrong}× wrong`, card.partial && `${card.partial}× partial`].filter(Boolean);
  return `${card.seen}× practised, last ${when}${tally.length ? ` (${tally.join(", ")})` : ""}`;
}

function CardRow({ card }: { card: StudyCard }) {
  const fields = Object.entries(card.fields);
  return (
    <li className="space-y-1.5 px-3 py-3">
      <div className="flex items-start gap-2">
        <span
          className={`mt-1.5 size-2.5 shrink-0 rounded-full ${STATUS_COLOR[card.status]}`}
          title={`${STATUS_LABEL[card.status]} – ${practised(card)}`}
        />
        <div className="min-w-0 flex-1">
          <div className="font-medium">{card.title}</div>
          <div className="text-xs text-slate-400">
            <span className="font-mono">{card.id}</span> · {STATUS_LABEL[card.status]} · {practised(card)}
          </div>
        </div>
      </div>
      {fields.length > 0 && (
        <dl className="ml-4.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
          {fields.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="text-slate-500">{key}</dt>
              <dd className="min-w-0 text-slate-800 dark:text-slate-200">{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}
