"""Shared context passed to every deterministic check.

Checks are pure functions over this context — no network, no model calls.
That is what makes their results trustworthy and unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from dpa_agent.config import PolicyLimits
from dpa_agent.models.request import DownPaymentRequest, PriorPayment, PurchaseOrder, Vendor


@dataclass(frozen=True)
class ToolContext:
    request: DownPaymentRequest
    vendor: Vendor
    po: PurchaseOrder
    prior_payments: list[PriorPayment]
    limits: PolicyLimits
    today: date
