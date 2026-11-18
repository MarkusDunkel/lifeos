"""Model calls through Claude Code (``claude -p``), billed to the owner's Claude subscription.

Each call runs ``claude`` non-interactively in an empty temporary directory, without
settings, MCP servers, session files or project context. The caller decides which built-in
tools are available (none, or only web search and fetch for research), so the model can
only return text or structured data; files and board writes stay with the code.

**Subscription only.** Claude Code bills to an API key whenever one is in its environment.
The call therefore removes every ``ANTHROPIC_*`` and ``CLAUDE*`` variable except the
subscription token (``CLAUDE_CODE_OAUTH_TOKEN``, created with ``claude setup-token``) and
the config directory, and it stops the process as soon as Claude Code reports a credential
other than the subscription — before the first model request is answered.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Variables passed on to Claude Code; all other ANTHROPIC_* / CLAUDE* ones are removed.
KEEP_ENV = ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR")
SUBSCRIPTION_SOURCE = "none"  # init event `apiKeySource` when no API key is in use
WEB_TOOLS = ("WebSearch", "WebFetch")
DEFAULT_TIMEOUT = 15 * 60


class ClaudeCodeError(RuntimeError):
    """The call failed: process error, API error, timeout or unexpected output."""


class NotSubscription(ClaudeCodeError):  # noqa: N818 - reads better at the raise site
    """Claude Code would bill an API key instead of the subscription."""


class SubscriptionLimit(ClaudeCodeError):  # noqa: N818
    """The subscription's usage limit is reached; the call was not answered."""


class Refused(ClaudeCodeError):  # noqa: N818
    """The model declined the request."""


@dataclass
class RateLimit:
    status: str = ""
    utilization: dict[str, float] = field(default_factory=dict)  # window -> 0..1
    resets_at: int | None = None  # unix seconds


@dataclass
class CallResult:
    text: str = ""
    structured: Any = None
    tokens: int = 0  # all tokens processed (input, cache reads and writes, output)
    list_usd: float = 0.0  # what the call would cost at API list prices (not billed)
    searches: int = 0
    rate_limit: RateLimit | None = None
    credential_expires_at: str | None = None


