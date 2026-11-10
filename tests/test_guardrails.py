"""Guardrail tests: the whole point is that the model cannot buy an approval."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from dpa_agent.guardrails import apply_guardrails, evidence_pool
from dpa_agent.models.checks import CheckResult, CheckStatus, Evidence, Severity
from dpa_agent.models.recommendation import Label, Recommendation
from dpa_agent.models.request import DownPaymentRequest, PurchaseOrder, Vendor
from dpa_agent.models.state import GraphState
from dpa_agent.rag.store import PolicyChunk


def _state(checks, chunks=None, errors=None) -> GraphState:
    st: GraphState = {
        "request": DownPaymentRequest(
            request_id="DPR-T", vendor_id="V", po_id="PO", amount=Decimal("1"),
            currency="EUR", requested_by="t", requested_on=date(2026, 11, 1),
        ),
        "vendor": Vendor(vendor_id="V", name="v"),
        "po": PurchaseOrder(po_id="PO", vendor_id="V", net_value=Decimal("100")),
        "checks": checks,
    }
    if chunks:
        st["policy_chunks"] = chunks
    if errors:
        st["errors"] = errors
    return st


def _fail(name="payment_limits", evidence_id="EV-X-1") -> CheckResult:
    return CheckResult(
        check_name=name,
        status=CheckStatus.FAIL,
        severity=Severity.HARD,
        summary="failed",
        evidence=[Evidence(evidence_id=evidence_id, ref_type="po", ref_id="PO", detail="d")],
    )


def _pass(name="payment_limits") -> CheckResult:
    return CheckResult(
        check_name=name, status=CheckStatus.PASS, severity=Severity.HARD, summary="ok"
    )


def test_hard_failure_caps_approve_to_hold():
    draft = Recommendation(label=Label.APPROVE, rationale="looks fine", citations=["EV-X-1"])
    final = apply_guardrails(draft, _state([_fail()]))
    assert final.label is Label.HOLD
    assert any("APPROVE -> HOLD" in a for a in final.guardrail_actions)


def test_bank_mismatch_forces_escalate_even_if_model_says_approve():
    draft = Recommendation(label=Label.APPROVE, rationale="trust me", citations=[])
    fail = _fail(name="bank_detail_match", evidence_id="EV-BANK-1")
    final = apply_guardrails(draft, _state([fail]))
    assert final.label is Label.ESCALATE
    assert any("ESCALATE" in a for a in final.guardrail_actions)


def test_injection_forces_escalate():
    draft = Recommendation(label=Label.HOLD, rationale="suspicious", citations=[])
    final = apply_guardrails(draft, _state([_fail(name="injection_scan", evidence_id="EV-INJ-1")]))
    assert final.label is Label.ESCALATE


def test_unknown_citations_are_dropped():
    draft = Recommendation(
        label=Label.HOLD, rationale="r", citations=["EV-REAL-1", "FAKE-CITATION-999"]
    )
    final = apply_guardrails(draft, _state([_fail(evidence_id="EV-REAL-1")]))
    assert "FAKE-CITATION-999" not in final.citations
    assert "EV-REAL-1" in final.citations
    assert any("dropped unknown citations" in a for a in final.guardrail_actions)


def test_failed_check_evidence_is_force_cited():
    draft = Recommendation(label=Label.HOLD, rationale="r", citations=["POL-NOPE"])
    final = apply_guardrails(draft, _state([_fail(evidence_id="EV-DUP-1")]))
    assert "EV-DUP-1" in final.citations


def test_policy_clause_ids_are_valid_citations():
    chunk = PolicyChunk(clause_id="POL-PAY-001", title="cap", text="20%")
    draft = Recommendation(label=Label.APPROVE, rationale="r", citations=["POL-PAY-001"])
    final = apply_guardrails(draft, _state([_pass()], chunks=[chunk]))
    assert final.label is Label.APPROVE
    assert "POL-PAY-001" in final.citations
    assert evidence_pool(_state([_pass()], chunks=[chunk])) == {"POL-PAY-001"} | {
        e.evidence_id for e in _pass().evidence
    }


def test_warn_blocks_silent_approval():
    warn = CheckResult(
        check_name="vendor_history", status=CheckStatus.WARN,
        severity=Severity.SOFT, summary="low otd",
        evidence=[Evidence(evidence_id="EV-VEND-1", ref_type="vendor", ref_id="V", detail="d")],
    )
    draft = Recommendation(label=Label.APPROVE, rationale="r", citations=["EV-VEND-1"])
    final = apply_guardrails(draft, _state([_pass(), warn]))
    assert final.label is Label.HOLD


def test_missing_draft_defaults_to_hold():
    final = apply_guardrails(None, _state([_pass()]))
    assert final.label is Label.HOLD
    assert final.rationale


def test_incomplete_evaluation_never_approves():
    draft = Recommendation(label=Label.APPROVE, rationale="r", citations=[])
    final = apply_guardrails(draft, _state([], errors=["vendor load failed"]))
    assert final.label is Label.HOLD


def test_clean_request_passes_through():
    passed = CheckResult(
        check_name="payment_limits", status=CheckStatus.PASS, severity=Severity.HARD,
        summary="ok",
        evidence=[Evidence(evidence_id="EV-A", ref_type="po", ref_id="PO", detail="d")],
    )
    draft = Recommendation(label=Label.APPROVE, rationale="all green", citations=["EV-A"])
    final = apply_guardrails(draft, _state([passed]))
    assert final.label is Label.APPROVE
    assert final.citations == ["EV-A"]
