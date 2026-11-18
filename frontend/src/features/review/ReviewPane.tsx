// A document with feedback rounds: select text or a block to comment on it (md4h rounds).
// Used by workspace reports and review packages; where the rounds live is up to `api`.
import { type QueryKey, type UseQueryOptions, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ClipboardCopy, History, MessageSquarePlus, Plus, Trash2, X } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../lib/api";
import { treeQuery } from "../knowledge/api";
import { flattenFiles } from "../knowledge/links";
import { MarkdownView } from "../knowledge/MarkdownView";
import type { FeedbackItem, RoundData, RoundInfo, Scope, Sealed } from "../workspaces/api";
import { formatTime, itemsAt, type LineRange, merge, rangeLabel, shorten, sourceSlice } from "../workspaces/review";

export type NewItem = { start_line: number; end_line: number; scope: Scope; feedback: string; quote?: string };

/** Where the rounds of one document live. */
export type RoundsApi = {
  invalidate: QueryKey; // invalidated after every change (the document and its round list)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- query keys differ per caller
  round: (id: string) => UseQueryOptions<RoundData, Error, RoundData, any>;
  start: () => Promise<RoundData>;
  add: (round: string, item: NewItem) => Promise<RoundData>;
  update: (round: string, id: string, feedback: string) => Promise<RoundData>;
  remove: (round: string, id: string) => Promise<RoundData>;
  seal?: (round: string) => Promise<Sealed>; // without it, rounds are finished elsewhere (e.g. "Send feedback")
  discard: (round: string) => Promise<unknown>;
};

export const btn =
  "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-40";
const TOP_BLOCK = "article > [data-ls]";

type Target = { scope: Scope; range: LineRange; quote: string };
type Selection = { text: string; range: LineRange; block: LineRange; top: number; left: number };

const lines = (el: Element): LineRange => ({
  start: Number(el.getAttribute("data-ls")),
  end: Number(el.getAttribute("data-le")),
});

const elementOf = (node: Node | null): Element | null =>
  node ? (node.nodeType === Node.ELEMENT_NODE ? (node as Element) : node.parentElement) : null;

export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

export function errorText(e: unknown): string {
  return e instanceof ApiError || e instanceof Error ? e.message : String(e);
}

type Props = {
  content: string;
  path: string; // vault path of the document (resolves relative links)
  rounds: RoundInfo[];
  api: RoundsApi;
  canReview: boolean; // e.g. archived reports cannot get feedback
  title: ReactNode; // left part of the toolbar
  actions?: ReactNode; // extra buttons in the toolbar
  preview?: ReactNode; // the rendered view, shown beside the content
  sealedNote?: (result: Sealed) => ReactNode; // what to show after finishing a round
};

