"""Claude Code calls (``claude -p``) against a fake ``claude`` executable."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

from lifeos.agents.claude_code import (
    ClaudeCode,
    ClaudeCodeError,
    NotSubscription,
    SubscriptionLimit,
    clean_env,
)
from lifeos.agents.team import AgentBlocked
from lifeos.modules.applications.writer import ClaudeCodeLlm

# A stand-in for `claude -p --output-format stream-json`: records how it was called and
# prints the events of the scenario in FAKE_SCENARIO.
FAKE = r"""
import json, os, sys, time
from pathlib import Path

args = sys.argv[1:]
sys.stdin.reconfigure(encoding="utf-8")  # like claude; Windows defaults to the ANSI codepage
prompt = sys.stdin.read()
system = Path(args[args.index("--system-prompt-file") + 1]).read_text(encoding="utf-8")
record = Path(os.environ["FAKE_RECORD"])
calls = json.loads(record.read_text(encoding="utf-8")) if record.exists() else []
calls.append({"args": args, "prompt": prompt, "system": system, "cwd": os.getcwd(),
              "cwd_files": os.listdir("."),
              "env": sorted(k for k in os.environ if k.startswith(("ANTHROPIC", "CLAUDE")))})
record.write_text(json.dumps(calls), encoding="utf-8")
scenario = os.environ["FAKE_SCENARIO"]

def emit(event):
    print(json.dumps(event), flush=True)

if scenario == "crash":
    print("boom", file=sys.stderr)
    sys.exit(3)
source = "ANTHROPIC_API_KEY" if scenario == "api_key" else "none"
emit({"type": "system", "subtype": "init", "apiKeySource": source})
if scenario == "slow":
    time.sleep(30)
status = "rejected" if scenario == "limit" else "allowed"
emit({"type": "rate_limit_event", "rate_limit_info": {"status": status, "resetsAt": 1800000000,
      "unifiedWindows": {"five_hour": {"utilization": 0.4}, "seven_day": {"utilization": 0.2}}}})
emit({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "WebSearch", "input": {"query": "q"}},
    {"type": "tool_use", "name": "WebFetch", "input": {"url": "u"}}]}})
result = {"type": "result", "subtype": "success", "is_error": False, "result": "notes",
          "stop_reason": "end_turn", "credential_expires_at": "2027-01-01T00:00:00Z",
          "modelUsage": {"m": {"inputTokens": 10, "outputTokens": 20,
                               "cacheReadInputTokens": 30, "cacheCreationInputTokens": 40,
                               "costUSD": 0.5}}}
if scenario in ("limit", "error"):
    result.update(is_error=True, subtype="error_during_execution", result="it failed",
                  api_error_status=429 if scenario == "limit" else 500)
if scenario == "structured":
    answers = json.loads(os.environ["FAKE_ANSWERS"])
    result["structured_output"] = answers[min(len(calls), len(answers)) - 1]
