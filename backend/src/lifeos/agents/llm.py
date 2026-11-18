"""Model access for agents: the Messages API or Claude Code on the owner's subscription.

``billing:`` in the agent definition chooses (see :func:`default_llm`). Both offer the same
two calls: ``research`` (text, with web search) and ``parse`` (structured output).
"""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from lifeos.agents.claude_code import (
    DEFAULT_TIMEOUT,
    WEB_TOOLS,
    CallResult,
    ClaudeCode,
    Refused,
    SubscriptionLimit,
)
from lifeos.agents.config import AgentDef
from lifeos.agents.team import AgentBlocked

log = logging.getLogger("lifeos.agents.llm")

MAX_CONTINUATIONS = 3

# USD per million tokens (input, output); cache writes cost 1.25× input, reads 0.05×.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
WEB_SEARCH_USD = 0.01  # per search


# -- the model -----------------------------------------------------------------------------


@dataclass
class Usage:
    usd: float = 0.0  # billed API cost; 0 on the subscription
    searches: int = 0
    tokens: int = 0  # subscription only: tokens processed
    list_usd: float = 0.0  # subscription only: the API list price of the same calls
    limits: dict[str, float] = field(default_factory=dict)  # subscription utilisation, latest
    credential_expires_at: str | None = None  # subscription token, latest

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.usd + other.usd,
            self.searches + other.searches,
            self.tokens + other.tokens,
            self.list_usd + other.list_usd,
            other.limits or self.limits,
            other.credential_expires_at or self.credential_expires_at,
        )


class Llm(Protocol):
    def research(
        self, system: str, prompt: str, max_searches: int, effort: str
    ) -> tuple[str, Usage]: ...  # noqa: E501
    def parse(
        self, system: str, prompt: str, schema: type[BaseModel], effort: str
    ) -> tuple[Any, Usage]: ...  # noqa: E501


def usage_usd(model: str, usage: Any) -> Usage:
    price_in, price_out = PRICES.get(model, PRICES["claude-opus-5-5"])
    tokens_in = getattr(usage, "input_tokens", 0) or 0
    written = getattr(usage, "cache_creation_input_tokens", 0) or 0
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    tokens_out = getattr(usage, "output_tokens", 0) or 0
    server = getattr(usage, "server_tool_use", None)
    searches = (getattr(server, "web_search_requests", 0) or 0) if server else 0
    usd = (
        tokens_in * price_in + written * price_in * 1.25 + read * price_in * 0.05
        + tokens_out * price_out
    ) / 1_000_000 + searches * WEB_SEARCH_USD  # fmt: skip
    return Usage(usd, searches)


class ModelRefused(RuntimeError):
    pass


class AnthropicLlm:
    """Messages API with server-side refusal fallbacks and prompt caching."""

    BETAS = ["server-side-fallback-2026-07-01"]

    def __init__(self, model: str, client: Any | None = None) -> None:
        import anthropic

        self.model = model
        self.client = client or anthropic.Anthropic()

    def _system(self, system: str) -> list[dict[str, Any]]:
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    def research(
        self, system: str, prompt: str, max_searches: int, effort: str
    ) -> tuple[str, Usage]:  # noqa: E501
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches}]
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        total = Usage()
        for _ in range(MAX_CONTINUATIONS + 1):
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=self._system(system),
                messages=messages,
                tools=tools,
                output_config={"effort": effort},
                betas=self.BETAS,
                fallbacks="default",
            )
            total += usage_usd(self.model, response.usage)
            if response.stop_reason == "refusal":
                raise ModelRefused("the model declined the research request")
            if response.stop_reason != "pause_turn":
                break
            messages = [messages[0], {"role": "assistant", "content": response.content}]
        # cited passages arrive as separate text blocks of one paragraph: join without gaps
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        return text.strip(), total

    def parse(
        self, system: str, prompt: str, schema: type[BaseModel], effort: str
    ) -> tuple[Any, Usage]:  # noqa: E501
        # streamed: long structured answers exceed the SDK's non-streaming time limit
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=64000,
            system=self._system(system),
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
            output_config={"effort": effort},
            betas=self.BETAS,
            fallbacks="default",
        ) as stream:
            response = stream.get_final_message()
        if response.stop_reason == "refusal":
            raise ModelRefused("the model declined the request")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("the answer was cut off (max_tokens)")
        if response.parsed_output is None:
            raise RuntimeError("the answer did not match the expected format")
        return response.parsed_output, usage_usd(self.model, response.usage)


class ClaudeCodeLlm:
    """The same calls through Claude Code, billed to the owner's subscription.

    Research may use only web search and fetch; writing and reviewing get no tools and a
    JSON schema. A subscription limit blocks the ticket instead of failing it.
    """

    RETRIES = 1  # extra attempts when the structured answer does not validate

    def __init__(self, model: str, cli: ClaudeCode | None = None) -> None:
        self.model = model
        self.cli = cli or ClaudeCode()

    def _call(self, **kwargs: Any) -> CallResult:
        try:
            return self.cli.call(model=self.model, **kwargs)
        except SubscriptionLimit as exc:
            raise AgentBlocked(str(exc)) from exc
        except Refused as exc:
            raise ModelRefused(str(exc)) from exc

    @staticmethod
    def _usage(result: CallResult) -> Usage:
        return Usage(
            0.0,
            result.searches,
            result.tokens,
            result.list_usd,
            dict(result.rate_limit.utilization) if result.rate_limit else {},
            result.credential_expires_at,
        )

    def research(
        self, system: str, prompt: str, max_searches: int, effort: str
    ) -> tuple[str, Usage]:  # noqa: E501
        result = self._call(
            system=system,
            prompt=prompt,
            effort=effort,
            tools=WEB_TOOLS,
            max_turns=2 * max_searches + 4,  # searches plus a few page fetches
        )
        return result.text.strip(), self._usage(result)

    def parse(
        self, system: str, prompt: str, schema: type[BaseModel], effort: str
    ) -> tuple[Any, Usage]:  # noqa: E501
        total = Usage()
        ask = prompt
        for attempt in range(self.RETRIES + 1):
            result = self._call(
                system=system, prompt=ask, effort=effort, schema=schema.model_json_schema()
            )
            total += self._usage(result)
            try:
                if result.structured is None:
                    raise ValueError("no structured answer")
                return schema.model_validate(result.structured), total
            except (ValidationError, ValueError) as exc:
                problem = str(exc)
            log.warning("structured answer invalid (attempt %d): %s", attempt + 1, problem)
            ask = (
                f"{prompt}\n\nYour previous answer did not match the required format:\n"
                f"{problem[:2000]}\nAnswer again in the required format."
            )
        raise RuntimeError("the answer did not match the expected format")


@dataclass
class LlmSettings:
    eur_per_usd: float = 0.9
    claude_command: str = "claude"  # Claude Code, for billing: subscription
    claude_timeout: int = DEFAULT_TIMEOUT
    public_url: str = ""  # LifeOS for links in reports, e.g. to the review page


def default_llm(agent: AgentDef, settings: LlmSettings) -> Llm:
    """The model access the agent file asks for (``billing:``)."""
    if agent.billing == "subscription":
        cli = ClaudeCode(command=shlex.split(settings.claude_command),
                         timeout=settings.claude_timeout)  # fmt: skip
        return ClaudeCodeLlm(agent.model, cli)
    return AnthropicLlm(agent.model)