export function ReviewPane({ content, path, rounds, api, canReview, title, actions, preview, sealedNote }: Props) {
  const queryClient = useQueryClient();
  const tree = useQuery(treeQuery);
  const files = useMemo(() => (tree.data ? flattenFiles(tree.data) : []), [tree.data]);

  const draftInfo = rounds.find((r) => r.editable);
  const staleDraft = rounds.find((r) => r.state === "draft" && r.stale);
  const [roundId, setRoundId] = useState<string | null>(null);
  const shownRound = roundId ?? draftInfo?.round ?? null;
  const round = useQuery({ ...api.round(shownRound ?? ""), enabled: canReview && shownRound !== null });
  const reviewing = Boolean(round.data?.editable);

  const [target, setTarget] = useState<Target | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [hovered, setHovered] = useState<{ range: LineRange; top: number } | null>(null);
  const [sealed, setSealed] = useState<Sealed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  const refresh = async (data?: RoundData) => {
    if (data) queryClient.setQueryData(api.round(data.round).queryKey, data);
    await queryClient.invalidateQueries({ queryKey: api.invalidate });
  };

  const run = async <T,>(action: () => Promise<T>): Promise<T | undefined> => {
    setError(null);
    try {
      return await action();
    } catch (e) {
      setError(errorText(e));
      await queryClient.invalidateQueries({ queryKey: api.invalidate });
      return undefined;
    }
  };

  const start = () =>
    run(async () => {
      const data = await api.start();
      setRoundId(data.round);
      setSealed(null);
      await refresh(data);
    });

  const finish = () =>
    run(async () => {
      if (!round.data || !api.seal) return;
      const result = await api.seal(round.data.round);
      setSealed(result);
      setRoundId(result.round);
      await refresh(result);
    });

  const discard = (id: string) =>
    run(async () => {
      if (!window.confirm("Discard this feedback draft and all its comments?")) return;
      await api.discard(id);
      setRoundId(null);
      setTarget(null);
      await refresh();
    });

  // Mark blocks that carry feedback and show the IDs in the gutter.
  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const items = round.data?.items ?? [];
    box.querySelectorAll(TOP_BLOCK).forEach((el) => {
      const ids = itemsAt(items, lines(el)).map((i) => i.id);
      if (ids.length) el.setAttribute("data-fb", ids.join(" "));
      else el.removeAttribute("data-fb");
    });
  }, [round.data, content]);

  // Escape closes the selection toolbar and the composer.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSelection(null);
        setTarget(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const onSelect = () => {
    if (!reviewing || !boxRef.current) return;
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return setSelection(null);
    const range = sel.getRangeAt(0);
    const box = boxRef.current;
    const startEl = elementOf(range.startContainer)?.closest("[data-ls]");
    const endEl = elementOf(range.endContainer)?.closest("[data-ls]");
    const startTop = elementOf(range.startContainer)?.closest(TOP_BLOCK);
    const endTop = elementOf(range.endContainer)?.closest(TOP_BLOCK);
    if (!startEl || !endEl || !startTop || !endTop || !box.contains(startEl) || !box.contains(endEl)) {
      return setSelection(null);
    }
    const rect = range.getBoundingClientRect();
    const boxRect = box.getBoundingClientRect();
    setSelection({
      text: sel.toString(),
      range: merge(lines(startEl), lines(endEl)),
      block: merge(lines(startTop), lines(endTop)),
      top: rect.bottom - boxRect.top + box.scrollTop + 6,
      left: Math.max(0, rect.left - boxRect.left),
    });
  };

  const onHover = (e: React.MouseEvent) => {
    if (!reviewing || !boxRef.current) return;
    const block = (e.target as Element).closest?.(TOP_BLOCK) as HTMLElement | null;
    if (!block || !boxRef.current.contains(block)) return;
    const boxRect = boxRef.current.getBoundingClientRect();
    const top = block.getBoundingClientRect().top - boxRect.top + boxRef.current.scrollTop;
    setHovered((h) => (h && h.range.start === lines(block).start ? h : { range: lines(block), top }));
  };

  const pick = (scope: Scope, range: LineRange, quote: string) => {
    setTarget({ scope, range, quote });
    setSelection(null);
    window.getSelection()?.removeAllRanges();
  };

  const scrollTo = (item: FeedbackItem) => {
    const block = [...(boxRef.current?.querySelectorAll(TOP_BLOCK) ?? [])].find(
      (el) => lines(el).end >= item.start_line,
    );
    block?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const showPanel = canReview && (round.data !== undefined || rounds.length > 0);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-4 py-2 dark:border-slate-800">
        <div className="min-w-0 flex-1 truncate text-sm text-slate-500">{title}</div>
        {actions}
        {canReview && !reviewing && !staleDraft && (
          <button onClick={start} className={`${btn} bg-sky-600 text-white hover:bg-sky-700`}>
            <MessageSquarePlus className="size-4" /> {draftInfo ? "Resume feedback" : "Start feedback"}
          </button>
        )}
        {reviewing && round.data && (
          <>
            <span className="text-xs text-slate-400">{plural(round.data.items.length, "comment")}</span>
            {api.seal && (
              <button
                onClick={finish}
                disabled={round.data.items.length === 0}
                className={`${btn} bg-sky-600 text-white hover:bg-sky-700`}
                title="Seal this round"
              >
                <Check className="size-4" /> Finish
              </button>
            )}
            <button
              onClick={() => discard(round.data!.round)}
              className={`${btn} text-slate-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950`}
              aria-label="Discard draft"
            >
              <Trash2 className="size-4" />
            </button>
          </>
        )}
      </header>

      {staleDraft && (
        <div className="flex flex-wrap items-center gap-2 border-b border-amber-300 bg-amber-50 px-4 py-2 text-sm dark:border-amber-800 dark:bg-amber-950">
          <span className="flex-1">
            This document changed after a feedback draft was started, so the draft can no longer be finished.
          </span>
          <button className={`${btn} bg-amber-600 text-white`} onClick={() => discard(staleDraft.round)}>
            Discard draft ({staleDraft.items})
          </button>
        </div>
      )}
      {error && (
        <div className="flex items-center gap-2 border-b border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          <span className="flex-1">{error}</span>
          <button onClick={() => setError(null)} aria-label="Dismiss">
            <X className="size-4" />
          </button>
        </div>
      )}
      {reviewing && (
        <p className="border-b border-slate-200 bg-sky-50 px-4 py-1.5 text-xs text-sky-800 dark:border-slate-800 dark:bg-sky-950 dark:text-sky-200">
          Feedback mode: select text to comment on it, or use + beside a block. The document itself stays unchanged.
        </p>
      )}

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
        <div
          ref={boxRef}
          className="relative min-h-0 shrink-0 lg:flex-1 lg:shrink lg:overflow-y-auto"
          onMouseUp={onSelect}
          onTouchEnd={() => setTimeout(onSelect, 0)}
          onMouseMove={onHover}
          onMouseLeave={() => setHovered(null)}
        >
          <div className={`mx-auto max-w-3xl py-6 pr-4 sm:pr-8 ${round.data ? "pl-14" : "pl-4 sm:pl-8"}`}>
            <MarkdownView content={content} path={path} files={files} sourceLines />
          </div>

          {reviewing && hovered && !selection && (
            <button
              onClick={() => pick("blocks", hovered.range, sourceSlice(content, hovered.range))}
              style={{ top: hovered.top }}
              className="absolute left-2 z-10 rounded-md bg-white p-1 text-slate-400 shadow ring-1 ring-slate-200 hover:text-sky-600 dark:bg-slate-900 dark:ring-slate-700"
              title={`Comment on this block (${rangeLabel(hovered.range)})`}
            >
              <Plus className="size-4" />
            </button>
          )}
          {selection && (
            <div
              style={{ top: selection.top, left: selection.left }}
              className="absolute z-20 flex gap-1 rounded-lg bg-white p-1 shadow-lg ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
            >
              <button
                className={`${btn} px-2 py-1 hover:bg-slate-100 dark:hover:bg-slate-800`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick("rendered-text", selection.range, selection.text)}
              >
                Comment text
              </button>
              <button
                className={`${btn} px-2 py-1 hover:bg-slate-100 dark:hover:bg-slate-800`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick("blocks", selection.block, sourceSlice(content, selection.block))}
                title="Comment on the whole block(s), with their exact source"
              >
                Comment block
              </button>
            </div>
          )}
        </div>

        {preview && (
          <div className="min-h-[60vh] shrink-0 border-t border-slate-200 lg:min-h-0 lg:w-[45%] lg:shrink-0 lg:border-t-0 lg:border-l dark:border-slate-800">
            {preview}
          </div>
        )}

        {showPanel && (
          <aside className="shrink-0 border-t border-slate-200 lg:w-80 lg:overflow-y-auto lg:border-t-0 lg:border-l dark:border-slate-800">
            {sealed && (
              <div className="border-b border-emerald-300 bg-emerald-50 p-3 text-sm dark:border-emerald-800 dark:bg-emerald-950">
                <div className="mb-1 flex items-center gap-2 font-medium text-emerald-800 dark:text-emerald-200">
                  <Check className="size-4" />
                  <span className="flex-1">Round sealed.</span>
                  <button onClick={() => setSealed(null)} aria-label="Close">
                    <X className="size-4" />
                  </button>
                </div>
                {sealedNote?.(sealed)}
              </div>
            )}
            {target && round.data && reviewing && (
              <Composer
                target={target}
                onCancel={() => setTarget(null)}
                onAdd={(feedback) =>
                  run(async () => {
                    const data = await api.add(round.data!.round, {
                      start_line: target.range.start,
                      end_line: target.range.end,
                      scope: target.scope,
                      feedback,
                      quote: target.scope === "rendered-text" ? target.quote : undefined,
                    });
                    setTarget(null);
                    await refresh(data);
                  })
                }
              />
            )}
            {round.data && (
              <RoundItems
                round={round.data}
                onJump={scrollTo}
                onUpdate={(id, feedback) => run(async () => refresh(await api.update(round.data!.round, id, feedback)))}
                onDelete={(id) =>
                  run(async () => {
                    if (!window.confirm(`Delete comment ${id}?`)) return;
                    await refresh(await api.remove(round.data!.round, id));
                  })
                }
              />
            )}
            <RoundsList rounds={rounds} current={shownRound} onSelect={(id) => setRoundId(id)} />
          </aside>
        )}
      </div>
    </div>
  );
}

