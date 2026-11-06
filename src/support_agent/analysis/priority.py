"""Recommended priority from impact and urgency, using a configurable lookup matrix."""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from support_agent.analysis.models import Level

DEFAULT_MATRIX_PATH = Path(__file__).with_name("priority_matrix.json")
LEVELS: tuple[str, ...] = get_args(Level)
PRIORITY_LABELS = {1: "Critical", 2: "High", 3: "Moderate", 4: "Low", 5: "Planning"}

PriorityMatrix = dict[str, dict[str, int]]


class PriorityMatrixError(ValueError):
    pass


def validate_matrix(raw: object) -> PriorityMatrix:
    """Require every impact x urgency combination to map to a priority from 1 to 5."""
    if not isinstance(raw, dict):
        raise PriorityMatrixError("Priority matrix must be a JSON object")
    matrix: PriorityMatrix = {}
    for impact in LEVELS:
        row = raw.get(impact)
        if not isinstance(row, dict):
            raise PriorityMatrixError(f"Priority matrix is missing impact row '{impact}'")
        matrix[impact] = {}
        for urgency in LEVELS:
            value = row.get(urgency)
            # bool is a subclass of int, so exclude it explicitly.
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
                raise PriorityMatrixError(
                    f"Priority for impact={impact}, urgency={urgency} must be an integer 1-5"
                )
            matrix[impact][urgency] = value
    return matrix


def load_matrix(path: Path | None = None) -> PriorityMatrix:
    target = path or DEFAULT_MATRIX_PATH
    try:
        raw = json.loads(target.read_text())
    except (OSError, ValueError) as exc:
        raise PriorityMatrixError(f"Could not read priority matrix {target}: {exc}") from None
    return validate_matrix(raw)


def recommend_priority(impact: str, urgency: str, matrix: PriorityMatrix) -> int:
    try:
        return matrix[impact][urgency]
    except KeyError:
        raise PriorityMatrixError(f"No priority for impact={impact}, urgency={urgency}") from None


def priority_label(priority: int) -> str:
    return f"P{priority} - {PRIORITY_LABELS.get(priority, 'Unknown')}"
