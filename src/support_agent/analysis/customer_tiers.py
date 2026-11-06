"""Deterministic customer-tier adjustment of the recommended priority.

The model never sees the tier and cannot change it. The customer is the incident's
Company reference field, which comes from the ServiceNow record, so ticket text such
as "I'm a premium customer" has no effect.

Customers are matched by company name, not sys_id: sys_ids differ per instance, and
this config is committed and must survive a PDI rebuild.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, model_validator

DEFAULT_TIERS_PATH = Path(__file__).with_name("customer_tiers.json")


class CustomerTierError(ValueError):
    pass


class TierRule(BaseModel):
    # How many levels to raise priority (P4 -> P3 is one level).
    priority_boost: int = Field(ge=0, le=4)
    # The lowest priority this tier may get after the boost (3 = never below P3).
    worst_priority: int = Field(default=5, ge=1, le=5)
    description: str = ""


class CustomerTiers(BaseModel):
    default_tier: str
    tiers: dict[str, TierRule]
    customers: dict[str, str] = Field(default_factory=dict)  # company name -> tier

    @model_validator(mode="after")
    def _tiers_exist(self) -> CustomerTiers:
        if self.default_tier not in self.tiers:
            raise ValueError(f"default_tier '{self.default_tier}' is not a defined tier")
        unknown = sorted({tier for tier in self.customers.values() if tier not in self.tiers})
        if unknown:
            raise ValueError(f"Customers reference undefined tiers: {', '.join(unknown)}")
        return self

    def tier_for(self, company: str) -> str:
        wanted = _normalize(company)
        if wanted:
            for name, tier in self.customers.items():
                if _normalize(name) == wanted:
                    return tier
        return self.default_tier


class PriorityAdjustment(BaseModel):
    """Audit record of the customer rule, kept in state and summarized in the work note."""

    customer: str  # company display name, "" if the incident has none
    tier: str
    base_priority: int
    priority: int
    rule: str  # human-readable explanation

    @property
    def changed(self) -> bool:
        return self.priority != self.base_priority


def _normalize(name: str) -> str:
    return " ".join(name.split()).casefold()


def load_customer_tiers(path: Path | None = None) -> CustomerTiers:
    target = path or DEFAULT_TIERS_PATH
    try:
        raw = json.loads(target.read_text())
    except (OSError, ValueError) as exc:
        raise CustomerTierError(f"Could not read customer tiers {target}: {exc}") from None
    try:
        return CustomerTiers.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in error['loc']) or 'file'}: {error['msg']}"
            for error in exc.errors()
        )
        raise CustomerTierError(f"Invalid customer tiers {target}: {details}") from None


def apply_customer_tier(
    base_priority: int, company: str, tiers: CustomerTiers
) -> PriorityAdjustment:
    tier = tiers.tier_for(company)
    rule = tiers.tiers[tier]
    # Lower number = higher priority. Boost first, then enforce the tier's floor, never above P1.
    priority = max(1, min(base_priority - rule.priority_boost, rule.worst_priority))
    who = f"{tier} customer {company}" if company else f"no company on incident, {tier} tier"
    if priority == base_priority:
        explanation = f"No change ({who})"
    else:
        explanation = f"Raised from P{base_priority} to P{priority} ({who})"
    return PriorityAdjustment(
        customer=company,
        tier=tier,
        base_priority=base_priority,
        priority=priority,
        rule=explanation,
    )
