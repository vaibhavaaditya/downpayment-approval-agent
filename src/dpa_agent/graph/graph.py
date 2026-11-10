"""The LangGraph state machine for one down-payment evaluation.

    START -> load_context -> run_checks -> (retrieve_policy | skip) -> propose
           -> guardrail -> record_audit -> END

The single conditional edge exists because policy retrieval is only worth doing
when a check flagged something — passing requests cite the checks themselves.
Everything else is linear on purpose: fewer branch states = easier audit story.
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
    skip_policy,
)
from dpa_agent.llm.client import Recommender
from dpa_agent.models.checks import CheckStatus
from dpa_agent.models.state import GraphState
from dpa_agent.rag.store import PolicyStore
from dpa_agent.sap.client import SapClient


def _needs_policy(state: GraphState) -> str:
    checks = state.get("checks", [])
    if any(c.status is not CheckStatus.PASS for c in checks) or not checks:
        return "retrieve"
    return "skip"


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
    sg.add_node("skip_policy", skip_policy)
    sg.add_node("propose", make_propose(recommender))
    sg.add_node("guardrail", guardrail)
    sg.add_node("record_audit", make_record_audit(audit))

    sg.add_edge(START, "load_context")
    sg.add_edge("load_context", "run_checks")
    sg.add_conditional_edges(
        "run_checks",
        _needs_policy,
        {"retrieve": "retrieve_policy", "skip": "skip_policy"},
    )
    sg.add_edge("retrieve_policy", "propose")
    sg.add_edge("skip_policy", "propose")
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
    recommender = build_recommender(
        settings.llm_provider, settings.anthropic_api_key, settings.anthropic_model
    )
    store = PolicyStore.from_directory()
    audit = AuditLog(settings.audit_log_path)
    graph = build_graph(client, recommender, store, audit, limits=settings.limits)
    return graph, client, audit, settings
