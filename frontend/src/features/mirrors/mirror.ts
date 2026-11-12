// Mirror notes written by the sync connectors (OneDrive presentations, Moodle).
// They are read-only in LifeOS; their source of truth is the external system.

export type MirrorKind = "presentation" | "moodle-file" | "moodle-course";

export type MirrorMeta = {
  kind: MirrorKind;
  title: string;
  url: string;
  /** Short facts shown under the title. */
  details: string[];
  openLabel: string;
  syncPath: string;
  deleted: boolean;
};

/** Counts shared by the sync reports of all mirror connectors. */
export type MirrorReport = {
  created: string[];
  updated: string[];
  renamed: string[];
  deleted: string[];
  unchanged: number;
  skipped?: string[];
  errors: string[];
  synced_at: string;
};

/** Flat `key: value` YAML as the backend writes it (plain or single/double-quoted scalars). */
export function parseFlatYaml(text: string): Record<string, string> {
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

const date = (iso: string | undefined) => (iso ? new Date(iso).toLocaleDateString() : null);

/** The mirror behind a note, or null for ordinary notes (and unsafe links). */
export function mirrorOf(frontmatter: string | null): MirrorMeta | null {
  if (!frontmatter) return null;
  const f = parseFlatYaml(frontmatter);
  if (!/^https:\/\//.test(f.url ?? "")) return null;
  const base = { title: f.title ?? "", url: f.url, deleted: f.status === "deleted" };
  const facts = (...items: (string | null | false | undefined)[]) => items.filter((x): x is string => !!x);
  if (f.type === "presentation") {
    return {
      ...base,
      kind: "presentation",
      details: facts(`${Number(f.slides) || 0} slides`, date(f.modified) && `changed ${date(f.modified)}`, `OneDrive: ${f.onedrive_path ?? ""}`),
      openLabel: "Open in PowerPoint",
      syncPath: "/api/presentations/sync",
    };
  }
  if (f.type === "moodle-file") {
    return {
      ...base,
      kind: "moodle-file",
      details: facts(f.course, f.section, f.file, f.pages && `${f.pages} pages`, date(f.modified) && `changed ${date(f.modified)}`),
      openLabel: "Open in Moodle",
      syncPath: "/api/moodle/sync",
    };
  }
  if (f.type === "moodle-course") {
    return {
      ...base,
      kind: "moodle-course",
      details: facts("Course overview", f.shortname),
      openLabel: "Open in Moodle",
      syncPath: "/api/moodle/sync",
    };
  }
  return null;
}

/** The note text for pasting into Claude: without the title line and the mirror notice. */
export function textForClaude(meta: MirrorMeta, body: string): string {
  const text = body
    .replace(/^\s*# [^\n]*\n+/, "")
    .replace(/^> (Mirror|Overview) of [^\n]*\n+/m, "")
    .trim();
  return `"${meta.title}" (${meta.details.join(", ")})\n\n${text}\n`;
}

export function summarize(r: MirrorReport): string {
  const parts = [
    r.created.length && `${r.created.length} new`,
    r.updated.length && `${r.updated.length} updated`,
    r.renamed.length && `${r.renamed.length} moved`,
    r.deleted.length && `${r.deleted.length} removed at the source`,
    `${r.unchanged} unchanged`,
    r.skipped?.length && `${r.skipped.length} not mirrored`,
    r.errors.length && `${r.errors.length} failed`,
  ];
  return parts.filter(Boolean).join(" · ");
}
