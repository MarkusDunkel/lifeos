// Pure helpers for reviewing a report. DOM access stays in the components.

import type { FeedbackItem } from "./api";

export type LineRange = { start: number; end: number };

export const rangeLabel = ({ start, end }: LineRange) => (start === end ? `line ${start}` : `lines ${start}-${end}`);

export const merge = (a: LineRange, b: LineRange): LineRange => ({
  start: Math.min(a.start, b.start),
  end: Math.max(a.end, b.end),
});

export const overlaps = (a: LineRange, b: LineRange) => a.start <= b.end && b.start <= a.end;

/** Source lines ``start..end`` (one-based, inclusive) of ``content``, LF-normalised. */
export function sourceSlice(content: string, { start, end }: LineRange): string {
  return content.replace(/\r\n/g, "\n").split("\n").slice(start - 1, end).join("\n");
}

/** Items whose lines touch the given block, in document order. */
export function itemsAt(items: FeedbackItem[], block: LineRange): FeedbackItem[] {
  return items.filter((i) => overlaps({ start: i.start_line, end: i.end_line }, block));
}

export function shorten(text: string, max = 160): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** Report name and folder from a path such as ``archive/x.md``. */
export function splitReportPath(path: string): { folder: string | null; name: string } {
  const i = path.indexOf("/");
  return i < 0 ? { folder: null, name: path } : { folder: path.slice(0, i), name: path.slice(i + 1) };
}

export function formatTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
