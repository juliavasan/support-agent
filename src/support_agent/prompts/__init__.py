"""Versioned prompt files, loaded at run time."""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text()
