"""HTTP surface (FastAPI TestClient, offline fallback recommender).

Skipped where FastAPI / httpx are not installed. The lifespan is not run: state is injected, so
the app is exercised exactly as the endpoints see it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from dpa_agent.api import main as api  # noqa: E402
from dpa_agent.audit import AuditLog  # noqa: E402
from dpa_agent.config import PolicyLimits  # noqa: E402
from dpa_agent.graph.graph import build_graph  # noqa: E402
from dpa_agent.llm.client import FallbackRecommender  # noqa: E402
from dpa_agent.rag.store import PolicyStore  # noqa: E402
from dpa_agent.sap.client import MemorySapClient  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    sap, audit = MemorySapClient(), AuditLog(tmp_path / "audit.jsonl")
    graph = build_graph(client=sap, recommender=FallbackRecommender(),
                        store=PolicyStore.from_directory(), audit=audit, limits=PolicyLimits())
    api._state.update(graph=graph, client=sap, audit=audit, settings=None)
    yield TestClient(api.app)
    api._state.clear()


def _decide(client, request_id, **over):
    body = {"request_id": request_id, "decided_label": "APPROVE", "decided_by": "alice",
            "approver_role": "L2_FINANCE_CONTROLLER", "justification": "reviewed"}
    body.update(over)
    return client.post("/api/v1/decisions", json=body)


def test_health_and_listing(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert len(client.get("/api/v1/requests").json()) == 6
    assert client.get("/api/v1/requests/NOPE").status_code == 404


def test_evaluate_returns_label_required_approver_and_flags(client):
    clean = client.post("/api/v1/requests/DPR-2026-0001/evaluate").json()
    assert clean["label"] == "APPROVE" and clean["required_approver"] == "L1_FINANCE_MANAGER"
    attack = client.post("/api/v1/requests/DPR-2026-0003/evaluate").json()
    assert attack["label"] == "ESCALATE" and attack["guardrail_actions"] is not None


def test_decision_requires_a_prior_evaluation(client):
    assert _decide(client, "DPR-2026-0001").status_code == 409


def test_requester_cannot_approve_their_own_request(client):
    client.post("/api/v1/requests/DPR-2026-0001/evaluate")  # requested_by jdoe
    r = _decide(client, "DPR-2026-0001", decided_by="jdoe")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "four_eyes"


def test_approval_authority_and_override_reason_are_enforced(client):
    client.post("/api/v1/requests/DPR-2026-0002/evaluate")  # duplicate -> not APPROVE
    r = _decide(client, "DPR-2026-0002", approver_role="L3_CFO_DELEGATE")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "override_reason"
    ok = _decide(client, "DPR-2026-0002", approver_role="L3_CFO_DELEGATE",
                 override_reason="Second milestone confirmed by the buyer, ticket 4411")
    assert ok.status_code == 200 and ok.json()["overrode_recommendation"] is True


def test_audit_chain_verifies_after_activity(client):
    client.post("/api/v1/requests/DPR-2026-0001/evaluate")
    _decide(client, "DPR-2026-0001")
    assert client.get("/api/v1/audit/verify").json() == {"ok": True, "first_bad_seq": None}
    assert [e["event"] for e in client.get("/api/v1/decisions").json()] == ["recommendation", "human_decision"]
