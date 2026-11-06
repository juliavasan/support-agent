"""LLM backends: the Claude CLI adapter (fake subprocess) and backend selection."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from support_agent.config import AgentSettings
from support_agent.llm import AnthropicLLM, ClaudeCliLLM, LLMError, default_llm


class Answer(BaseModel):
    score: int
    reason: str


def cli_output(**overrides: Any) -> str:
    values: dict[str, Any] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": '{"score": 3, "reason": "r"}',
        "structured_output": {"score": 3, "reason": "r"},
        "usage": {
            "input_tokens": 2,
            "cache_creation_input_tokens": 600,
            "cache_read_input_tokens": 100,
            "output_tokens": 40,
        },
        "modelUsage": {"claude-haiku-5-5": {}},
    }
    values.update(overrides)
    return json.dumps(values)


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    seen: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["command"], seen["kwargs"] = command, kwargs
        seen["system"] = Path(command[command.index("--system-prompt-file") + 1]).read_text()
        return subprocess.CompletedProcess(command, 0, fake.stdout, "")

    fake = MagicMock(side_effect=fake_run, stdout=cli_output(), seen=seen)
    monkeypatch.setattr("support_agent.llm.subprocess.run", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-reach-the-cli")
    return fake


def call(llm: ClaudeCliLLM) -> Any:
    return llm.parse(
        model="claude-haiku-5-5",
        system="Score it.",
        user="<incident>ticket text</incident>",
        output_format=Answer,
        max_tokens=1000,
        effort="low",
    )


def test_cli_call_is_tool_free_isolated_and_uses_the_subscription(run: MagicMock) -> None:
    result = call(ClaudeCliLLM("claude", timeout_seconds=60))

    assert result.output == Answer(score=3, reason="r")
    assert (result.stop_reason, result.model) == ("end_turn", "claude-haiku-5-5")
    assert (result.input_tokens, result.output_tokens) == (702, 40)

    command, kwargs = run.seen["command"], run.seen["kwargs"]
    assert command[:2] == ["claude", "-p"]
    assert command[command.index("--tools") + 1] == ""  # no tools at all
    assert "--strict-mcp-config" in command
    assert command[command.index("--model") + 1] == "claude-haiku-5-5"
    assert command[command.index("--effort") + 1] == "low"
    assert json.loads(command[command.index("--json-schema") + 1])["required"] == [
        "score",
        "reason",
    ]
    # Untrusted text goes in on stdin, never in argv.
    assert kwargs["input"] == "<incident>ticket text</incident>"
    assert not any("ticket text" in part for part in command)
    assert run.seen["system"] == "Score it."
    # Subscription auth, an empty working directory, and a timeout.
    assert "ANTHROPIC_API_KEY" not in kwargs["env"]
    assert "support-agent-llm-" in kwargs["cwd"]
    assert kwargs["timeout"] == 60


@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        (cli_output(is_error=True, subtype="success", result="Usage limit reached"), "limit"),
        (cli_output(subtype="error_max_structured_output_retries"), "error_max_structured"),
        (cli_output(structured_output={"score": "high"}), "schema validation"),
        ("not json", "no JSON result"),
    ],
)
def test_cli_failures_become_llm_errors(run: MagicMock, stdout: str, message: str) -> None:
    run.stdout = stdout
    with pytest.raises(LLMError, match=message):
        call(ClaudeCliLLM())


def test_missing_cli_and_timeout_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "support_agent.llm.subprocess.run", MagicMock(side_effect=FileNotFoundError)
    )
    with pytest.raises(LLMError, match="not found"):
        call(ClaudeCliLLM("/nope/claude"))
    monkeypatch.setattr(
        "support_agent.llm.subprocess.run",
        MagicMock(side_effect=subprocess.TimeoutExpired("claude", 5)),
    )
    with pytest.raises(LLMError, match="timed out"):
        call(ClaudeCliLLM(timeout_seconds=5))


def test_backend_selection() -> None:
    assert isinstance(default_llm(AgentSettings(llm_backend="claude_cli")), ClaudeCliLLM)
    assert isinstance(default_llm(AgentSettings(), client=MagicMock()), AnthropicLLM)
    # An injected client (tests) always means the API path.
    injected = default_llm(AgentSettings(llm_backend="claude_cli"), client=MagicMock())
    assert isinstance(injected, AnthropicLLM)