def clean_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for Claude Code: no API keys, no settings of a parent session."""
    source = os.environ if env is None else env
    return {
        k: v
        for k, v in source.items()
        if k in KEEP_ENV or not (k.startswith("ANTHROPIC") or k.startswith("CLAUDE"))
    }


class ClaudeCode:
    """Runs single ``claude -p`` calls; see the module docstring."""

    def __init__(
        self,
        command: Sequence[str] = ("claude",),
        timeout: int = DEFAULT_TIMEOUT,
        env: dict[str, str] | None = None,
    ) -> None:
        self.command = list(command)
        self.timeout = timeout
        self.env = clean_env(env)

    def call(
        self,
        *,
        system: str,
        prompt: str,
        model: str,
        effort: str,
        tools: Sequence[str] = (),
        schema: dict[str, Any] | None = None,
        max_turns: int | None = None,
    ) -> CallResult:
        with tempfile.TemporaryDirectory(
            prefix="lifeos-claude-", ignore_cleanup_errors=True
        ) as tmp:
            workdir = Path(tmp) / "work"
            workdir.mkdir()
            system_file = Path(tmp) / "system.md"
            system_file.write_text(system, encoding="utf-8")
            args = [
                *self.command,
                "-p",
                "--output-format", "stream-json",
                "--verbose",
                "--no-session-persistence",
                "--setting-sources", "",
                "--strict-mcp-config",
                "--model", model,
                "--effort", effort,
                "--system-prompt-file", str(system_file),
                "--tools", ",".join(tools),
            ]  # fmt: skip
            if tools:
                args += ["--allowedTools", ",".join(tools)]
            if schema is not None:
                args += ["--json-schema", json.dumps(schema)]
            if max_turns is not None:
                args += ["--max-turns", str(max_turns)]
            return self._run(args, prompt, workdir, Path(tmp) / "stderr.txt")

    def _run(self, args: list[str], prompt: str, workdir: Path, stderr_path: Path) -> CallResult:
        with stderr_path.open("w", encoding="utf-8") as stderr:
            try:
                proc = subprocess.Popen(
                    args,
                    cwd=workdir,
                    env=self.env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=stderr,
                    text=True,
                    encoding="utf-8",
                )
            except OSError as exc:
                raise ClaudeCodeError(f"Claude Code cannot be started: {exc}") from exc
            timed_out = threading.Event()

            def kill_on_timeout() -> None:
                timed_out.set()
                proc.kill()

            timer = threading.Timer(self.timeout, kill_on_timeout)
            timer.start()
            writer = threading.Thread(target=_feed, args=(proc, prompt), daemon=True)
            writer.start()
            try:
                result = self._read(proc)
            finally:
                timer.cancel()
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
                writer.join(timeout=5)
        if timed_out.is_set():
            raise ClaudeCodeError(f"Claude Code did not finish within {self.timeout} s")
        if result is None:
            detail = stderr_path.read_text(encoding="utf-8").strip()[-500:]
            raise ClaudeCodeError(
                f"Claude Code ended without a result (exit {proc.returncode}): {detail}"
            )
        return result

    def _read(self, proc: subprocess.Popen[str]) -> CallResult | None:
        assert proc.stdout is not None
        out = CallResult()
        seen_init = False
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "system" and event.get("subtype") == "init":
                seen_init = True
                source = event.get("apiKeySource")
                if source != SUBSCRIPTION_SOURCE:
                    proc.kill()
                    raise NotSubscription(
                        f"Claude Code would not use the subscription (credential: {source}); "
                        "no API key may be visible to it."
                    )
            elif not seen_init:
                proc.kill()
                raise ClaudeCodeError("Claude Code did not report its credential first")
            elif kind == "rate_limit_event":
                out.rate_limit = _rate_limit(event.get("rate_limit_info") or {})
            elif kind == "assistant":
                for block in (event.get("message") or {}).get("content") or []:
                    if block.get("type") == "tool_use" and block.get("name") == "WebSearch":
                        out.searches += 1
            elif kind == "result":
                return _finish(out, event)
        return None


def _feed(proc: subprocess.Popen[str], prompt: str) -> None:
    try:
        assert proc.stdin is not None
        proc.stdin.write(prompt)
        proc.stdin.close()
    except (OSError, ValueError):
        pass  # the process ended early; reading reports why


def _rate_limit(info: dict[str, Any]) -> RateLimit:
    windows = info.get("unifiedWindows") or {}
    return RateLimit(
        status=str(info.get("status") or ""),
        utilization={
            name: float(w.get("utilization") or 0.0)
            for name, w in windows.items()
            if isinstance(w, dict)
        },
        resets_at=info.get("resetsAt"),
    )


def _finish(out: CallResult, event: dict[str, Any]) -> CallResult:
    for usage in (event.get("modelUsage") or {}).values():
        out.tokens += sum(
            int(usage.get(k) or 0)
            for k in ("inputTokens", "outputTokens", "cacheReadInputTokens",
                      "cacheCreationInputTokens")
        )  # fmt: skip
        out.list_usd += float(usage.get("costUSD") or 0.0)
    out.credential_expires_at = event.get("credential_expires_at")
    out.text = str(event.get("result") or "")
    out.structured = event.get("structured_output")
    limited = out.rate_limit is not None and out.rate_limit.status not in ("", "allowed")
    if event.get("is_error") or event.get("subtype") != "success":
        if limited or event.get("api_error_status") == 429:
            raise SubscriptionLimit(_limit_message(out.rate_limit))
        detail = out.text or event.get("terminal_reason") or event.get("subtype")
        raise ClaudeCodeError(f"Claude Code reported an error: {detail}")
    if event.get("stop_reason") == "refusal":
        raise Refused("the model declined the request")
    return out


def _limit_message(rate: RateLimit | None) -> str:
    message = "The usage limit of the Claude subscription is reached"
    if rate and rate.resets_at:
        from datetime import UTC, datetime

        when = datetime.fromtimestamp(rate.resets_at, UTC).strftime("%Y-%m-%d %H:%M UTC")
        message += f" (resets {when})"
    return message + "."