/** The agent prompt of a sealed round, copied to the clipboard when possible. */
export function PromptNote({ prompt }: { prompt: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    navigator.clipboard?.writeText(prompt).then(
      () => setCopied(true),
      () => setCopied(false), // clipboard needs HTTPS or localhost; the prompt is shown instead
    );
  }, [prompt]);
  return (
    <>
      <p className="mb-1 flex items-center gap-1.5 text-emerald-800 dark:text-emerald-200">
        <ClipboardCopy className="size-4" /> {copied ? "Agent prompt copied." : "Copy the agent prompt:"}
      </p>
      <textarea
        readOnly
        value={prompt}
        rows={5}
        onFocus={(e) => e.currentTarget.select()}
        className="w-full rounded border border-emerald-200 bg-white p-2 font-mono text-xs dark:border-emerald-900 dark:bg-slate-950"
      />
    </>
  );
}

function Composer({ target, onAdd, onCancel }: { target: Target; onAdd: (text: string) => Promise<unknown>; onCancel: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    await onAdd(text);
    setBusy(false);
  };
  return (
    <div className="fixed inset-x-0 bottom-0 z-30 border-t border-sky-300 bg-white p-3 shadow-2xl lg:static lg:shadow-none dark:border-sky-800 dark:bg-slate-950">
      <p className="mb-1 text-xs font-medium text-slate-500">
        {target.scope === "blocks" ? "Whole block" : "Selected text"} · {rangeLabel(target.range)}
      </p>
      <blockquote className="mb-2 max-h-24 overflow-y-auto rounded bg-slate-50 px-2 py-1 font-mono text-xs whitespace-pre-wrap text-slate-600 dark:bg-slate-900 dark:text-slate-400">
        {target.quote}
      </blockquote>
      <textarea
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if ((e.ctrlKey || e.metaKey) && e.key === "Enter") void submit();
        }}
        rows={4}
        placeholder="What should change? (Ctrl+Enter adds the comment)"
        className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-sky-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900"
      />
      <div className="mt-2 flex justify-end gap-2">
        <button onClick={onCancel} className={`${btn} hover:bg-slate-100 dark:hover:bg-slate-800`}>
          Cancel
        </button>
        <button onClick={submit} disabled={!text.trim() || busy} className={`${btn} bg-sky-600 text-white hover:bg-sky-700`}>
          Add comment
        </button>
      </div>
    </div>
  );
}

