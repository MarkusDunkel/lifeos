// Areas on rendered pages (percent of the page), for comments on the layout of a PDF.
import type { Region } from "../workspaces/api";

/** A rectangle in percent of the page (x, y from the top left). */
export type Box = { x: number; y: number; w: number; h: number };
/** A run of text on the page, from the pdf.js text layer. */
export type TextBox = Box & { str: string; eol: boolean };

export type Point = { x: number; y: number };

const MIN_SIZE = 1; // percent: smaller drags are clicks, not areas
const round1 = (n: number) => Math.round(n * 10) / 10;
const clamp = (n: number, max: number) => Math.min(Math.max(n, 0), max);

/** The area between two points of a drag on a page of `width` × `height` pixels. */
export function dragRegion(page: number, a: Point, b: Point, width: number, height: number): Region | null {
  if (width <= 0 || height <= 0) return null;
  const x1 = clamp(Math.min(a.x, b.x), width);
  const x2 = clamp(Math.max(a.x, b.x), width);
  const y1 = clamp(Math.min(a.y, b.y), height);
  const y2 = clamp(Math.max(a.y, b.y), height);
  const w = ((x2 - x1) / width) * 100;
  const h = ((y2 - y1) / height) * 100;
  if (w < MIN_SIZE || h < MIN_SIZE) return null;
  const x = round1((x1 / width) * 100);
  const y = round1((y1 / height) * 100);
  return { page, x, y, w: Math.min(round1(w), 100 - x), h: Math.min(round1(h), 100 - y) };
}

/** The text whose runs have their centre inside `area`, in reading order of the page. */
export function textIn(boxes: TextBox[], area: Box): string {
  let out = "";
  let last = -2;
  boxes.forEach((b, i) => {
    const cx = b.x + b.w / 2;
    const cy = b.y + b.h / 2;
    if (cx < area.x || cx > area.x + area.w || cy < area.y || cy > area.y + area.h) return;
    if (last >= 0 && last !== i - 1 && !/\s$/.test(out)) out += " "; // skipped runs between
    out += b.str + (b.eol ? "\n" : "");
    last = i;
  });
  return out
    .split("\n")
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter(Boolean)
    .join("\n");
}

export const regionLabel = (r: Region) => `page ${r.page}`;
