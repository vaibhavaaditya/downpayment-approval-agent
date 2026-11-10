"""Structured output of deterministic checks — the evidence the model may cite."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class CheckStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class Severity(StrEnum):
    """HARD failures cap the recommendation; WARN feeds the model but caps nothing."""

    HARD = "HARD"
    SOFT = "SOFT"


class Evidence(BaseModel):
    """A citable pointer into the underlying data (never free-form LLM text)."""

    evidence_id: str                 # e.g. "EV-DUP-1"
    ref_type: str                    # payment | po | vendor | request
    ref_id: str
    detail: str


class CheckResult(BaseModel):
    check_name: str
    status: CheckStatus
    severity: Severity
    summary: str
    evidence: list[Evidence] = Field(default_factory=list)
    metrics: dict[str, str | int | float] = Field(default_factory=dict)

    @property
    def is_hard_failure(self) -> bool:
        return self.status is CheckStatus.FAIL and self.severity is Severity.HARD
