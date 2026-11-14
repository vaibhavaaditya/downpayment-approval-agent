"""Recommendation contract: the only thing this agent is allowed to produce."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Label(StrEnum):
    APPROVE = "APPROVE"
    HOLD = "HOLD"
    ESCALATE = "ESCALATE"


class Recommendation(BaseModel):
    label: Label
    rationale: str
    citations: list[str] = Field(default_factory=list)  # evidence IDs + policy clause IDs
    guardrail_actions: list[str] = Field(default_factory=list)  # what code changed/validated
    injection_suspected: bool = False  # the model saw instructions aimed at the reviewer
    risk_flags: list[str] = Field(default_factory=list)
    questions_for_vendor: list[str] = Field(default_factory=list)


class HumanDecision(BaseModel):
    """What the human approver did with the recommendation (the agent never decides)."""

    request_id: str
    decided_label: Label
    decided_by: str
    justification: str
    approver_role: str = ""
    override_reason: str | None = None
    overrode_recommendation: bool = False
