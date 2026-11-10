"""FastAPI surface.

Endpoints are *advisory* only: they produce recommendations and record human
decisions. There is deliberately no endpoint that posts, releases, or approves
a payment — the agent's write surface ends at the audit log.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from dpa_agent.graph.graph import build_default, evaluate
from dpa_agent.models.recommendation import HumanDecision, Label
from dpa_agent.models.request import DownPaymentRequest

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    graph, client, audit, settings = build_default()
    _state.update(graph=graph, client=client, audit=audit, settings=settings)
    yield
    _state.clear()


app = FastAPI(
    title="P2P Down-Payment Approval Agent",
    description="Recommendation-only agent for SAP FI-AP down-payment requests.",
    version="0.1.0",
    lifespan=lifespan,
)


class EvaluationResponse(BaseModel):
    request_id: str
    label: Label
    rationale: str
    citations: list[str]
    guardrail_actions: list[str]
    checks: list[dict]
    policy_clauses: list[str]
    errors: list[str] = Field(default_factory=list)


class HumanDecisionRequest(BaseModel):
    request_id: str
    decided_label: Label
    decided_by: str
    justification: str


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/api/v1/requests")
def list_requests() -> list[dict]:
    """Mock OData-style listing of pending down-payment requests."""
    return [
        r.model_dump(mode="json")
        for r in _state["client"].list_requests()
    ]


@app.get("/api/v1/requests/{request_id}")
def get_request(request_id: str) -> dict:
    try:
        return _state["client"].get_request(request_id).model_dump(mode="json")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown request {request_id}") from None


@app.post("/api/v1/requests/{request_id}/evaluate", response_model=EvaluationResponse)
def evaluate_request(request_id: str) -> EvaluationResponse:
    try:
        request = _state["client"].get_request(request_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown request {request_id}") from None
    return _run_evaluation(request)


@app.post("/api/v1/evaluate", response_model=EvaluationResponse)
def evaluate_payload(request: DownPaymentRequest) -> EvaluationResponse:
    """Evaluate an arbitrary payload (used by evals and integration tests)."""
    return _run_evaluation(request)


def _run_evaluation(request: DownPaymentRequest) -> EvaluationResponse:
    state = evaluate(_state["graph"], request)
    final = state["final"]
    return EvaluationResponse(
        request_id=request.request_id,
        label=final.label,
        rationale=final.rationale,
        citations=final.citations,
        guardrail_actions=final.guardrail_actions,
        checks=[c.model_dump(mode="json") for c in state.get("checks", [])],
        policy_clauses=[c.clause_id for c in state.get("policy_chunks", [])],
        errors=state.get("errors", []),
    )


@app.post("/api/v1/decisions")
def record_human_decision(req: HumanDecisionRequest) -> dict:
    """The human approver's decision — the agent never fills this in itself."""
    recs = [
        e
        for e in _state["audit"].read_all()
        if e.get("event") == "recommendation" and e.get("request_id") == req.request_id
    ]
    if not recs:
        raise HTTPException(
            status_code=409,
            detail="evaluate the request before recording a decision on it",
        )
    prior_label = Label(recs[-1]["final"]["label"])
    decision = HumanDecision(
        request_id=req.request_id,
        decided_label=req.decided_label,
        decided_by=req.decided_by,
        justification=req.justification,
        overrode_recommendation=req.decided_label is not prior_label,
    )
    _state["audit"].record_human_decision(decision)
    return decision.model_dump(mode="json")


@app.get("/api/v1/decisions")
def list_decisions() -> list[dict]:
    return [
        e
        for e in _state["audit"].read_all()
        if e.get("event") in {"recommendation", "human_decision"}
    ]


def create_app() -> FastAPI:
    return app
