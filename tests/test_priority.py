from __future__ import annotations

import json
from pathlib import Path

import pytest

from support_agent.analysis.priority import (
    PriorityMatrixError,
    load_matrix,
    priority_label,
    recommend_priority,
    validate_matrix,
)

EXPECTED = {
    ("High", "High"): 1,
    ("High", "Medium"): 2,
    ("High", "Low"): 3,
    ("Medium", "High"): 2,
    ("Medium", "Medium"): 3,
    ("Medium", "Low"): 4,
    ("Low", "High"): 3,
    ("Low", "Medium"): 4,
    ("Low", "Low"): 5,
}


@pytest.mark.parametrize(("impact", "urgency"), list(EXPECTED))
def test_default_matrix_matches_servicenow_lookup(impact: str, urgency: str) -> None:
    assert recommend_priority(impact, urgency, load_matrix()) == EXPECTED[(impact, urgency)]


def test_custom_matrix_file_overrides_default(tmp_path: Path) -> None:
    custom = {
        level: {u: 4 for u in ("High", "Medium", "Low")} for level in ("High", "Medium", "Low")
    }
    custom["High"]["High"] = 2
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(custom))

    matrix = load_matrix(path)

    assert recommend_priority("High", "High", matrix) == 2
    assert recommend_priority("Low", "Low", matrix) == 4


@pytest.mark.parametrize(
    "bad",
    [
        [],
        {"High": {"High": 1, "Medium": 2, "Low": 3}},  # missing rows
        {lvl: {"High": 1, "Medium": 2} for lvl in ("High", "Medium", "Low")},  # missing column
        {lvl: {"High": 0, "Medium": 2, "Low": 3} for lvl in ("High", "Medium", "Low")},
        {lvl: {"High": 6, "Medium": 2, "Low": 3} for lvl in ("High", "Medium", "Low")},
        {lvl: {"High": True, "Medium": 2, "Low": 3} for lvl in ("High", "Medium", "Low")},
        {lvl: {"High": "1", "Medium": 2, "Low": 3} for lvl in ("High", "Medium", "Low")},
    ],
)
def test_invalid_matrices_are_rejected(bad: object) -> None:
    with pytest.raises(PriorityMatrixError):
        validate_matrix(bad)


def test_unreadable_matrix_file(tmp_path: Path) -> None:
    path = tmp_path / "matrix.json"
    path.write_text("{not json")
    with pytest.raises(PriorityMatrixError, match="Could not read"):
        load_matrix(path)


def test_unknown_level_is_an_error() -> None:
    with pytest.raises(PriorityMatrixError):
        recommend_priority("Critical", "High", load_matrix())


def test_priority_label() -> None:
    assert priority_label(1) == "P1 - Critical"
    assert priority_label(3) == "P3 - Moderate"
