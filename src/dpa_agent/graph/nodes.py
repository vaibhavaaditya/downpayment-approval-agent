"""LangGraph node functions.

Each node is a pure-ish function GraphState -> partial state update. Side effects
(loading data, writing audit lines) are injected, never imported from globals,
so tests can run the whole graph with fakes.
"""

from __future__ import annotations

from dpa_agent.audit import AuditLog
from dpa_agent.config import PolicyLimits
from dpa_agent.guardrails import apply_guardrails
from dpa_agent.llm.client import Recommender
from dpa_agent.models.state import GraphState
from dpa_agent.rag.store import PolicyStore
from dpa_agent.sap.client import SapClient
from dpa_agent.tools import run_all_checks
from dpa_agent.tools.context import ToolContext


def make_load_context(client: SapClient, limits: PolicyLimits):
    def load_context(state: GraphState) -> dict:
        request = state["request"]
        updates: dict = {}
        errors: list[str] = list(state.get("errors", []))
        if "vendor" not in state:
            try:
                updates["vendor"] = client.get_vendor(request.vendor_id)
            except Exception as exc:  # missing master data -> surfaced, not swallowed
                errors.append(f"vendor load failed: {exc}")
        if "po" not in state:
            try:
                updates["po"] = client.get_po(request.po_id)
            except Exception as exc:
                errors.append(f"PO load failed: {exc}")
        if "prior_payments" not in state:
            updates["prior_payments"] = client.list_prior_payments(
                request.vendor_id, request.po_id
            )
        if errors:
            updates["errors"] = errors
        return updates

    return load_context


def make_run_checks(limits: PolicyLimits, today=None):
    import datetime as _dt

    run_date = today or _dt.date.today()

    def run_checks(state: GraphState) -> dict:
        if "vendor" not in state or "po" not in state:
            # load_context already recorded the error; empty checks make the
            # guardrail refuse any approval (see guardrails rule 4).
            return {"checks": []}
        ctx = ToolContext(
            request=state["request"],
            vendor=state["vendor"],
            po=state["po"],
            prior_payments=state.get("prior_payments", []),
            limits=limits,
            today=run_date,
        )
        return {"checks": run_all_checks(ctx)}

    return run_checks


def make_retrieve_policy(store: PolicyStore, top_k: int = 4):
    def retrieve_policy(state: GraphState) -> dict:
        checks = state.get("checks", [])
        # Query from what the checks actually found — retrieval follows evidence.
        query_parts = [
            f"{c.check_name} {c.status.value} {c.summary}" for c in checks
        ]
        query_parts.append(f"down payment approval {state['request'].currency}")
        chunks = store.retrieve(" ".join(query_parts), top_k=top_k)
        return {"policy_chunks": chunks}

    return retrieve_policy


def skip_policy(state: GraphState) -> dict:
    return {"policy_chunks": []}


def make_propose(recommender: Recommender):
    def propose(state: GraphState) -> dict:
        try:
            draft = recommender.propose(state)
            return {"draft": draft}
        except Exception as exc:  # model/API failure degrades to guardrail default
            errors = list(state.get("errors", []))
            errors.append(f"recommender failed: {type(exc).__name__}: {exc}")
            return {"errors": errors}

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
