"""Approval authority: who may approve how much.

Deterministic, like every other control. The approval matrix lives in config so the
policy numbers are reviewable in one place.
"""

from __future__ import annotations

from decimal import Decimal

from dpa_agent.config import PolicyLimits

ROLE_LEVELS: dict[str, int] = {
    "L1_FINANCE_MANAGER": 1,
    "L2_FINANCE_CONTROLLER": 2,
    "L3_CFO_DELEGATE": 3,
}


def required_approver(amount: Decimal, currency: str, limits: PolicyLimits) -> str:
    """Lowest role allowed to approve `amount`; unknown currencies need the top role."""
    bands = limits.approval_matrix.get(currency)
    if bands is None:
        return limits.top_role
    for upper, role in bands:
        if amount <= upper:
            return role
    return limits.top_role
