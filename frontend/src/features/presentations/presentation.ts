// Mirror notes of OneDrive presentations (written by the backend sync).

import type { SyncReport } from "./api";

export type PresentationMeta = {
  title: string;
  url: string;
  onedrivePath: string;
  modified: string;
  slides: number;
  deleted: boolean;
};

/** Flat `key: value` YAML as the backend writes it (plain or single/double-quoted scalars). */
function parseFlatYaml(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split(/\r?\n/)) {
    const m = /^([A-Za-z_][\w-]*):\s*(.*)$/.exec(line);
    if (!m) continue;
    let value = m[2].trim();
    if (value.startsWith("'") && value.endsWith("'") && value.length > 1) value = value.slice(1, -1).replace(/''/g, "'");
    else if (value.startsWith('"') && value.endsWith('"') && value.length > 1) value = JSON.parse(value) as string;
    out[m[1]] = value;
  }
  return out;
}

/** The presentation behind a note, or null if the note is not a mirror. */
export function presentationOf(frontmatter: string | null): PresentationMeta | null {
  if (!frontmatter) return null;
  const f = parseFlatYaml(frontmatter);
  if (f.type !== "presentation" || !/^https:\/\//.test(f.url ?? "")) return null;
  return {
    title: f.title ?? "",
    url: f.url,
    onedrivePath: f.onedrive_path ?? "",
    modified: f.modified ?? "",
    slides: Number(f.slides) || 0,
    deleted: f.status === "deleted",
  };
}

/** Slide text for pasting into Claude: the body without the mirror notice. */
export function textForClaude(meta: PresentationMeta, body: string): string {
  const slides = body.replace(/^[\s\S]*?(?=^## )/m, "").trim();
  return `Presentation "${meta.title}" (${meta.slides} slides, OneDrive: ${meta.onedrivePath})\n\n${slides}\n`;
}

export function summarize(r: SyncReport): string {
  const parts = [
    r.created.length && `${r.created.length} new`,
    r.updated.length && `${r.updated.length} updated`,
    r.renamed.length && `${r.renamed.length} moved`,
    r.deleted.length && `${r.deleted.length} deleted in OneDrive`,
    `${r.unchanged} unchanged`,
    r.errors.length && `${r.errors.length} failed`,
  ];
  return parts.filter(Boolean).join(" · ");
}
