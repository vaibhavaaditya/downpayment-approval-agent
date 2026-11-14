"""Human decision rules: the agent advises, a person decides — under explicit controls.

Validated against the *audited* recommendation (not against anything the caller claims),
so authority and four-eyes checks cannot be bypassed by editing a request payload.
"""

from __future__ import annotations

from typing import Any

from dpa_agent.authority import ROLE_LEVELS

TOP_ROLE = "L3_CFO_DELEGATE"


class DecisionError(Exception):
    """A human decision that violates the approval controls."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_human_decision(
    rec_entry: dict[str, Any],
    *,
    decided_by: str,
    approver_role: str,
    decided_label: str,
    override_reason: str | None,
) -> None:
    summary = rec_entry.get("request_summary") or {}
    if decided_by == summary.get("requested_by"):
        raise DecisionError(
            "four_eyes", "four-eyes principle: the requester cannot decide on their own request"
        )
    if approver_role not in ROLE_LEVELS:
        raise DecisionError("unknown_role", f"unknown approver role: {approver_role!r}")

    if str(decided_label) != "APPROVE":
        return  # holding or escalating never needs special authority

    required = rec_entry.get("required_approver") or TOP_ROLE
    if ROLE_LEVELS[approver_role] < ROLE_LEVELS.get(required, ROLE_LEVELS[TOP_ROLE]):
        raise DecisionError(
            "authority", f"approval requires {required}; approver has {approver_role}"
        )
    agent_label = (rec_entry.get("final") or {}).get("label")
    if agent_label != "APPROVE" and not (override_reason or "").strip():
        raise DecisionError(
            "override_reason",
            "approving against the agent's recommendation requires a written override reason",
        )
