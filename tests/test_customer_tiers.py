from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from support_agent.analysis.customer_tiers import (
    CustomerTierError,
    CustomerTiers,
    apply_customer_tier,
    load_customer_tiers,
)

PREMIUM = "Cobalt Ridge Manufacturing"


def tiers(**overrides: Any) -> CustomerTiers:
    raw: dict[str, Any] = {
        "default_tier": "standard",
        "tiers": {
            "premium": {"priority_boost": 1, "worst_priority": 3},
            "standard": {"priority_boost": 0},
        },
        "customers": {PREMIUM: "premium"},
    }
    raw.update(overrides)
    return CustomerTiers.model_validate(raw)


def test_bundled_config_marks_the_demo_customer_premium() -> None:
    config = load_customer_tiers()
    assert config.tier_for(PREMIUM) == "premium"
    assert config.tier_for("Brightline Supplies") == "standard"


@pytest.mark.parametrize(
    ("base", "expected"),
    [(5, 3), (4, 3), (3, 2), (2, 1), (1, 1)],
)
def test_premium_is_raised_one_level_never_below_p3_never_above_p1(
    base: int, expected: int
) -> None:
    adjustment = apply_customer_tier(base, PREMIUM, tiers())
    assert adjustment.tier == "premium"
    assert adjustment.base_priority == base
    assert adjustment.priority == expected


@pytest.mark.parametrize("base", [1, 2, 3, 4, 5])
def test_standard_customer_keeps_matrix_priority(base: int) -> None:
    adjustment = apply_customer_tier(base, "Harborview Foods", tiers())
    assert adjustment.tier == "standard"
    assert adjustment.priority == base
    assert not adjustment.changed
    assert adjustment.rule.startswith("No change")


def test_missing_company_uses_default_tier() -> None:
    adjustment = apply_customer_tier(4, "", tiers())
    assert adjustment.tier == "standard"
    assert adjustment.priority == 4
    assert "no company on incident" in adjustment.rule


def test_company_match_ignores_case_and_extra_spaces() -> None:
    assert tiers().tier_for("  cobalt   RIDGE manufacturing ") == "premium"


def test_partial_name_does_not_match() -> None:
    assert tiers().tier_for("Cobalt Ridge") == "standard"


def test_rule_explains_the_change() -> None:
    adjustment = apply_customer_tier(4, PREMIUM, tiers())
    assert adjustment.changed
    assert adjustment.rule == f"Raised from P4 to P3 (premium customer {PREMIUM})"


def test_undefined_default_tier_is_rejected() -> None:
    with pytest.raises(ValueError, match="default_tier 'gold'"):
        tiers(default_tier="gold")


def test_customer_with_undefined_tier_is_rejected() -> None:
    with pytest.raises(ValueError, match="undefined tiers: platinum"):
        tiers(customers={PREMIUM: "platinum"})


def test_invalid_file_raises_customer_tier_error(tmp_path: Path) -> None:
    path = tmp_path / "tiers.json"
    path.write_text(json.dumps({"default_tier": "standard", "tiers": {}}))
    with pytest.raises(CustomerTierError, match="Invalid customer tiers"):
        load_customer_tiers(path)


def test_out_of_range_boost_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "tiers.json"
    path.write_text(
        json.dumps({"default_tier": "x", "tiers": {"x": {"priority_boost": 9}}, "customers": {}})
    )
    with pytest.raises(CustomerTierError, match="priority_boost"):
        load_customer_tiers(path)


def test_unreadable_file_raises_customer_tier_error(tmp_path: Path) -> None:
    with pytest.raises(CustomerTierError, match="Could not read"):
        load_customer_tiers(tmp_path / "missing.json")
