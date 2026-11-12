"""In-memory full-text and link index over the knowledge hub.

The index is rebuilt incrementally on demand (``refresh``): files are compared by
mtime and size, so edits made outside the app (Obsidian, agents, git) are picked
up on the next query without a file watcher.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

WIKILINK_RE = re.compile(r"!?\[\[([^\]\|#\^]+)(?:[#\^][^\]\|]*)?(?:\|[^\]]*)?\]\]")
MDLINK_RE = re.compile(r"\]\(([^)\s]+\.md)(?:#[^)]*)?\)")
HEADING_RE = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.MULTILINE)
TOKEN_RE = re.compile(r"\w+", re.UNICODE)


@dataclass
class SearchHit:
    path: str
    title: str
    snippet: str


def title_of(path: str, content: str) -> str:
    match = HEADING_RE.search(content)
    return match.group(1).strip() if match else PurePosixPath(path).stem


def link_targets(src: str, content: str) -> set[str]:
    """Normalised link targets: lower-case, without ``.md``, POSIX separators."""
    targets: set[str] = set()
    for raw in WIKILINK_RE.findall(content):
        t = raw.strip().replace("\\", "/").lower()
        targets.add(t.removesuffix(".md"))
    src_dir = PurePosixPath(src).parent
    for raw in MDLINK_RE.findall(content):
        if "://" in raw:
            continue
        joined = PurePosixPath(*(src_dir / raw.replace("%20", " ")).parts)
        parts: list[str] = []
        for part in joined.parts:
            if part == "..":
                if parts:
                    parts.pop()
            elif part != ".":
                parts.append(part)
        targets.add("/".join(parts).lower().removesuffix(".md"))
    return targets


def link_matches(target: str, path: str) -> bool:
    """Obsidian-style resolution: a target matches by basename or by path suffix."""
    p = path.lower().removesuffix(".md")
    return target == p or p.endswith("/" + target) or target == PurePosixPath(p).name


class KnowledgeIndex:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()
        self._db = sqlite3.connect(":memory:", check_same_thread=False)
        self._db.executescript(
            """
            CREATE TABLE docs(path TEXT PRIMARY KEY, mtime INTEGER, size INTEGER, title TEXT);
            CREATE TABLE links(src TEXT, target TEXT);
            CREATE INDEX links_target ON links(target);
            CREATE VIRTUAL TABLE fts USING fts5(path UNINDEXED, title, body,
                tokenize = 'unicode61 remove_diacritics 2');
            """
        )

    def _scan(self) -> dict[str, tuple[int, int]]:
        found: dict[str, tuple[int, int]] = {}
        if not self.root.exists():
            return found
        for file in self.root.rglob("*.md"):
            rel = file.relative_to(self.root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            st = file.stat()
            found[rel.as_posix()] = (st.st_mtime_ns, st.st_size)
        return found

    def refresh(self) -> None:
        with self._lock:
            current = self._scan()
            known = {
                row[0]: (row[1], row[2])
                for row in self._db.execute("SELECT path, mtime, size FROM docs")
            }
            for path in known.keys() - current.keys():
                self._remove(path)
            for path, stamp in current.items():
                if known.get(path) != stamp:
                    self._upsert(path, stamp)
            self._db.commit()

    def _remove(self, path: str) -> None:
        self._db.execute("DELETE FROM docs WHERE path = ?", (path,))
        self._db.execute("DELETE FROM links WHERE src = ?", (path,))
        self._db.execute("DELETE FROM fts WHERE path = ?", (path,))

    def _upsert(self, path: str, stamp: tuple[int, int]) -> None:
        self._remove(path)
        try:
            content = (self.root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        title = title_of(path, content)
        self._db.execute("INSERT INTO docs VALUES (?, ?, ?, ?)", (path, *stamp, title))
        self._db.execute("INSERT INTO fts VALUES (?, ?, ?)", (path, title, content))
        self._db.executemany(
            "INSERT INTO links VALUES (?, ?)", [(path, t) for t in link_targets(path, content)]
        )

    def search(self, query: str, limit: int = 30) -> list[SearchHit]:
        tokens = TOKEN_RE.findall(query)
        if not tokens:
            return []
        match = " ".join(f'"{t}"*' for t in tokens)
        self.refresh()
        with self._lock:
            rows = self._db.execute(
                """
                SELECT path, title, snippet(fts, 2, '[[[', ']]]', ' … ', 12)
                FROM fts WHERE fts MATCH ? ORDER BY bm25(fts, 0, 5.0, 1.0) LIMIT ?
                """,
                (match, limit),
            ).fetchall()
        return [SearchHit(path=r[0], title=r[1], snippet=r[2]) for r in rows]

    def backlinks(self, path: str) -> list[tuple[str, str]]:
        self.refresh()
        with self._lock:
            rows = self._db.execute(
                "SELECT DISTINCT l.src, l.target, d.title "
                "FROM links l JOIN docs d ON d.path = l.src"
            ).fetchall()
        hits = {
            (src, title)
            for src, target, title in rows
            if src != path and link_matches(target, path)
        }
        return sorted(hits)
