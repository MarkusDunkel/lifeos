"""Shared helpers for turning documents into Markdown mirror notes."""

from __future__ import annotations

import re

import yaml


class ExtractError(Exception):
    """A file cannot be read (damaged, password-protected, unsupported)."""


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\x0b", " ")).strip()


def escape(line: str) -> str:
    """Keep document text from turning into Markdown headings, quotes or rules."""
    return "\\" + line if re.match(r"^(#|>|---|\*\*\*|=+$)", line) else line


def md_table(rows: list[list[str]]) -> str:
    rows = [[clean(c).replace("|", "\\|") for c in r] for r in rows if r]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def link(url: str) -> str:
    """A URL usable as Markdown link target."""
    return f"<{url}>" if any(c in url for c in " ()<>") else url


# -- frontmatter of mirror notes ---------------------------------------------------


def dump_front(front: dict) -> str:
    return "---\n" + yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip() + "\n---\n"


def front_matter(content: str) -> dict:
    if not content.startswith("---\n"):
        return {}
    end = content.find("\n---\n", 4)
    if end < 0:
        return {}
    try:
        data = yaml.safe_load(content[4:end])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def mark_deleted(content: str) -> str:
    """Flag a mirror whose source is gone; the text stays for reference."""
    if not content.startswith("---\n"):
        return content
    end = content.find("\n---\n", 4)
    if end < 0:
        return content
    front = front_matter(content)
    if front.get("status") == "deleted":
        return content
    front["status"] = "deleted"
    return dump_front(front) + content[end + 5 :]
