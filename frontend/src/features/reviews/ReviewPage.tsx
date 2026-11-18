import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowLeft, Check, ExternalLink, FileText, Send, TriangleAlert, X } from "lucide-react";
import { useMemo, useState } from "react";
import { treeQuery } from "../knowledge/api";
import { flattenFiles } from "../knowledge/links";
import { MarkdownView } from "../knowledge/MarkdownView";
import { formatTime } from "../workspaces/review";
import { btn, errorText, plural, ReviewPane } from "../review/ReviewPane";
import {
  packageQuery,
  packagesQuery,
  pendingComments,
  type ReviewDocument,
  roundsApi,
  type SendResult,
  sendFeedback,
  ticketUrl,
  viewUrl,
} from "./api";

export function PackageList() {
  const { data, isError } = useQuery(packagesQuery);
  if (isError) return <p className="p-4 text-sm text-red-600">Could not load review packages.</p>;
  if (!data) return <p className="p-4 text-sm text-slate-400">Loading…</p>;
  return (
    <nav className="h-full overflow-y-auto p-2">
      <h2 className="px-2 pt-2 pb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase">To review</h2>
      {data.length === 0 && (
        <p className="px-2 py-3 text-sm text-slate-500">
          Nothing to review. Agents describe what they hand over in a <code>review.yaml</code>.
        </p>
      )}
      <ul>
        {data.map((p) => (
          <li key={p.path}>
            <Link
              to="/reviews/$"
              params={{ _splat: p.path }}
              className="flex gap-2 rounded-lg px-2 py-2 text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
              activeProps={{ className: "bg-sky-50 dark:bg-sky-950/40" }}
            >
              {p.error ? <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-500" /> : <FileText className="mt-0.5 size-4 shrink-0 text-slate-400" />}
              <span className="min-w-0">
                <span className="block truncate font-medium">{p.title}</span>
                <span className="block text-xs text-slate-500">
                  {p.error ? p.error : `${plural(p.documents, "document")} · ${formatTime(p.modified)}`}
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function ReviewPage({ path }: { path: string }) {
  const pkg = useQuery(packageQuery(path));
  const queryClient = useQueryClient();
  const [active, setActive] = useState(0);
  const [sent, setSent] = useState<SendResult | null>(null);
  const send = useMutation({
    mutationFn: () => sendFeedback(path),
    onSuccess: (result) => setSent(result),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["reviews"] }),
  });
  if (pkg.isError) return <p className="p-6 text-slate-500">This review package does not exist (anymore).</p>;
  if (!pkg.data) return <p className="p-6 text-slate-400">Loading…</p>;
  const docs = pkg.data.documents;
  const doc = docs[Math.min(active, docs.length - 1)];
  const ticket = pkg.data.ticket ? ticketUrl(pkg.data.ticket) : null;
  const pending = pendingComments(docs);
  const confirmSend = () => {
    const text =
      `Send ${plural(pending, "comment")} to ${pkg.data.ticket}?\n\n` +
      "The drafts are sealed and can no longer change. The ticket stays where it is: " +
      "move it back to the agent's column when it should rework the documents.";
    if (window.confirm(text)) send.mutate();
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-4 pt-2 dark:border-slate-800">
        <Link to="/reviews" className="-ml-1 rounded-md p-1.5 text-slate-500 md:hidden" aria-label="Back to the list">
          <ArrowLeft className="size-5" />
        </Link>
        <h1 className="min-w-0 flex-1 truncate font-medium">{pkg.data.title}</h1>
        {ticket && (
          <a href={ticket} target="_blank" rel="noreferrer" className={`${btn} text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800`}>
            {pkg.data.ticket} <ExternalLink className="size-3.5" />
          </a>
        )}
        {pkg.data.ticket && (
          <button
            onClick={confirmSend}
            disabled={pending === 0 || send.isPending}
            className={`${btn} bg-sky-600 text-white hover:bg-sky-700`}
            title={pending === 0 ? "Add comments to the documents first" : "Post all comments to the ticket"}
          >
            <Send className="size-4" /> {send.isPending ? "Sending…" : `Send feedback${pending ? ` (${pending})` : ""}`}
          </button>
        )}
        {send.isError && <p className="w-full text-sm text-red-600">Could not send: {errorText(send.error)}</p>}
        {sent && (
          <div className="flex w-full items-center gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200">
            <Check className="size-4 shrink-0" />
            <span className="flex-1">
              Sent {plural(sent.comments, "comment")} to{" "}
              {sent.url ? (
                <a href={sent.url} target="_blank" rel="noreferrer" className="underline">
                  {sent.ticket}
                </a>
              ) : (
                sent.ticket
              )}
              . Move the ticket back to the agent when it should rework the documents.
            </span>
            <button onClick={() => setSent(null)} aria-label="Close">
              <X className="size-4" />
            </button>
          </div>
        )}
        <nav className="flex w-full gap-1 overflow-x-auto" role="tablist">
          {docs.map((d) => (
            <button
              key={d.index}
              role="tab"
              aria-selected={d.index === doc.index}
              onClick={() => setActive(d.index)}
              className={`border-b-2 px-3 py-2 text-sm whitespace-nowrap ${
                d.index === doc.index
                  ? "border-sky-600 font-medium text-sky-700 dark:text-sky-300"
                  : "border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
              }`}
            >
              {d.label}
              {d.rounds.some((r) => r.editable) && <span className="ml-1 text-xs text-sky-600">●</span>}
            </button>
          ))}
        </nav>
      </div>
      <div className="min-h-0 flex-1">
        <DocumentReview key={`${path}#${doc.index}`} path={path} doc={doc} />
      </div>
    </div>
  );
}

function DocumentReview({ path, doc }: { path: string; doc: ReviewDocument }) {
  const api = useMemo(() => roundsApi(path, doc.index), [path, doc.index]);
  if (!doc.exists) return <p className="p-6 text-slate-500">The file {doc.source} is missing.</p>;
  return (
    <ReviewPane
      content={doc.content}
      path={doc.source}
      rounds={doc.rounds}
      api={api}
      canReview
      title={<span className="text-xs">{doc.source}</span>}
      preview={<Preview path={path} doc={doc} />}
    />
  );
}

function Preview({ path, doc }: { path: string; doc: ReviewDocument }) {
  const tree = useQuery(treeQuery);
  const files = useMemo(() => (tree.data ? flattenFiles(tree.data) : []), [tree.data]);
  if (doc.view === "none") return null;
  if (doc.view === "markdown") {
    return (
      <div className="h-full overflow-y-auto bg-slate-50 p-6 dark:bg-slate-900">
        <MarkdownView content={doc.content} path={doc.source} files={files} />
      </div>
    );
  }
  // PDFs (stored or generated on request) in the browser's own viewer.
  return <iframe title={`${doc.label} – view`} src={viewUrl(path, doc.index, doc.sha256)} className="h-full min-h-[60vh] w-full bg-slate-100" />;
}
