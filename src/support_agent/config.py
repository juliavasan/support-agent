"""Typed settings loaded from environment variables (and .env, if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast, get_args

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


@dataclass(frozen=True)
class ServiceNowSettings:
    instance_url: str
    username: str
    # repr=False keeps the password out of logs, tracebacks, and LangSmith traces.
    password: str = field(repr=False)
    timeout_seconds: float = 30.0


DEFAULT_TRIAGE_MODEL = "claude-opus-5-5"
# Reranking is a short scoring task; the small model is accurate enough and ~40x cheaper.
DEFAULT_RERANK_MODEL = "claude-haiku-5-5"
DEFAULT_RELEVANCE_THRESHOLD = 7  # rerank score 0-10; below it a hit is dropped
DEFAULT_RETRIEVAL_DB = PROJECT_ROOT / "data" / "retrieval.db"
DEFAULT_EMBEDDING_MODEL = "voyage-4"
DEFAULT_EMBEDDING_DIMENSIONS = 1024
EMBEDDING_DIMENSIONS = (256, 512, 1024, 2048)  # what Voyage 4 models can output
Effort = Literal["low", "medium", "high", "xhigh", "max"]
DEFAULT_TRIAGE_EFFORT: Effort = "medium"
TRIAGE_EFFORT_LEVELS: tuple[str, ...] = get_args(Effort)
LLMBackend = Literal["api", "claude_cli"]
LLM_BACKENDS: tuple[str, ...] = get_args(LLMBackend)
RunMode = Literal["dry_run", "write"]
RUN_MODES: tuple[str, ...] = get_args(RunMode)
TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AgentSettings:
    """LLM and write-safety settings. The Anthropic API key is read by the SDK itself."""

    triage_model: str = DEFAULT_TRIAGE_MODEL
    triage_effort: Effort = DEFAULT_TRIAGE_EFFORT
    llm_timeout_seconds: float = 120.0
    # "api": Anthropic API (ANTHROPIC_API_KEY). "claude_cli": `claude -p` on the signed-in
    # Claude subscription, for local development (see llm.py).
    llm_backend: LLMBackend = "api"
    claude_cli_path: str = "claude"
    # Incident numbers that write mode may touch. Empty means write mode is refused.
    write_test_incidents: frozenset[str] = frozenset()
    kill_switch: bool = False
    priority_matrix_path: Path | None = None
    customer_tiers_path: Path | None = None
    # Used when a run doesn't set `mode` (e.g. LangSmith Studio with the field left empty).
    # The CLI always sets mode explicitly, so it stays dry-run unless --write is given.
    default_run_mode: RunMode = "dry_run"
    # Retrieval (KB, known issues, similar incidents).
    retrieval_db_path: Path = DEFAULT_RETRIEVAL_DB
    # Set → hybrid index in Postgres + pgvector. Empty → keyword-only SQLite index.
    # repr=False: the URL contains the database password.
    retrieval_database_url: str = field(default="", repr=False)
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS
    rerank_model: str = DEFAULT_RERANK_MODEL
    relevance_threshold: int = DEFAULT_RELEVANCE_THRESHOLD
    # Incremental sync before each run, so Studio and the CLI always see fresh data.
    retrieval_sync_on_run: bool = True


def _load_env_file(env_file: Path | None) -> None:
    if env_file is not None and env_file.exists():
        load_dotenv(env_file, override=False)


def load_agent_settings(env_file: Path | None = PROJECT_ROOT / ".env") -> AgentSettings:
    """Read agent settings. All values are optional and fall back to safe defaults."""
    _load_env_file(env_file)

    effort = os.environ.get("TRIAGE_EFFORT") or DEFAULT_TRIAGE_EFFORT
    if effort not in TRIAGE_EFFORT_LEVELS:
        raise ConfigError(f"TRIAGE_EFFORT must be one of {', '.join(TRIAGE_EFFORT_LEVELS)}")

    backend = os.environ.get("LLM_BACKEND") or "api"
    if backend not in LLM_BACKENDS:
        raise ConfigError(f"LLM_BACKEND must be one of {', '.join(LLM_BACKENDS)}")

    run_mode = os.environ.get("DEFAULT_RUN_MODE") or "dry_run"
    if run_mode not in RUN_MODES:
        raise ConfigError(f"DEFAULT_RUN_MODE must be one of {', '.join(RUN_MODES)}")

    raw_threshold = os.environ.get("KB_RELEVANCE_THRESHOLD") or str(DEFAULT_RELEVANCE_THRESHOLD)
    threshold = int(raw_threshold) if raw_threshold.strip().isdigit() else -1
    if not 0 <= threshold <= 10:
        raise ConfigError("KB_RELEVANCE_THRESHOLD must be an integer from 0 to 10")
    db_path = os.environ.get("RETRIEVAL_DB_PATH")
    raw_dimensions = os.environ.get("EMBEDDING_DIMENSIONS") or str(DEFAULT_EMBEDDING_DIMENSIONS)
    dimensions = int(raw_dimensions) if raw_dimensions.strip().isdigit() else -1
    if dimensions not in EMBEDDING_DIMENSIONS:
        raise ConfigError(
            "EMBEDDING_DIMENSIONS must be one of " + ", ".join(map(str, EMBEDDING_DIMENSIONS))
        )
    sync_on_run = os.environ.get("RETRIEVAL_SYNC_ON_RUN", "true").strip().lower()

    incidents = os.environ.get("WRITE_TEST_INCIDENTS", "")
    matrix_path = os.environ.get("PRIORITY_MATRIX_PATH")
    tiers_path = os.environ.get("CUSTOMER_TIERS_PATH")
    return AgentSettings(
        triage_model=os.environ.get("TRIAGE_MODEL") or DEFAULT_TRIAGE_MODEL,
        triage_effort=cast(Effort, effort),
        llm_timeout_seconds=float(os.environ.get("LLM_TIMEOUT_SECONDS") or "120"),
        llm_backend=cast(LLMBackend, backend),
        claude_cli_path=os.environ.get("CLAUDE_CLI_PATH") or "claude",
        write_test_incidents=frozenset(
            number.strip().upper() for number in incidents.split(",") if number.strip()
        ),
        kill_switch=os.environ.get("AGENT_KILL_SWITCH", "").strip().lower() in TRUE_VALUES,
        priority_matrix_path=Path(matrix_path) if matrix_path else None,
        customer_tiers_path=Path(tiers_path) if tiers_path else None,
        default_run_mode=cast(RunMode, run_mode),
        retrieval_db_path=Path(db_path) if db_path else DEFAULT_RETRIEVAL_DB,
        retrieval_database_url=os.environ.get("RETRIEVAL_DATABASE_URL", "").strip(),
        embedding_model=os.environ.get("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL,
        embedding_dimensions=dimensions,
        rerank_model=os.environ.get("RERANK_MODEL") or DEFAULT_RERANK_MODEL,
        relevance_threshold=threshold,
        retrieval_sync_on_run=sync_on_run in TRUE_VALUES or sync_on_run == "",
    )


def load_servicenow_settings(env_file: Path | None = PROJECT_ROOT / ".env") -> ServiceNowSettings:
    """Read ServiceNow settings. Existing environment variables win over .env values."""
    _load_env_file(env_file)

    names = ("SERVICENOW_INSTANCE_URL", "SERVICENOW_USERNAME", "SERVICENOW_PASSWORD")
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")

    return ServiceNowSettings(
        instance_url=os.environ["SERVICENOW_INSTANCE_URL"].rstrip("/"),
        username=os.environ["SERVICENOW_USERNAME"],
        password=os.environ["SERVICENOW_PASSWORD"],
        timeout_seconds=float(os.environ.get("SERVICENOW_TIMEOUT_SECONDS", "30")),
    )
