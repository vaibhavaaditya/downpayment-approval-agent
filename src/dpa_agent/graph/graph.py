"""The LangGraph state machine for one down-payment evaluation.

    START -> load_context -(ok)-> run_checks -> retrieve_policy -> propose -> guardrail
                  |                                                              ^
                  +-(SAP data missing)-------------------------------------------+
    guardrail -> record_audit -> END

The single conditional edge routes a request whose SAP data could not be loaded straight
to the guardrail: there is nothing to check and no reason to spend a model call on it, and
the guardrail turns "incomplete evaluation" into HOLD. Everything else is linear on
purpose: fewer branch states = an easier audit story.
"""

from __future__ import annotations

from datetime import date

from langgraph.graph import END, START, StateGraph

from dpa_agent.audit import AuditLog
from dpa_agent.config import PolicyLimits, Settings
from dpa_agent.graph.nodes import (
    guardrail,
    make_load_context,
    make_propose,
    make_record_audit,
    make_retrieve_policy,
    make_run_checks,
)
from dpa_agent.llm.client import Recommender
from dpa_agent.models.state import GraphState
from dpa_agent.rag.store import PolicyStore
from dpa_agent.sap.client import SapClient


def _route_after_load(state: GraphState) -> str:
    incomplete = state.get("errors") or "vendor" not in state or "po" not in state
    return "error" if incomplete else "ok"


def build_graph(
    client: SapClient,
    recommender: Recommender,
    store: PolicyStore,
    audit: AuditLog,
    limits: PolicyLimits | None = None,
    today: date | None = None,
):
    limits = limits or PolicyLimits()
    sg: StateGraph = StateGraph(GraphState)

    sg.add_node("load_context", make_load_context(client, limits))
    sg.add_node("run_checks", make_run_checks(limits, today=today))
    sg.add_node("retrieve_policy", make_retrieve_policy(store))
    sg.add_node("propose", make_propose(recommender))
    sg.add_node("guardrail", guardrail)
    sg.add_node("record_audit", make_record_audit(audit))

    sg.add_edge(START, "load_context")
    sg.add_conditional_edges(
        "load_context",
        _route_after_load,
        {"ok": "run_checks", "error": "guardrail"},
    )
    sg.add_edge("run_checks", "retrieve_policy")
    sg.add_edge("retrieve_policy", "propose")
    sg.add_edge("propose", "guardrail")
    sg.add_edge("guardrail", "record_audit")
    sg.add_edge("record_audit", END)

    return sg.compile()


def evaluate(
    graph,
    request,
    vendor=None,
    po=None,
    prior_payments=None,
) -> dict:
    """Run the pipeline for one request and return the final state as a dict."""
    initial: GraphState = {"request": request}
    if vendor is not None:
        initial["vendor"] = vendor
    if po is not None:
        initial["po"] = po
    if prior_payments is not None:
        initial["prior_payments"] = prior_payments
    final_state = graph.invoke(initial)
    return dict(final_state)


def build_default(settings: Settings | None = None):
    """Wire the whole app together from settings — used by the API and evals."""
    from dpa_agent.llm.client import build_recommender
    from dpa_agent.sap.client import build_sap_client

    settings = settings or Settings.from_env()
    client = build_sap_client(settings.sap_base_url)
    store = PolicyStore.from_directory()
    recommender = build_recommender(
        settings.llm_provider, settings.anthropic_api_key, settings.anthropic_model, store=store
    )
    audit = AuditLog(settings.audit_log_path)
    graph = build_graph(client, recommender, store, audit, limits=settings.limits)
    return graph, client, audit, settings
