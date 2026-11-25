from __future__ import annotations

import json
import logging
import random
from datetime import UTC, datetime
from pathlib import Path

import anyio
from fastapi.testclient import TestClient
from mcp.client.client import Client

from lifeos.core.config import Settings
from lifeos.core.vault import Vault
from lifeos.main import create_app
from lifeos.mcp_server.http import MaskPath
from lifeos.mcp_server.server import build_mcp
from lifeos.modules.study.service import StudyService

TOKEN = "t" * 40
SUBJECT = "---\nfach: Sample\nprüfung: 2026-11-21T12:30\nsprache: en\n---\n# Sample\n"
BANK = "**SL-1** – alpha\n- Definition: first\n\n**SL-2** – beta\n- Definition: second\n"


def make_vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    folder = root / "ready" / "knowledge" / "Studium" / "Sample"
    folder.mkdir(parents=True)
    (folder / "_Fach.md").write_text(SUBJECT, encoding="utf-8")
    (folder / "Letters - Prüfen.md").write_text(BANK, encoding="utf-8")
    return root


def payload(result) -> dict:
    if getattr(result, "structured_content", None):
        data = result.structured_content
        return data.get("result", data) if isinstance(data, dict) else data
    return json.loads(result.content[0].text)


def test_tools_run_a_session(tmp_path: Path) -> None:
    root = make_vault(tmp_path)
    committed: list = []
    study = StudyService(
        Vault.open(root),
        "Europe/Vienna",
        committer=lambda paths, msg: committed.append(msg),
        clock=lambda: datetime(2026, 11, 16, 12, tzinfo=UTC),
        rng=random.Random(0),
    )
    server = build_mcp(study)

    async def scenario() -> None:
        async with Client(server) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert names == {
                "list_subjects",
                "start_session",
                "next_question",
                "record_answer",
                "correct_answer",
                "end_session",
                "progress",
                "read_material",
            }
            subjects = payload(await client.call_tool("list_subjects", {}))
            assert subjects["subjects"][0]["key"] == "Sample"
            started = payload(
                await client.call_tool("start_session", {"subject": "sample", "count": 2})
            )
            sid = started["session_id"]
            card = started["question"]["card"]["id"]
            recorded = payload(
                await client.call_tool(
                    "record_answer",
                    {"session_id": sid, "card_id": card, "result": "correct", "note": ""},
                )
            )
            assert recorded["recorded"] is True
            bad = payload(
                await client.call_tool(
                    "record_answer", {"session_id": "nope", "card_id": card, "result": "correct"}
                )
            )
            assert "error" in bad
            summary = payload(await client.call_tool("end_session", {"session_id": sid}))
            assert summary["answered"] == 1

    anyio.run(scenario)
    assert committed and committed[0].startswith("study: Sample – 1 answer, 1 correct")


def test_http_mount_only_under_secret_path(tmp_path: Path) -> None:
    root = make_vault(tmp_path)
    settings = Settings(vault_root=root, session_secret="x" * 40, mcp_path_token=TOKEN)
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(create_app(settings)) as client:
        ok = client.post(f"/mcp/{TOKEN}/", json=init, headers=headers)
        assert ok.status_code == 200
        assert ok.json()["result"]["serverInfo"]["name"] == "LifeOS Study"
        wrong = client.post(f"/mcp/{'u' * 40}/", json=init, headers=headers)
        assert wrong.status_code in (404, 405)


def test_mcp_is_off_without_token(tmp_path: Path) -> None:
    settings = Settings(vault_root=make_vault(tmp_path), session_secret="x" * 40)
    with TestClient(create_app(settings)) as client:
        assert client.post(f"/mcp/{TOKEN}/", json={}).status_code in (404, 405)


def test_access_log_masks_the_token() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s"',
        ("1.2.3.4", "POST", f"/mcp/{TOKEN}/"),
        None,
    )
    MaskPath(TOKEN).filter(record)
    assert TOKEN not in record.getMessage()
    assert "/mcp/***/" in record.getMessage()


def test_tools_carry_coach_rules(tmp_path: Path) -> None:
    root = make_vault(tmp_path)
    study = StudyService(
        Vault.open(root),
        "Europe/Vienna",
        clock=lambda: datetime(2026, 11, 16, 12, tzinfo=UTC),
        rng=random.Random(0),
    )
    server = build_mcp(study)

    async def scenario() -> None:
        async with Client(server) as client:
            tools = {t.name: t.description for t in (await client.list_tools()).tools}
            assert "MUST be called after every answer" in tools["record_answer"]
            assert "MUST be called" in tools["correct_answer"]
            for name in ("record_answer", "correct_answer", "end_session"):
                assert "Never say saved" in tools[name]

            started = payload(
                await client.call_tool("start_session", {"subject": "sample", "count": 2})
            )
            sid = started["session_id"]
            first = started["question"]["card"]["id"]
            assert "call record_answer BEFORE" in started["coach_rule"]
            assert "'bewerten' or 'weiter'" in started["coach_rule"]
            assert "previous" not in started["question"]

            saved = payload(
                await client.call_tool(
                    "record_answer", {"session_id": sid, "card_id": first, "result": "partial"}
                )
            )
            assert saved["coach_rule"].startswith("Saved on the server as partial.")
            assert f"correct_answer for card {first}" in saved["coach_rule"]
            assert "amends their answer" in saved["coach_rule"]
            assert "not record_answer again" in saved["coach_rule"]
            assert "follow-up talk is open" in saved["coach_rule"]
            assert "only when the learner says 'weiter'" in saved["coach_rule"]

            nxt = payload(await client.call_tool("next_question", {"session_id": sid}))
            assert nxt["previous"]["card"] == first
            assert nxt["previous"]["result"] == "partial"
            assert f"Previous card {first} was saved as partial" in nxt["coach_rule"]
            assert "call record_answer BEFORE" in nxt["coach_rule"]

            fixed = payload(
                await client.call_tool(
                    "correct_answer", {"session_id": sid, "card_id": first, "result": "known"}
                )
            )
            assert fixed["coach_rule"].startswith(
                f"Changed on the server: card {first} is now known"
            )
            again = payload(await client.call_tool("next_question", {"session_id": sid}))
            assert again["previous"]["result"] == "known"

            bad = payload(
                await client.call_tool(
                    "record_answer", {"session_id": "nope", "card_id": first, "result": "correct"}
                )
            )
            assert bad["coach_rule"].startswith("The tool failed: nothing was saved")
            assert "Call start_session again" in bad["coach_rule"]

            wrong_card = payload(
                await client.call_tool(
                    "record_answer", {"session_id": sid, "card_id": "XX-9", "result": "correct"}
                )
            )
            assert "start_session again" not in wrong_card["coach_rule"]

            open_end = payload(await client.call_tool("end_session", {"session_id": sid}))
            assert open_end["ended"] is False
            assert "NOT saved" in open_end["coach_rule"]

    anyio.run(scenario)


def test_instructions_name_every_control_word() -> None:
    from lifeos.mcp_server.server import INSTRUCTIONS

    for word in (
        "fertig",
        "bewerten",
        "weiter",
        "kenn ich",
        "zählt als richtig",
        "Schluss",
        "Hilfe",
    ):
        assert f'"{word}"' in INSTRUCTIONS
    assert "Follow-up talk" in INSTRUCTIONS
    assert "at most partial" in INSTRUCTIONS
    assert "go back to the session" not in INSTRUCTIONS
