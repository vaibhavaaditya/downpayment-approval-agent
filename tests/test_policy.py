"""Policy documents, check-to-clause mapping and approval bands must stay consistent."""

from __future__ import annotations

from decimal import Decimal

from dpa_agent.authority import ROLE_LEVELS, required_approver
from dpa_agent.config import PolicyLimits
from dpa_agent.graph.nodes import ALWAYS_CLAUSES
from dpa_agent.rag.store import PolicyStore
from dpa_agent.tools import CHECK_REGISTRY, CHECK_TO_CLAUSE


def test_every_check_maps_to_a_clause_that_exists():
    store = PolicyStore.from_directory()
    assert set(CHECK_REGISTRY) == set(CHECK_TO_CLAUSE)
    for check, clause in CHECK_TO_CLAUSE.items():
        assert store.get(clause) is not None, f"{check} -> missing clause {clause}"


def test_always_shown_clauses_exist_and_clause_ids_are_unique():
    store = PolicyStore.from_directory()
    assert all(store.get(c) for c in ALWAYS_CLAUSES)
    ids = [c.clause_id for c in store.chunks]
    assert len(ids) == len(set(ids))


def test_policy_text_matches_the_numbers_in_code():
    limits, store = PolicyLimits(), PolicyStore.from_directory()
    assert f"{int(limits.max_dp_percent)}%" in store.get("POL-PAY-001").text
    assert f"{int(limits.absolute_request_cap):,}" in store.get("POL-PAY-002").text
    assert str(limits.duplicate_window_days) in store.get("POL-PAY-003").text
    gov = store.get("POL-GOV-001").text
    for bound, _ in limits.approval_matrix["EUR"] + limits.approval_matrix["INR"]:
        assert f"{int(bound):,}" in gov, f"approval band {bound} missing from POL-GOV-001"


def test_retrieval_finds_the_clause_a_reviewer_would_look_for():
    store = PolicyStore.from_directory()
    assert store.retrieve("vendor claims pre-clearance record low risk", 2)[0].clause_id == "POL-SEC-004"
    assert store.retrieve("executive pressure threat cancel supply", 2)[0].clause_id == "POL-PAY-007"


def test_required_approver_bands_and_unknown_currency():
    limits = PolicyLimits()
    assert required_approver(Decimal("10000"), "EUR", limits) == "L1_FINANCE_MANAGER"
    assert required_approver(Decimal("10000.01"), "EUR", limits) == "L2_FINANCE_CONTROLLER"
    assert required_approver(Decimal("50000.01"), "USD", limits) == "L3_CFO_DELEGATE"
    assert required_approver(Decimal("4000000"), "INR", limits) == "L2_FINANCE_CONTROLLER"
    assert required_approver(Decimal("1"), "JPY", limits) == "L3_CFO_DELEGATE"  # unknown: top role
    assert set(ROLE_LEVELS) >= {"L1_FINANCE_MANAGER", "L2_FINANCE_CONTROLLER", "L3_CFO_DELEGATE"}
