"""The comments AI teammates leave on tickets.

Agents write with the owner's GitHub token, so their comments appear under the owner's
name. Every agent comment therefore starts with an invisible marker; comments without it
are the owner's and count as instructions.

The wording comes from ``agents/texts.yaml`` in the vault (any of the keys below); the
defaults here are English.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from lifeos.agents.config import AGENTS_DIR
from lifeos.core.vault import Vault

TEXTS_FILE = f"{AGENTS_DIR}/texts.yaml"
MARKER_RE = re.compile(r"<!--\s*lifeos-agent:\s*([a-z0-9-]+)")

DEFAULT_TEXTS: dict[str, str] = {
    "taken": "▶️ **Taken: {agent}**",
    "column_means": "This ticket is in **{column}**. That means: {description}",
    "what_i_do": "**What I do now:**",
    "instructions": "📖 My instructions: {link}",
    "instructions_local": "`{path}` in the vault",
    "revision": " · revision `{revision}`",
    "uncommitted": " (changed locally, not committed yet)",
    "done": "✅ **Done – please review**",
    "question": "❓ **Question – waiting for you**",
    "error": "⛔ **Stopped**",
    "error_line": "Error: {error}",
    "unblock_hint": "When you are ready: clear the **AI** field and I continue.",
    "interrupted": "⛔ **Run interrupted**",
    "interrupted_line": (
        "The **AI** field has been `running` for more than {minutes} minutes, but nothing is "
        "running any more (e.g. the server restarted). I set it to `blocked`."
    ),
    "no_workflow": (
        "There is no implementation of the workflow `{workflow}` in the code yet, so I "
        "cannot work on this ticket."
    ),
    "footer": "<sub>Instructions `{revision}` · {minutes} min {seconds} s · {cost} €</sub>",
    "workflow_heading": "Workflow",
    "drift_title": "Board “{title}” differs from the vault",
    "drift_body": (
        "The board **{title}** on GitHub differs from its contract in the vault (`{path}`):\n\n"
        "{items}\n\nNothing was overwritten. Please decide:\n\n"
        "- The change on GitHub is wanted → update `{path}` in the vault accordingly.\n"
        "- It was a mistake → change anything in `{path}` and commit; LifeOS then mirrors "
        "the contract to GitHub again."
    ),
    "runlog_title": "# Runs: {agent}",
    "runlog_intro": "Every run of the agent, newest last. Written by LifeOS.",
    "runlog_ticket": "Ticket",
    "runlog_board": "Board",
    "runlog_instructions": "Instructions: revision",
    "runlog_duration": "Duration",
    "runlog_cost": "Cost",
    "runlog_steps": "Steps",
    "runlog_result": "Result",
    "outcome_done": "done",
    "outcome_blocked": "blocked (question)",
    "outcome_error": "error",
}


class Texts:
    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        self.texts = DEFAULT_TEXTS | {
            k: str(v) for k, v in (overrides or {}).items() if k in DEFAULT_TEXTS
        }

    def __call__(self, key: str, **values: Any) -> str:
        return self.texts[key].format(**values)

    @classmethod
    def load(cls, vault: Vault) -> Texts:
        path = vault.resolve(TEXTS_FILE)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            data = None
        return cls(data if isinstance(data, dict) else None)


def marker(agent: str) -> str:
    return f"<!-- lifeos-agent: {agent} -->"


def agent_of(comment_body: str) -> str | None:
    """The agent that wrote a comment, or None for a comment of the owner."""
    match = MARKER_RE.search(comment_body or "")
    return match.group(1) if match else None


def numbered_steps(section: str) -> str:
    """The numbered list of an agent's workflow section."""
    steps: list[str] = []
    for line in section.splitlines():
        if re.match(r"^\d+\.\s", line):
            steps.append(line.rstrip())
        elif steps and line.startswith((" ", "\t")) and line.strip():
            steps[-1] += " " + line.strip()  # wrapped continuation of the step
        elif steps and not line.strip():
            continue
        elif steps:
            break
    return "\n".join(steps)


def instructions_line(t: Texts, link: str, revision: str | None, dirty: bool) -> str:
    line = t("instructions", link=link)
    if revision:
        line += t("revision", revision=revision)
    if dirty:
        line += t("uncommitted")
    return line


def start_comment(
    t: Texts,
    agent: str,
    title: str,
    column: str,
    description: str,
    steps: str,
    instructions: str,
) -> str:
    parts = [
        marker(agent),
        t("taken", agent=title),
        "",
        t("column_means", column=column, description=description).rstrip(),
    ]
    if steps:
        parts += ["", t("what_i_do"), steps]
    parts += ["", instructions]
    return "\n".join(parts)


def report_comment(t: Texts, agent: str, report: str, footer: str) -> str:
    return "\n".join([marker(agent), t("done"), "", report.strip(), "", footer])


def question_comment(t: Texts, agent: str, question: str, footer: str) -> str:
    return "\n".join(
        [marker(agent), t("question"), "", question.strip(), "", t("unblock_hint"), "", footer]
    )


def error_comment(t: Texts, agent: str, error: str, footer: str) -> str:
    return "\n".join(
        [marker(agent), t("error"), "", t("error_line", error=error.strip()), "",
         t("unblock_hint"), "", footer]
    )  # fmt: skip


def interrupted_comment(t: Texts, agent: str, minutes: int) -> str:
    return "\n".join(
        [marker(agent), t("interrupted"), "", t("interrupted_line", minutes=minutes), "",
         t("unblock_hint")]
    )  # fmt: skip


def footer(t: Texts, revision: str | None, seconds: int, cost_eur: float) -> str:
    return t(
        "footer",
        revision=revision or "–",
        minutes=seconds // 60,
        seconds=seconds % 60,
        cost=f"{cost_eur:.2f}",
    )
