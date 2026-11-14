"""LangGraph node functions.

Each node is a pure-ish function GraphState -> partial state update. Side effects
(loading data, writing audit lines) are injected, never imported from globals,
so tests can run the whole graph with fakes.
"""

from __future__ import annotations

from dpa_agent.audit import AuditLog
from dpa_agent.authority import required_approver
from dpa_agent.config import PolicyLimits
from dpa_agent.guardrails import apply_guardrails
from dpa_agent.llm.client import PROMPT_VERSION, Recommender
from dpa_agent.models.checks import CheckStatus
from dpa_agent.models.state import GraphState
from dpa_agent.rag.store import PolicyStore
from dpa_agent.sap.client import SapClient
from dpa_agent.tools import CHECK_TO_CLAUSE, run_all_checks
from dpa_agent.tools.context import ToolContext

#: Always shown to the model, whatever the checks found. The security clauses matter most
#: exactly when every check passes: a subtle injection leaves no failed check to point at it.
ALWAYS_CLAUSES: tuple[str, ...] = ("POL-SEC-001", "POL-SEC-004", "POL-PAY-007")
MAX_CLAUSES = 8


def make_load_context(client: SapClient, limits: PolicyLimits):
    def load_context(state: GraphState) -> dict:
        request = state["request"]
        updates: dict = {}
        errors: list[str] = list(state.get("errors", []))

        po = state.get("po")
        if po is None:
            try:
                po = client.get_po(request.po_id)
                updates["po"] = po
            except Exception as exc:  # missing master data -> surfaced, not swallowed
                errors.append(f"PO load failed: {exc}")

        # The PO's vendor is the reference for bank data and risk tier. A request naming a
        # different vendor is not silently "fixed": the vendor_po_match check fails on it.
        vendor_id = po.vendor_id if po is not None else request.vendor_id
        if "vendor" not in state:
            try:
                updates["vendor"] = client.get_vendor(vendor_id)
            except Exception as exc:
                errors.append(f"vendor load failed: {exc}")
        if "prior_payments" not in state:
            try:
                updates["prior_payments"] = client.list_prior_payments(vendor_id, request.po_id)
            except Exception as exc:
                errors.append(f"payment history load failed: {exc}")

        if errors:
            updates["errors"] = errors
        return updates

    return load_context


def make_run_checks(limits: PolicyLimits, today=None):
    def run_checks(state: GraphState) -> dict:
        request = state["request"]
        if "vendor" not in state or "po" not in state:
            # Defensive: the graph routes load failures straight to the guardrail.
            return {"checks": []}
        ctx = ToolContext(
            request=request,
            vendor=state["vendor"],
            po=state["po"],
            prior_payments=state.get("prior_payments", []),
            limits=limits,
            # Evaluate "as of" the request, so replays and evals are deterministic.
            today=today or request.requested_on,
        )
        return {
            "checks": run_all_checks(ctx),
            "required_approver": required_approver(request.amount, request.currency, limits),
        }

    return run_checks


def make_retrieve_policy(store: PolicyStore, max_clauses: int = MAX_CLAUSES):
    def retrieve_policy(state: GraphState) -> dict:
        request, checks = state["request"], state.get("checks", [])
        ids: list[str] = list(ALWAYS_CLAUSES)
        # clauses that explain the checks which did not pass
        for c in checks:
            clause = CHECK_TO_CLAUSE.get(c.check_name)
            if c.status is not CheckStatus.PASS and clause and clause not in ids:
                ids.append(clause)
        chunks = [ch for ch in (store.get(i) for i in ids) if ch is not None]

        # then lexical matches on what the checks found and what the vendor wrote
        query = " ".join(
            [f"{c.check_name} {c.status.value} {c.summary}" for c in checks]
            + [f"down payment approval {request.currency}", request.purpose or ""]
            + [(request.vendor_document_text or "")[:500]]
        )
        have = {c.clause_id for c in chunks}
        for ch in store.retrieve(query, top_k=max_clauses):
            if len(chunks) >= max_clauses:
                break
            if ch.clause_id not in have:
                chunks.append(ch)
                have.add(ch.clause_id)
        return {"policy_chunks": chunks}

    return retrieve_policy


def make_propose(recommender: Recommender):
    def propose(state: GraphState) -> dict:
        try:
            proposal = recommender.propose(state)
        except Exception as exc:  # model/API failure degrades to guardrail default
            errors = list(state.get("errors", []))
            errors.append(f"recommender failed: {type(exc).__name__}: {exc}")
            return {"errors": errors}

        updates: dict = {
            "draft": proposal.draft,
            "model_meta": {
                "model": proposal.model,
                "prompt_version": PROMPT_VERSION,
                "usage": proposal.usage,
            },
        }
        if proposal.extra_chunks:  # clauses the model fetched itself become citable
            have = {c.clause_id for c in state.get("policy_chunks", [])}
            updates["policy_chunks"] = [
                *state.get("policy_chunks", []),
                *[c for c in proposal.extra_chunks if c.clause_id not in have],
            ]
        return updates

    return propose


def guardrail(state: GraphState) -> dict:
    draft = state.get("draft")
    final = apply_guardrails(draft, state)
    return {"final": final}


def make_record_audit(audit: AuditLog):
    def record_audit(state: GraphState) -> dict:
        try:
            audit.record_evaluation(state)
        except Exception as exc:
            errors = list(state.get("errors", []))
            errors.append(f"audit write failed: {exc}")
            return {"errors": errors}
        return {}

    return record_audit
