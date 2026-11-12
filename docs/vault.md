# Vault specification

```
vault/
├── inbox/                 raw ingest data per connector (gitignored, reproducible)
├── ready/                 the workbench ("AI ready") — the only place humans and agents edit
│   ├── notes/             notes as JSON Lines
│   ├── knowledge/         knowledge hub, an Obsidian-compatible Markdown vault
│   ├── tickets/           GitHub issue mirror: <owner>/<repo>/<number>-<slug>.md
│   └── workspaces/        short-lived AI reports (flat)
│       ├── aenderungsprotokoll/   change log per processed report
│       └── archive/               processed reports, moved unchanged
├── outbox/                digest plans and results per connector
├── agents/                personal agent overrides and context
├── .md4h/feedback/        feedback rounds on workspace files (md4h v2 format)
├── .lifeos/               runtime state: indexes, merge bases (gitignored)
└── CLAUDE.md              rules for agents working in the vault
```

## Data flow

```
source ──ingest──▶ inbox/ ──normalise/merge──▶ ready/ ◀── humans, agents
                                                 │
                                   digest plan ──▶ outbox/<connector>/<ts>_plan.md
                                                 │   (reviewed by the user)
                                   apply ────────▶ source, result in outbox/
```

## Workspace lifecycle

1. **Create** — an agent writes exactly one Markdown file into `ready/workspaces/`.
2. **Review** — the user highlights passages and comments; each round is stored as
   `.md4h/feedback/ready/workspaces/<file>--<round>/feedback.md` and sealed together
   with the SHA-256 of the reviewed file.
3. **Clean up** — an agent applies every feedback item to its target (notes, tickets,
   knowledge), always writes `aenderungsprotokoll/<file>`, and moves the report to
   `archive/` once no question is left open.

Workspace files are never edited after creation, and `.md4h/` is never modified by agents.

## Conventions

- UTF-8, LF line endings (`.gitattributes` enforces it).
- Markdown must render in "Markdown for Humans": no `==highlight==`, no footnotes, no raw
  HTML outside code, no lists that mix checkboxes and plain items.
