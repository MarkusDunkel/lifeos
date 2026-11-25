import { ExternalLink, Flag } from "lucide-react";
import type { BasedOn, ControlStatus, FlowNode, Generator, Level } from "./api";

import { formatAgo } from "./format";
import { LEVEL_STYLE } from "./layout";

function Dot({ level, className = "size-2.5" }: { level: Level; className?: string }) {
  return <span className={`inline-block shrink-0 rounded-full ${LEVEL_STYLE[level].dot} ${className}`} />;
}

/** One input of the process that makes the actionable: fresh? considered? */
function InputRow({ b }: { b: BasedOn }) {
  const open = (b.newSince ?? 0) + (b.pending ?? 0);
  return (
    <li className="rounded-lg border border-slate-200 p-2.5 dark:border-slate-800">
      <div className="flex items-center gap-2 font-medium">
        <Dot level={b.level} className="size-2" /> {b.label}
        <span className="ml-auto text-xs font-normal text-slate-500">
          {b.kind === "source"
            ? "source"
            : b.updated
              ? `updated ${formatAgo(b.updated)}`
              : b.local
                ? "kept by you"
                : "never updated"}
        </span>
      </div>
      <div className="mt-1 text-xs text-slate-600 dark:text-slate-300">
        {b.total !== undefined && b.total > 0 && (
          <span>
            {b.total - (b.pending ?? 0)} of {b.total} notes considered
            {b.consideredLastRun ? ` (${b.consideredLastRun} in the last run)` : ""}.{" "}
          </span>
        )}
        {open > 0 ? (
          <span className="text-amber-700 dark:text-amber-300">
            {open} new or changed note(s) not considered yet – {b.byLabel} picks them up in its next run.
          </span>
        ) : (
          <span className="text-emerald-700 dark:text-emerald-300">Nothing new since {b.byLabel} last ran.</span>
        )}
      </div>
    </li>
  );
}

function GeneratorRun({ label, g }: { label: string; g: Generator }) {
  return (
    <div className="space-y-1 text-sm">
      <div className="font-medium">
        Last run of {label} <span className="font-normal text-slate-500">· {formatAgo(g.lastRun)}</span>
      </div>
      {g.summary && <p>{g.summary}</p>}
      {g.events && g.events.length > 0 && (
        <ul className="list-inside list-disc text-xs text-slate-600 dark:text-slate-300">
          {g.events.slice(0, 12).map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      {g.uncertain && g.uncertain.length > 0 && (
        <div className="text-xs">
          <div className="font-medium text-amber-700 dark:text-amber-300">Uncertain, not turned into tickets:</div>
          <ul className="list-inside list-disc text-slate-600 dark:text-slate-300">
            {g.uncertain.map((u) => (
              <li key={u}>{u}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** Can I trust this actionable? What went into it, and what is new since? */
export function ActionableDetails({ s, node }: { s: ControlStatus; node: FlowNode }) {
  const trust = node.trust;
  const style = LEVEL_STYLE[node.level];
  const label = (id: string) => s.processes.find((p) => p.id === id)?.label ?? id;
  const internal = node.url?.startsWith("/");
  return (
    <div className="space-y-4">
      <div>
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <Flag className="size-5 text-slate-500" /> {node.label}
        </h2>
        <p className="text-sm text-slate-500">Actionable · {node.detail}</p>
      </div>
      {node.url && (
        <a
          href={node.url}
          target={internal ? undefined : "_blank"}
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 rounded-lg bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-700"
        >
          <ExternalLink className="size-4" /> Open
        </a>
      )}
      <div className={`rounded-lg border p-3 text-sm ${style.box}`}>
        <div className={`flex items-center gap-2 font-medium ${style.text}`}>
          <Dot level={node.level} /> {node.level === "ok" ? "Trustworthy" : node.level === "off" ? "Not set up" : "Check before you rely on it"}
        </div>
        <div>{node.reason}</div>
      </div>
      {trust && trust.basedOn.length > 0 && (
        <div>
          <h3 className="mb-1.5 text-sm font-medium">Based on</h3>
          <ul className="space-y-1.5 text-sm">
            {trust.basedOn.map((b) => (
              <InputRow key={`${b.by}:${b.id}`} b={b} />
            ))}
          </ul>
        </div>
      )}
      {trust && trust.upstream.length > 0 && (
        <div className="text-sm">
          <h3 className="mb-1 font-medium">Processes before it</h3>
          <ul className="space-y-0.5">
            {[...trust.producers, ...trust.upstream].map((id) => {
              const p = s.processes.find((x) => x.id === id);
              return (
                p && (
                  <li key={id} className="flex items-center gap-2">
                    <Dot level={p.level} className="size-2" /> {p.label}
                    <span className="text-xs text-slate-500">· last success {formatAgo(p.lastSuccess)}</span>
                  </li>
                )
              );
            })}
          </ul>
        </div>
      )}
      {trust?.generators &&
        Object.entries(trust.generators).map(([id, g]) => <GeneratorRun key={id} label={label(id)} g={g} />)}
    </div>
  );
}

/** The actionables at a glance: can I rely on them right now? */
export function ActionableStrip({ s, onSelect }: { s: ControlStatus; onSelect: (id: string) => void }) {
  const items = s.nodes.filter((n) => n.kind === "actionable" && n.level !== "off");
  if (!items.length) return null;
  return (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
      {items.map((n) => {
        const style = LEVEL_STYLE[n.level];
        const open = (n.trust?.basedOn ?? []).reduce((sum, b) => sum + (b.newSince ?? 0) + (b.pending ?? 0), 0);
        return (
          <button
            key={n.id}
            onClick={() => onSelect(n.id)}
            className={`rounded-r-lg border border-l-[6px] p-2.5 text-left text-xs ${style.box}`}
          >
            <div className="flex items-center gap-1.5 text-sm font-medium text-slate-800 dark:text-slate-100">
              <Flag className="size-3.5 text-slate-500" /> {n.label}
              <Dot level={n.level} className="ml-auto size-2.5" />
            </div>
            <div className={`mt-0.5 line-clamp-2 ${style.text}`}>
              {n.level === "ok" ? "Trustworthy" : n.reason}
            </div>
            {open > 0 && <div className="mt-0.5 text-amber-700 dark:text-amber-300">{open} new note(s) not considered yet</div>}
          </button>
        );
      })}
    </div>
  );
}
