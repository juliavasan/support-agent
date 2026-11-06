"""Structured-output LLM calls behind one small interface, with two backends:

  - "api" (default): the Anthropic API via the SDK, billed to ANTHROPIC_API_KEY.
    The production path.
  - "claude_cli": the Claude Code CLI in headless mode (`claude -p`), billed to the
    signed-in Claude subscription. For local development without API credits.

Both return the same StructuredResult, so the analyzer and reranker don't know which
one ran. Failures become LLMError with a short, credential-free message.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from support_agent.config import AgentSettings, Effort

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """A model call failed. Messages never contain credentials or request bodies."""


@dataclass
class StructuredResult(Generic[T]):
    output: T | None
    stop_reason: str  # "end_turn", "refusal", "max_tokens", ...
    model: str
    input_tokens: int
    output_tokens: int
    refusal_category: str | None = None


class StructuredLLM(Protocol):
    def parse(
        self,
        *,
        model: str,
        system: str,
        user: str,
        output_format: type[T],
        max_tokens: int,
        effort: Effort,
        api_options: dict[str, Any] | None = None,
    ) -> StructuredResult[T]: ...


@contextmanager
def anthropic_errors() -> Iterator[None]:
    """Re-raise SDK and schema-validation failures as LLMError, without tracebacks that
    could carry request details."""
    try:
        yield
    except anthropic.AuthenticationError:
        raise LLMError("Anthropic authentication failed: set ANTHROPIC_API_KEY in .env") from None
    except anthropic.RateLimitError:
        raise LLMError("Anthropic rate limit reached (after retries)") from None
    except anthropic.BadRequestError as exc:
        raise LLMError(f"Anthropic rejected the request: {exc.message}") from None
    except anthropic.APIStatusError as exc:
        raise LLMError(f"Anthropic API error (HTTP {exc.status_code})") from None
    except anthropic.APIConnectionError:
        raise LLMError("Could not reach the Anthropic API") from None
    except ValidationError as exc:
        raise LLMError(
            f"Model output failed schema validation ({exc.error_count()} errors)"
        ) from None


class AnthropicLLM:
    def __init__(self, client: anthropic.Anthropic):
        self._client = client

    def parse(
        self,
        *,
        model: str,
        system: str,
        user: str,
        output_format: type[T],
        max_tokens: int,
        effort: Effort,
        api_options: dict[str, Any] | None = None,
    ) -> StructuredResult[T]:
        with anthropic_errors():
            response = self._client.beta.messages.parse(
                model=model,
                max_tokens=max_tokens,
                **(api_options or {}),
                output_config={"effort": effort},
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=output_format,
            )
        details = getattr(response, "stop_details", None)
        return StructuredResult(
            output=response.parsed_output,
            stop_reason=str(response.stop_reason),
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            refusal_category=getattr(details, "category", None) if details else None,
        )


class ClaudeCliLLM:
    """`claude -p` with structured output, every tool disabled, and no project context.

    - `--tools ""` and `--strict-mcp-config`: the model can only answer. Ticket text is
      untrusted, so it must not be able to steer a session that can run commands.
    - Runs in an empty temporary directory, so no CLAUDE.md or project settings are
      discovered and added to the prompt.
    - ANTHROPIC_API_KEY is removed from the child environment, so the CLI uses the
      subscription login instead of the API key.
    - The prompt goes in on stdin and the system prompt in a file, never in argv
      (visible to other local processes).
    """

    def __init__(self, executable: str = "claude", timeout_seconds: float = 300.0):
        self._executable = executable
        self._timeout = timeout_seconds

    def parse(
        self,
        *,
        model: str,
        system: str,
        user: str,
        output_format: type[T],
        max_tokens: int,
        effort: Effort,
        api_options: dict[str, Any] | None = None,  # API-only features; not applicable
    ) -> StructuredResult[T]:
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with tempfile.TemporaryDirectory(prefix="support-agent-llm-") as workdir:
            system_file = Path(workdir) / "system.md"
            system_file.write_text(system)
            command = [
                self._executable,
                "-p",
                "--output-format",
                "json",
                "--json-schema",
                json.dumps(output_format.model_json_schema()),
                "--system-prompt-file",
                str(system_file),
                "--tools",
                "",
                "--strict-mcp-config",
                "--no-session-persistence",
                "--model",
                model,
                "--effort",
                effort,
            ]
            try:
                completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                    command,
                    input=user,
                    capture_output=True,
                    text=True,
                    cwd=workdir,
                    env=env,
                    timeout=self._timeout,
                    check=False,
                )
            except FileNotFoundError:
                raise LLMError(f"Claude CLI not found: {self._executable}") from None
            except subprocess.TimeoutExpired:
                raise LLMError(f"Claude CLI timed out after {self._timeout:.0f}s") from None
        return _parse_cli_result(completed, output_format, model)


def _parse_cli_result(
    completed: subprocess.CompletedProcess[str], output_format: type[T], model: str
) -> StructuredResult[T]:
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise LLMError(
            f"Claude CLI exited with code {completed.returncode} and no JSON result"
            + (f": {completed.stderr.strip()[:200]}" if completed.stderr.strip() else "")
        ) from None
    if data.get("is_error") or data.get("subtype") != "success":
        # The result text of an error is the CLI's own message (auth, usage limit).
        detail = str(data.get("result") or data.get("api_error_status") or "")[:200]
        raise LLMError(f"Claude CLI failed ({data.get('subtype')}): {detail}")
    usage = data.get("usage") or {}
    structured = data.get("structured_output")
    try:
        output = output_format.model_validate(structured) if structured is not None else None
    except ValidationError as exc:
        raise LLMError(
            f"Model output failed schema validation ({exc.error_count()} errors)"
        ) from None
    used_models = list((data.get("modelUsage") or {}).keys())
    return StructuredResult(
        output=output,
        stop_reason="end_turn",
        model=used_models[0] if used_models else model,
        input_tokens=int(usage.get("input_tokens", 0))
        + int(usage.get("cache_creation_input_tokens", 0))
        + int(usage.get("cache_read_input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
    )


def default_llm(
    settings: AgentSettings, client: anthropic.Anthropic | None = None
) -> StructuredLLM:
    """The configured backend. An injected client (tests) always means the API path."""
    if client is None and settings.llm_backend == "claude_cli":
        return ClaudeCliLLM(settings.claude_cli_path, settings.llm_timeout_seconds)
    return AnthropicLLM(
        client or anthropic.Anthropic(timeout=settings.llm_timeout_seconds, max_retries=2)
    )
