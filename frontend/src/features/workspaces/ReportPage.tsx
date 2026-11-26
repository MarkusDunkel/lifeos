import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowLeft, ScrollText } from "lucide-react";
import { useMemo } from "react";
import { PromptNote, ReviewPane, type RoundsApi } from "../review/ReviewPane";
import {
  addItem,
  deleteItem,
  discardRound,
  reportQuery,
  roundQuery,
  sealRound,
  startRound,
  updateItem,
} from "./api";
import { splitReportPath } from "./review";
import { buttonClass, Loading } from "../../components/ui";

export function ReportPage({ path }: { path: string }) {
  const { folder, name } = splitReportPath(path);
  const doc = useQuery(reportQuery(path));

  const api = useMemo<RoundsApi>(
    () => ({
      invalidate: ["workspaces"],
      round: (id) => roundQuery(name, id),
      start: () => startRound(name),
      add: (round, item) => addItem(name, round, item),
      update: (round, id, feedback) => updateItem(name, round, id, feedback),
      remove: (round, id) => deleteItem(name, round, id),
      seal: (round) => sealRound(name, round),
      discard: (round) => discardRound(name, round),
    }),
    [name],
  );

  if (doc.isError)
    return <p className="p-6 text-slate-500">This report does not exist (anymore). It may have been archived.</p>;
  if (!doc.data) return <Loading className="p-6" />;

  return (
    <ReviewPane
      content={doc.data.content}
      path={path}
      rounds={doc.data.rounds}
      api={api}
      canReview={folder === null}
      sealedNote={(result) => <PromptNote prompt={result.prompt} />}
      title={
        <span className="flex items-center gap-1">
          <Link to="/workspaces" className="-ml-1 rounded-md p-1.5 text-slate-500 md:hidden" aria-label="Back to the list">
            <ArrowLeft className="size-5" />
          </Link>
          {folder && `${folder} / `}
          <span className="font-medium text-slate-900 dark:text-slate-100">{name}</span>
        </span>
      }
      actions={
        doc.data.changelog && (
          <Link
            to="/workspaces/$"
            params={{ _splat: doc.data.changelog }}
            className={`${buttonClass("ghost")}`}
          >
            <ScrollText className="size-4" /> Change log
          </Link>
        )
      }
    />
  );
}
