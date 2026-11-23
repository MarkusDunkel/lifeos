"""Markdown pipe tables: find them by their first header cell, read and extend them.

The career documents keep their data in plain tables so that the owner can edit them in
any Markdown editor. A table is found by the text of its first header cell (several
spellings allowed, compared case-insensitively), never by the heading above it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SEPARATOR = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


@dataclass
class Table:
    header: list[str]
    rows: list[list[str]] = field(default_factory=list)
    start: int = 0  # line index of the header
    end: int = 0  # line index after the last row

    def column(self, *names: str) -> int | None:
        """The first column whose header is one of ``names`` or starts with one of them
        (``Weight %`` matches ``weight``)."""
        wanted = [n.casefold() for n in names]
        for i, cell in enumerate(self.header):
            text = cell.casefold().strip()
            if text in wanted or any(text.startswith(w + " ") for w in wanted):
                return i
        return None


def split_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    # split on pipes that are not escaped and not inside a [[wiki|link]]
    cells, buf, depth, i = [], "", 0, 0
    while i < len(text):
        ch = text[i]
        if text.startswith("[[", i):
            depth += 1
            buf += "[["
            i += 2
            continue
        if text.startswith("]]", i) and depth:
            depth -= 1
            buf += "]]"
            i += 2
            continue
        if ch == "\\" and i + 1 < len(text) and text[i + 1] == "|":
            buf += "|"
            i += 2
            continue
        if ch == "|" and not depth:
            cells.append(buf.strip())
            buf = ""
        else:
            buf += ch
        i += 1
    cells.append(buf.strip())
    return cells


def tables(text: str) -> list[Table]:
    lines = text.splitlines()
    out: list[Table] = []
    i = 0
    while i < len(lines) - 1:
        line = lines[i]
        if line.strip().startswith("|") and _SEPARATOR.match(lines[i + 1].strip()):
            table = Table(header=split_row(line), start=i)
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                table.rows.append(split_row(lines[j]))
                j += 1
            table.end = j
            out.append(table)
            i = j
            continue
        i += 1
    return out


def find(text: str, *first_cells: str) -> Table | None:
    """The first table whose first header cell is one of ``first_cells``."""
    wanted = {c.casefold() for c in first_cells}
    for table in tables(text):
        if table.header and table.header[0].casefold() in wanted:
            return table
    return None


def cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return row[index].strip()


def escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def render(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(escape(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def append_rows(text: str, table: Table, rows: list[list[str]]) -> str:
    """``text`` with ``rows`` added at the end of ``table`` (found in that same text)."""
    if not rows:
        return text
    lines = text.splitlines()
    new = ["| " + " | ".join(escape(c) for c in row) + " |" for row in rows]
    lines[table.end : table.end] = new
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def wiki_label(value: str) -> str:
    """``[[target|label]]`` → label, ``[[target]]`` → target, backticks removed."""
    value = value.strip().strip("`")
    match = re.fullmatch(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", value)
    if match:
        return (match.group(2) or match.group(1)).strip()
    return value


def wiki_target(value: str) -> str:
    value = value.strip().strip("`")
    match = re.fullmatch(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", value)
    return match.group(1).strip() if match else value