emit(result)
"""


@pytest.fixture
def fake(tmp_path: Path):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE, encoding="utf-8")
    record = tmp_path / "calls.json"

    def make(scenario: str, timeout: int = 60, **extra: str) -> ClaudeCode:
        env = dict(os.environ)
        env.update(
            ANTHROPIC_API_KEY="must-not-leak",
            ANTHROPIC_BASE_URL="https://example.invalid",
            CLAUDECODE="1",
            CLAUDE_CODE_OAUTH_TOKEN="subscription-token",
            FAKE_SCENARIO=scenario,
            FAKE_RECORD=str(record),
            **extra,
        )
        return ClaudeCode(command=[sys.executable, str(script)], timeout=timeout, env=env)

    def calls() -> list[dict]:
        return json.loads(record.read_text(encoding="utf-8"))

    make.calls = calls  # type: ignore[attr-defined]
    return make


def test_clean_env_keeps_only_the_subscription_token():
    keep = {"CLAUDE_CODE_OAUTH_TOKEN": "o", "CLAUDE_CONFIG_DIR": "/c", "PATH": "/bin"}
    drop = {"ANTHROPIC_API_KEY": "k", "ANTHROPIC_AUTH_TOKEN": "t", "CLAUDECODE": "1"}
    env = clean_env(keep | drop)
    assert env == {"CLAUDE_CODE_OAUTH_TOKEN": "o", "CLAUDE_CONFIG_DIR": "/c", "PATH": "/bin"}


def test_call_passes_prompt_system_and_tools_without_api_key(fake):
    cli = fake("ok")
    result = cli.call(system="System ä", prompt="Prompt ü", model="m", effort="high",
                      tools=("WebSearch", "WebFetch"), max_turns=7)  # fmt: skip
    call = fake.calls()[0]
    assert call["prompt"] == "Prompt ü" and call["system"] == "System ä"
    assert call["env"] == ["CLAUDE_CODE_OAUTH_TOKEN"]
    assert call["cwd_files"] == []  # an empty working directory, no project context
    args = call["args"]
    assert args[args.index("--tools") + 1] == "WebSearch,WebFetch"
    assert args[args.index("--allowedTools") + 1] == "WebSearch,WebFetch"
    assert args[args.index("--model") + 1] == "m" and args[args.index("--effort") + 1] == "high"
    assert args[args.index("--max-turns") + 1] == "7"
    assert args[args.index("--setting-sources") + 1] == ""
    assert "--bare" not in args  # bare mode ignores the subscription token
    assert result.text == "notes"
    assert result.tokens == 100 and result.list_usd == 0.5 and result.searches == 1
    assert result.rate_limit.status == "allowed"
    assert result.rate_limit.utilization == {"five_hour": 0.4, "seven_day": 0.2}
    assert result.credential_expires_at == "2027-01-01T00:00:00Z"


def test_no_tools_and_schema(fake):
    fake("structured", FAKE_ANSWERS='[{"a": 1}]').call(
        system="s", prompt="p", model="m", effort="low", schema={"type": "object"}
    )
    args = fake.calls()[0]["args"]
    assert args[args.index("--tools") + 1] == ""
    assert "--allowedTools" not in args
    assert json.loads(args[args.index("--json-schema") + 1]) == {"type": "object"}


def test_api_key_credential_stops_the_call(fake):
    with pytest.raises(NotSubscription):
        fake("api_key").call(system="s", prompt="p", model="m", effort="low")


def test_limit_reached(fake):
    with pytest.raises(SubscriptionLimit, match="resets"):
        fake("limit").call(system="s", prompt="p", model="m", effort="low")


@pytest.mark.parametrize(("scenario", "message"), [
    ("error", "it failed"), ("crash", "exit 3"), ("slow", "did not finish"),
])  # fmt: skip
def test_failures(fake, scenario, message):
    with pytest.raises(ClaudeCodeError, match=message):
        fake(scenario, timeout=3).call(system="s", prompt="p", model="m", effort="low")


class Answer(BaseModel):
    value: int


def test_llm_parse_retries_an_invalid_answer(fake):
    llm = ClaudeCodeLlm("m", fake("structured", FAKE_ANSWERS='[{"value": "x"}, {"value": 3}]'))
    answer, usage = llm.parse("s", "p", Answer, "high")
    assert answer == Answer(value=3)
    assert usage.usd == 0 and usage.tokens == 200 and usage.list_usd == 1.0
    calls = fake.calls()
    assert len(calls) == 2 and "did not match the required format" in calls[1]["prompt"]


def test_llm_parse_gives_up(fake):
    llm = ClaudeCodeLlm("m", fake("structured", FAKE_ANSWERS='[{"value": "x"}]'))
    with pytest.raises(RuntimeError, match="expected format"):
        llm.parse("s", "p", Answer, "high")


def test_llm_limit_blocks_the_ticket(fake):
    with pytest.raises(AgentBlocked, match="usage limit"):
        ClaudeCodeLlm("m", fake("limit")).research("s", "p", 5, "medium")


def test_llm_research_uses_web_tools_only(fake):
    text, usage = ClaudeCodeLlm("m", fake("ok")).research("s", "p", 3, "medium")
    args = fake.calls()[0]["args"]
    assert args[args.index("--tools") + 1] == "WebSearch,WebFetch"
    assert args[args.index("--max-turns") + 1] == "10"
    assert text == "notes" and usage.searches == 1 and usage.usd == 0
