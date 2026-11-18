"""Run logs of the agents in the vault: ``agents/<name>/runs.md``, newest run last."""

from __future__ import annotations

from dataclasses import dataclass, field

from lifeos.agents.comments import Texts
from lifeos.agents.config import AGENTS_DIR
from lifeos.core.vault import Vault, write_atomic


@dataclass
class RunRecord:
    started: str  # local time, "YYYY-MM-DD HH:MM"
    ticket: str  # "#12 Title"
    ticket_url: str
    board: str
    outcome: str  # done | blocked | error
    instructions: str  # revision of the agent file
    seconds: int
    cost_eur: float
    steps: list[str] = field(default_factory=list)
    result: str = ""


def runlog_path(agent: str) -> str:
    return f"{AGENTS_DIR}/{agent}/runs.md"


def append_run(vault: Vault, agent: str, title: str, record: RunRecord, t: Texts) -> str:
    rel = runlog_path(agent)
    path = vault.resolve(rel)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
    else:
        text = f"{t('runlog_title', agent=title)}\n\n{t('runlog_intro')}\n"
    key = f"outcome_{record.outcome}"
    outcome = t(key) if key in t.texts else record.outcome
    lines = [
        "",
        f"## {record.started} · {record.ticket} · {outcome}",
        "",
        f"- {t('runlog_ticket')}: {record.ticket_url}",
        f"- {t('runlog_board')}: {record.board}",
        f"- {t('runlog_instructions')} `{record.instructions or '–'}`",
        f"- {t('runlog_duration')}: {record.seconds // 60} min {record.seconds % 60} s",
        f"- {t('runlog_cost')}: {record.cost_eur:.2f} €",
    ]
    if record.steps:
        lines += [f"- {t('runlog_steps')}:"] + [f"  - {s}" for s in record.steps]
    if record.result:
        lines += [f"- {t('runlog_result')}: {record.result}"]
    write_atomic(path, text.rstrip("\n") + "\n" + "\n".join(lines) + "\n")
    return rel