function RoundItems(props: {
  round: RoundData;
  onJump: (item: FeedbackItem) => void;
  onUpdate: (id: string, feedback: string) => void;
  onDelete: (id: string) => void;
}) {
  const { round } = props;
  return (
    <section className="p-3">
      <h3 className="mb-2 text-xs font-semibold tracking-wide text-slate-500 uppercase">
        {round.state === "sealed" ? `Sealed round · ${formatTime(round.sealed_at)}` : round.stale ? "Stale draft" : "Draft round"}
      </h3>
      {round.items.length === 0 ? (
        <p className="text-sm text-slate-500">No comments yet.</p>
      ) : (
        <ol className="space-y-3">
          {round.items.map((item) => (
            <li key={item.id} className="rounded-lg border border-slate-200 p-2 text-sm dark:border-slate-800">
              <div className="mb-1 flex items-center gap-2 text-xs text-slate-500">
                <button onClick={() => props.onJump(item)} className="font-semibold text-sky-700 hover:underline dark:text-sky-400">
                  {item.id}
                </button>
                <span className="flex-1">
                  {item.scope === "blocks" ? "block" : "text"} · {rangeLabel({ start: item.start_line, end: item.end_line })}
                </span>
                {round.editable && (
                  <button onClick={() => props.onDelete(item.id)} className="text-slate-400 hover:text-red-600" aria-label={`Delete ${item.id}`}>
                    <Trash2 className="size-3.5" />
                  </button>
                )}
              </div>
              {item.quote && <p className="mb-1 font-mono text-xs text-slate-400">{shorten(item.quote)}</p>}
              {round.editable ? (
                <EditableFeedback value={item.feedback} onSave={(v) => props.onUpdate(item.id, v)} />
              ) : (
                <p className="whitespace-pre-wrap">{item.feedback}</p>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function EditableFeedback({ value, onSave }: { value: string; onSave: (v: string) => void }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  return (
    <textarea
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => {
        if (text.trim() && text !== value) onSave(text);
        else setText(value);
      }}
      rows={Math.min(8, Math.max(2, text.split("\n").length))}
      className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 hover:border-slate-200 focus:border-sky-500 focus:outline-none dark:hover:border-slate-700"
    />
  );
}

function RoundsList({ rounds, current, onSelect }: { rounds: RoundInfo[]; current: string | null; onSelect: (id: string) => void }) {
  if (rounds.length === 0) return null;
  return (
    <section className="border-t border-slate-200 p-3 dark:border-slate-800">
      <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold tracking-wide text-slate-500 uppercase">
        <History className="size-3.5" /> Rounds
      </h3>
      <ul className="space-y-1 text-sm">
        {rounds.map((r) => (
          <li key={r.round}>
            <button
              onClick={() => onSelect(r.round)}
              className={`w-full rounded px-2 py-1 text-left hover:bg-slate-100 dark:hover:bg-slate-800 ${r.round === current ? "bg-slate-100 dark:bg-slate-800" : ""}`}
            >
              <span className="font-medium">{r.state === "sealed" ? "Sealed" : r.stale ? "Stale draft" : "Draft"}</span>
              <span className="ml-2 text-xs text-slate-400">
                {formatTime(r.sealed_at ?? r.created_at)} · {plural(r.items, "comment")}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
