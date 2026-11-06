from __future__ import annotations

from pathlib import Path

import pytest

from support_agent.config import ConfigError, load_agent_settings

AGENT_VARS = (
    "TRIAGE_MODEL",
    "TRIAGE_EFFORT",
    "LLM_TIMEOUT_SECONDS",
    "WRITE_TEST_INCIDENTS",
    "AGENT_KILL_SWITCH",
    "PRIORITY_MATRIX_PATH",
    "DEFAULT_RUN_MODE",
    "CUSTOMER_TIERS_PATH",
    "RETRIEVAL_DB_PATH",
    "RERANK_MODEL",
    "KB_RELEVANCE_THRESHOLD",
    "RETRIEVAL_SYNC_ON_RUN",
    "RETRIEVAL_DATABASE_URL",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSIONS",
    "LLM_BACKEND",
    "CLAUDE_CLI_PATH",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in AGENT_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_safe() -> None:
    settings = load_agent_settings(env_file=None)
    assert settings.triage_model == "claude-opus-5-5"
    assert settings.triage_effort == "medium"
    assert settings.write_test_incidents == frozenset()
    assert settings.kill_switch is False
    assert settings.priority_matrix_path is None
    assert settings.customer_tiers_path is None
    assert settings.default_run_mode == "dry_run"


def test_empty_placeholders_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRIAGE_MODEL", "")
    monkeypatch.setenv("TRIAGE_EFFORT", "")
    assert load_agent_settings(env_file=None).triage_model == "claude-opus-5-5"


def test_values_are_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRIAGE_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("TRIAGE_EFFORT", "high")
    monkeypatch.setenv("WRITE_TEST_INCIDENTS", " inc0010001, INC0010002 ,")
    monkeypatch.setenv("AGENT_KILL_SWITCH", "true")
    monkeypatch.setenv("PRIORITY_MATRIX_PATH", "config/priority.json")

    settings = load_agent_settings(env_file=None)

    assert settings.triage_model == "claude-haiku-5-5"
    assert settings.triage_effort == "high"
    assert settings.write_test_incidents == {"INC0010001", "INC0010002"}
    assert settings.kill_switch is True
    assert settings.priority_matrix_path == Path("config/priority.json")


def test_invalid_effort_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRIAGE_EFFORT", "extreme")
    with pytest.raises(ConfigError, match="TRIAGE_EFFORT"):
        load_agent_settings(env_file=None)


def test_default_run_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_RUN_MODE", "write")
    assert load_agent_settings(env_file=None).default_run_mode == "write"
    monkeypatch.setenv("DEFAULT_RUN_MODE", "always")
    with pytest.raises(ConfigError, match="DEFAULT_RUN_MODE"):
        load_agent_settings(env_file=None)


def test_customer_tiers_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOMER_TIERS_PATH", "config/tiers.json")
    assert load_agent_settings(env_file=None).customer_tiers_path == Path("config/tiers.json")


def test_retrieval_defaults() -> None:
    settings = load_agent_settings(env_file=None)
    assert settings.rerank_model == "claude-haiku-5-5"
    assert settings.relevance_threshold == 7
    assert settings.retrieval_sync_on_run is True
    assert settings.retrieval_db_path.name == "retrieval.db"


def test_retrieval_values_are_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KB_RELEVANCE_THRESHOLD", "5")
    monkeypatch.setenv("RETRIEVAL_SYNC_ON_RUN", "false")
    monkeypatch.setenv("RETRIEVAL_DB_PATH", "data/other.db")
    settings = load_agent_settings(env_file=None)
    assert settings.relevance_threshold == 5
    assert settings.retrieval_sync_on_run is False
    assert settings.retrieval_db_path == Path("data/other.db")


@pytest.mark.parametrize("value", ["11", "-1", "high"])
def test_invalid_threshold_is_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("KB_RELEVANCE_THRESHOLD", value)
    with pytest.raises(ConfigError, match="KB_RELEVANCE_THRESHOLD"):
        load_agent_settings(env_file=None)


def test_hybrid_index_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = load_agent_settings(env_file=None)
    assert defaults.retrieval_database_url == ""  # keyword-only SQLite index
    assert (defaults.embedding_model, defaults.embedding_dimensions) == ("voyage-4", 1024)

    url = "postgresql://support_agent:s3cret@127.0.0.1:5433/support_agent"
    monkeypatch.setenv("RETRIEVAL_DATABASE_URL", url)
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "512")
    settings = load_agent_settings(env_file=None)
    assert settings.retrieval_database_url == url
    assert settings.embedding_dimensions == 512
    assert "s3cret" not in repr(settings)


@pytest.mark.parametrize("value", ["1000", "abc", "-1"])
def test_invalid_embedding_dimensions_are_rejected(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", value)
    with pytest.raises(ConfigError, match="EMBEDDING_DIMENSIONS"):
        load_agent_settings(env_file=None)


def test_llm_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    assert load_agent_settings(env_file=None).llm_backend == "api"
    monkeypatch.setenv("LLM_BACKEND", "claude_cli")
    assert load_agent_settings(env_file=None).llm_backend == "claude_cli"
    monkeypatch.setenv("LLM_BACKEND", "openai")
    with pytest.raises(ConfigError, match="LLM_BACKEND"):
        load_agent_settings(env_file=None)
