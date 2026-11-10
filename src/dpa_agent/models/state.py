"""LangGraph state: a typed, serializable snapshot of one evaluation.

`total=False` because nodes fill the state incrementally (load_context first).
"""

from __future__ import annotations

from typing import TypedDict

from dpa_agent.models.checks import CheckResult
from dpa_agent.models.recommendation import Recommendation
from dpa_agent.models.request import DownPaymentRequest, PriorPayment, PurchaseOrder, Vendor
from dpa_agent.rag.store import PolicyChunk


class GraphState(TypedDict, total=False):
    request: DownPaymentRequest
    vendor: Vendor
    po: PurchaseOrder
    prior_payments: list[PriorPayment]
    checks: list[CheckResult]
    policy_chunks: list[PolicyChunk]
    draft: Recommendation        # model output, pre-guardrail
    final: Recommendation        # post-guardrail, what humans see
    errors: list[str]
