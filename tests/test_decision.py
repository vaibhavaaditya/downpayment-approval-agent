"""Human decision controls, validated against the audited recommendation."""

from __future__ import annotations

from dpa_agent.decision import DecisionError, validate_human_decision


def rec(agent_label="APPROVE", required="L2_FINANCE_CONTROLLER", requested_by="jdoe"):
    return {
        "request_summary": {"requested_by": requested_by},
        "required_approver": required,
        "final": {"label": agent_label},
    }


def attempt(entry, **kw):
    args = {"decided_by": "alice", "approver_role": "L2_FINANCE_CONTROLLER",
            "decided_label": "APPROVE", "override_reason": None}
    args.update(kw)
    try:
        validate_human_decision(entry, **args)
    except DecisionError as exc:
        return exc.code
    return None


def test_valid_approval_passes():
    assert attempt(rec()) is None


def test_requester_cannot_decide_their_own_request():
    assert attempt(rec(), decided_by="jdoe") == "four_eyes"
    assert attempt(rec(), decided_by="jdoe", decided_label="HOLD") == "four_eyes"


def test_unknown_role_is_rejected():
    assert attempt(rec(), approver_role="INTERN") == "unknown_role"


def test_approval_needs_sufficient_authority():
    assert attempt(rec(), approver_role="L1_FINANCE_MANAGER") == "authority"
    assert attempt(rec(), approver_role="L3_CFO_DELEGATE") is None


def test_approving_against_the_agent_needs_a_written_reason():
    held = rec(agent_label="HOLD")
    assert attempt(held) == "override_reason"
    assert attempt(held, override_reason="   ") == "override_reason"
    assert attempt(held, override_reason="Buyer confirmed milestone 1 by phone, ticket 4411") is None


def test_holding_or_escalating_never_needs_authority_or_a_reason():
    low = {"approver_role": "L1_FINANCE_MANAGER"}
    assert attempt(rec(agent_label="APPROVE"), decided_label="HOLD", **low) is None
    assert attempt(rec(agent_label="HOLD"), decided_label="ESCALATE", **low) is None


def test_missing_required_approver_defaults_to_the_top_role():
    incomplete = rec(agent_label="HOLD", required=None)
    assert attempt(incomplete, override_reason="x") == "authority"
    assert attempt(incomplete, approver_role="L3_CFO_DELEGATE", override_reason="x") is None
