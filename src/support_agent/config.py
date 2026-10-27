"""Typed settings loaded from environment variables (and .env, if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

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


def load_servicenow_settings(env_file: Path | None = PROJECT_ROOT / ".env") -> ServiceNowSettings:
    """Read ServiceNow settings. Existing environment variables win over .env values."""
    if env_file is not None and env_file.exists():
        load_dotenv(env_file, override=False)

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
