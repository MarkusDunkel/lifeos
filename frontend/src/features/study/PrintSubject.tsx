import { useQueries, useQuery } from "@tanstack/react-query";
import { FileDown, X } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { docQuery, treeQuery } from "../knowledge/api";
import { flattenFiles } from "../knowledge/links";
import { MarkdownView } from "../knowledge/MarkdownView";
import { subjectQuery } from "./api";
import { formatExamDate, printDocs } from "./format";

/**
 * The whole subject on one page for the browser's "Save as PDF": overview, every
 * learning text and every question card with its fields. Opens the print dialog once
 * everything has loaded; always light, whatever the system theme.
 */
export function PrintSubject({ subject }: { subject: string }) {
  const detail = useQuery(subjectQuery(subject));
  const tree = useQuery(treeQuery);
  const files = useMemo(() => (tree.data ? flattenFiles(tree.data) : []), [tree.data]);
  const docs = useMemo(() => (detail.data ? printDocs(detail.data) : []), [detail.data]);
  const contents = useQueries({ queries: docs.map((doc) => ({ ...docQuery(doc.path), retry: false })) });
  const ready = detail.data !== undefined && tree.data !== undefined && contents.every((c) => !c.isPending);
  const printed = useRef(false);

  useEffect(() => {
    document.documentElement.classList.add("force-light");
    return () => document.documentElement.classList.remove("force-light");
  }, []);

  useEffect(() => {
    if (!ready || !detail.data || printed.current) return;
    printed.current = true;
    document.title = `${detail.data.name} – Study`;
    // let images and layout settle before the dialog freezes the page
    const timer = window.setTimeout(() => window.print(), 400);
    return () => window.clearTimeout(timer);
  }, [ready, detail.data]);

  if (detail.isError) return <p className="p-8 text-slate-500">This subject does not exist (anymore).</p>;
  if (!detail.data) return <p className="p-8 text-slate-400">Loading…</p>;
  const d = detail.data;
  const banks = d.banks.filter((b) => b.cards.length > 0);
  const cardCount = banks.reduce((n, b) => n + b.cards.length, 0);

  return (
    <div className="print-compact mx-auto max-w-3xl bg-white p-6 text-slate-900 print:max-w-none print:p-0">
      <div className="mb-8 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm print:hidden">
        <span className="flex-1 text-slate-600">{ready ? "Ready – choose “Save as PDF” in the print dialog." : "Loading all files…"}</span>
        <button
          onClick={() => window.print()}
          disabled={!ready}
          className="inline-flex items-center gap-1.5 rounded-lg bg-sky-600 px-3 py-1.5 font-medium text-white hover:bg-sky-700 disabled:opacity-40"
        >
          <FileDown className="size-4" /> Print / Save as PDF
        </button>
        <button onClick={() => window.close()} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 hover:bg-slate-200">
          <X className="size-4" /> Close
        </button>
      </div>

      <header className="space-y-3">
        <h1 className="text-2xl font-bold">{d.name}</h1>
        {d.exams.map((e) => (
          <div key={e.date} className="text-slate-700">
            <span className="font-medium">Exam {formatExamDate(e.date)}</span>
            {e.format && <span> – {e.format}</span>}
            {d.exams.length > 1 && <div className="text-sm text-slate-500">{e.topics.join(" · ")}</div>}
          </div>
        ))}
        <p className="text-sm text-slate-500">
          Exported {new Date().toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })}
        </p>
      </header>

      <nav className="mt-8">
        <h2 className="mb-2 text-sm font-semibold tracking-wide text-slate-500 uppercase">Contents</h2>
        <ol className="list-decimal space-y-0.5 pl-5">
          {docs.map((doc) => (
            <li key={doc.path}>
              <a href={`#${doc.anchor}`} className="text-sky-700">
                {doc.heading}
              </a>
            </li>
          ))}
          {banks.length > 0 && (
            <li>
              <a href="#questions" className="text-sky-700">
                Questions ({cardCount})
              </a>
            </li>
          )}
        </ol>
      </nav>

      {docs.map((doc, i) => {
        const content = contents[i]?.data?.content;
        return (
          <section key={doc.path} id={doc.anchor} className="mt-8 break-before-page print:mt-0">
            <div className="mb-2 border-b border-slate-300 pb-0.5 text-xs font-semibold tracking-wide text-slate-500 uppercase">
              {doc.heading}
            </div>
            {content === undefined ? (
              <p className="text-sm text-slate-500">{contents[i]?.isError ? "This file could not be loaded." : "Loading…"}</p>
            ) : (
              <MarkdownView content={content} path={doc.path} files={files} hideFrontmatter compact />
            )}
          </section>
        );
      })}

      {banks.length > 0 && (
        <section id="questions" className="mt-8 break-before-page print:mt-0">
          <h1 className="mb-3 text-xl font-bold">Questions</h1>
          {banks.map((bank) => (
            <div key={bank.file} className="mb-5">
              <h2 className="mb-2 border-b border-slate-300 pb-0.5 text-base font-semibold break-after-avoid">
                {bank.topic}
                {bank.exam && <span className="ml-2 text-sm font-normal text-slate-500">exam {formatExamDate(bank.exam)}</span>}
              </h2>
              <ol className="space-y-2">
                {bank.cards.map((card, n) => (
                  <li key={card.id} className="break-inside-avoid">
                    <div className="font-semibold">
                      {n + 1}. {card.title}
                    </div>
                    {Object.keys(card.fields).length > 0 && (
                      <dl className="mt-0.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 pl-4 text-sm leading-snug">
                        {Object.entries(card.fields).map(([key, value]) => (
                          <div key={key} className="contents">
                            <dt className="text-slate-500">{key}</dt>
                            <dd className="min-w-0 whitespace-pre-line">{value}</dd>
                          </div>
                        ))}
                      </dl>
                    )}
                  </li>
                ))}
              </ol>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
