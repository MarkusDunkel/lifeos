"""The comments AI teammates leave on tickets.

Agents write with the owner's GitHub token, so their comments appear under the owner's
name. Every agent comment therefore starts with an invisible marker; comments without it
are the owner's and count as instructions.

The wording comes from ``agents/texts.yaml`` in the vault (any of the keys below); the
defaults here are English.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import yaml

from lifeos.agents.config import AGENTS_DIR
from lifeos.agents.spend import Spend
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
    "footer_subscription": (
        "<sub>Instructions `{revision}` · {minutes} min {seconds} s · subscription: "
        "{tokens}k tokens (API price {list_cost} €){limits}</sub>"
    ),
    "limits": " · usage {windows}",
    "window": "{percent} % ({name})",
    "window_five_hour": "5 h",
    "window_seven_day": "week",
    "credential_expiring": (
        "⚠️ The Claude subscription token expires on {date}. Create a new one with "
        "`claude setup-token` and replace it on the server."
    ),
    "billing_line": "💳 Paid via: {way}",
    "billing_api": "API (pay per use)",
    "billing_subscription": "Claude subscription",
    # history entry when billing is switched in LifeOS (section of the agent file)
    "history_heading": "History",
    "history_billing": "{date}: paid via {way} instead of {previous} (switched in LifeOS).",
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
    "runlog_subscription": "Subscription: {tokens}k tokens (API price {list_cost} €)",
    "runlog_steps": "Steps",
    "runlog_result": "Result",
    "outcome_done": "done",
    "outcome_blocked": "blocked (question)",
    "outcome_error": "error",
    # report of the application writer
    "report_files": "**Files:** {links}",
    "report_letter": "Cover letter",
    "report_cv": "CV",
    "report_folder": "Folder",
    "report_decisions": "**Key decisions**",
    "report_uncertain": "**Uncertain about**",
    "report_check": "**Please check**",
    "report_review": "**Review:** {passed} of {total} checks passed",
    "report_fixed": "**Fixed after review**",
    "report_open": "**Still open after review**",
    "report_research": "**Research:** {searches} web searches – {link}",
    "report_feedback": "**Your feedback applied:** {count} comment(s)",
    "report_review_link": "👀 **[Open in review]({url})** – comment on the content and the layout",
    "review_notes": "Notes",
    "review_research": "Research",
    "report_comments": "**Your comments, as I read them**",
    "report_comment_this": "- “{comment}” → for this ticket: {understood}",
    "report_comment_general": "- “{comment}” → general: {understood}",
    "report_proposals": "**Proposals in the inbox** (waiting for your approval, move to Todo):",
    "proposal_source": "Proposed by {agent} while working on {ref} ({url}).",
    # the assistant's answers to work orders (comments starting with "KI:")
    "order_title": "🤖 **Work order received**",
    "order_understood": "**Understood as:** {text}",
    "order_new": (
        "→ New ticket in the inbox: {link} – it waits there for your approval (move it to Todo)."
    ),
    "order_existing": "→ Added to the open ticket {link} instead of creating a second one.",
    "order_current": "**For this ticket:** {text}",
    "order_error": (
        "⛔ **I could not process this work order.** {error}\n\n"
        "Write the comment again (starting with `KI:`) to retry."
    ),
    "order_source": "From a comment of the owner on {ref} ({url}):\n\n{quote}",
    "order_addition": "🤖 **Addition from {ref}** ({url})",
    # the assistant's report on an approved ticket it worked on
    "task_summary": "**Done:** {summary}",
    "task_files": "**Changed files** (committed in the vault)",
    "task_changes": "**What changed**",
    "task_rejected": "**Not applied** (outside what I may change, or invalid)",
    "task_nothing": "No file was changed.",
    "task_log": "**Every step of this run:** {link}",
    "task_changed_before_question": (
        "\n\nBefore asking I had already changed these files (not committed yet):{files}"
    ),
    "tool_log_title": "# Steps: {ticket}",
    "tool_log_intro": "{count} tool calls of the assistant on {url}, in order. Secrets redacted.",
    # releases: what went live for a ticket, and undoing it (boards with a discard column)
    "report_commit": "**Changes:** commit {link}",
    "release_vault": "**Live in the vault:** commit {link}",
    "release_pr_queued": (
        "**App:** pull request {link} – tests, merge and deployment run now; I report here."
    ),
    "release_pr_skipped": "**App:** pull request {link} is not released automatically: {reason}",
    "release_not_configured": "releases are not set up (LIFEOS_APP_REPO)",
    "release_other_repo": "it is not a branch of the app repository",
    "release_pr_missing": "the pull request was not found",
    "release_pr_not_open": "it is {state}",
    "release_pr_wrong_base": "it targets `{base}` instead of `{branch}`",
    "release_pr_belongs": "Belongs to {ref}.",
    "release_test_failed": (
        "⛔ **Tests failed** for {link} – nothing was deployed.\n\n```\n{message}\n```"
    ),
    "release_merge_failed": "⛔ **Could not merge** {link}: {error}",
    "release_tested": "✅ Tests passed for {link}; merged as {merge}, deploying now.",
    "release_live": "🚀 **Live:** {link} runs on the server now (version `{version}`).",
    "release_deploy_failed": (
        "⛔ **Deployment failed** for {link} – the previous version runs again.\n\n"
        "```\n{message}\n```"
    ),
    "release_branch_reset": "\n\nThe merge was undone on the branch ({revert}).",
    "release_branch_not_reset": (
        "\n\n⚠️ The merge could not be undone on the branch ({error}) – please do it by hand."
    ),
    "release_reverted": "↩️ **Undone** – the ticket was moved to **{column}**:",
    "release_revert_vault": "- vault: commit {commit} undone by {revert}",
    "release_revert_app": (
        "- app: pull request {link} undone by {revert}; the previous version is being deployed"
    ),
    "release_revert_cancelled": (
        "- app: pull request {link} was not merged yet – it will not be released"
    ),
    "release_revert_failed": "- ⛔ could not undo {what}: {error} – please undo it by hand",
    # heading of the human summary in agents/<name>.md (shown to the assistant)
    "summary_heading": "Summary",
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
    billing: str = "",
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
    if billing:
        parts.append(billing)
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


def billing_line(t: Texts, billing: str) -> str:
    return t("billing_line", way=t(f"billing_{billing}"))


def kilo(tokens: int) -> str:
    return str(round(tokens / 1000))


def footer(
    t: Texts,
    revision: str | None,
    seconds: int,
    spend: Spend,
    billing: str = "api",
    now: datetime | None = None,
) -> str:
    common = {"revision": revision or "–", "minutes": seconds // 60, "seconds": seconds % 60}
    if billing != "subscription":
        return t("footer", cost=f"{spend.cost_eur:.2f}", **common)
    windows = [
        t(
            "window",
            percent=round(share * 100),
            name=t(f"window_{name}") if f"window_{name}" in t.texts else name,
        )  # fmt: skip
        for name, share in spend.limits.items()
    ]
    text = t(
        "footer_subscription",
        tokens=kilo(spend.tokens),
        list_cost=f"{spend.list_eur:.2f}",
        limits=t("limits", windows=", ".join(windows)) if windows else "",
        **common,
    )
    expires = spend.expires_soon(now or datetime.now(UTC))
    if expires is not None:
        text = t("credential_expiring", date=expires.strftime("%Y-%m-%d")) + "\n\n" + text
    return text
