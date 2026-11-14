"""The Claude tool loop, run through the real graph with a scripted fake client.

No API key, no network: the fake plays the model. What is under test is everything around
the model: what it is shown, what its tools may do, how its output is validated, and how the
guardrails treat it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

from dpa_agent.audit import AuditLog
from dpa_agent.config import PolicyLimits
from dpa_agent.graph.graph import build_graph, evaluate
from dpa_agent.llm.client import ClaudeRecommender
from dpa_agent.models.recommendation import Label
from dpa_agent.rag.store import PolicyStore
from dpa_agent.sap.client import MemorySapClient


def tool_use(name, args, id_="tu_1"):
    return NS(type="tool_use", name=name, input=args, id=id_)


def reply(*blocks):
    return NS(content=list(blocks), usage=NS(input_tokens=100, output_tokens=20))


class FakeAnthropic:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.messages = NS(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        reply_ = self.replies.pop(0)
        if isinstance(reply_, Exception):
            raise reply_
        return reply_


def submit(label="HOLD", citations=("POL-SEC-001",), injection=False, rationale="Specific, cited reasoning."):
    return tool_use("submit_recommendation", {
        "label": label, "rationale": rationale, "citations": list(citations),
        "injection_suspected": injection, "risk_flags": ["flag"], "questions_for_vendor": ["Which milestone?"],
    }, "tu_submit")


def run(replies, request_id="DPR-2026-0001", tmp_path=None, max_rounds=3):
    store = PolicyStore.from_directory()
    fake = FakeAnthropic(replies)
    recommender = ClaudeRecommender(store=store, client=fake, model="test-model", max_tool_rounds=max_rounds)
    graph = build_graph(client=MemorySapClient(), recommender=recommender, store=store,
                        audit=AuditLog(Path(tmp_path) / "a.jsonl"), limits=PolicyLimits())
    state = evaluate(graph, MemorySapClient().get_request(request_id))
    return state, fake


def test_a_direct_submission_becomes_the_draft(tmp_path):
    state, fake = run([reply(submit("APPROVE", ["EV-BANK-1"]))], tmp_path=tmp_path)
    assert state["draft"].label is Label.APPROVE and state["final"].label is Label.APPROVE
    assert state["model_meta"]["model"] == "test-model"
    assert state["model_meta"]["usage"]["calls"] == 1
    assert fake.calls[0]["tool_choice"] == {"type": "any"}


def test_model_sees_masked_bank_data_and_delimited_vendor_text(tmp_path):
    state, fake = run([reply(submit("HOLD"))], request_id="DPR-2026-0002", tmp_path=tmp_path)
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "DE89 3704 0044 0532 0130 00" not in prompt and "DE89370400440532013000" not in prompt
    assert "47110815" not in prompt and "****3000" in prompt
    assert "<vendor_document>" in prompt and "</vendor_document>" in prompt
    system = fake.calls[0]["system"]
    assert "untrusted" in system.lower() and "injection_suspected" in system
    assert "POL-SEC-001" in prompt and "POL-SEC-004" in prompt  # security clauses are always shown


def test_a_policy_clause_fetched_by_a_tool_becomes_citable(tmp_path):
    state, fake = run([
        reply(tool_use("search_policy", {"query": "approval authority four-eyes override"}, "tu_a")),
        reply(submit("APPROVE", ["POL-GOV-001"])),
    ], tmp_path=tmp_path)
    followup = fake.calls[1]["messages"]
    assert followup[-1]["role"] == "user" and followup[-1]["content"][0]["tool_use_id"] == "tu_a"
    assert "POL-GOV-001" in {c.clause_id for c in state["policy_chunks"]}
    assert "POL-GOV-001" in state["final"].citations  # accepted: it was really retrieved


def test_invented_citations_are_dropped_by_the_guardrail(tmp_path):
    state, _ = run([reply(submit("HOLD", ["POL-MADE-UP-9", "POL-SEC-001"]))], tmp_path=tmp_path)
    assert "POL-MADE-UP-9" not in state["final"].citations
    assert any("dropped unknown citations" in a for a in state["final"].guardrail_actions)


def test_payment_history_tool_is_scoped_to_the_po_vendor(tmp_path):
    state, fake = run([
        reply(tool_use("get_payment_history", {"vendor_id": "V-1001"}, "tu_a")),  # not this PO's vendor
        reply(submit("APPROVE", ["EV-BANK-1"])),
    ], tmp_path=tmp_path)
    assert "not permitted" in fake.calls[1]["messages"][-1]["content"][0]["content"]


def test_model_flagging_an_injection_forces_escalation(tmp_path):
    # the subtle-injection demo request passes every deterministic check
    state, _ = run([reply(submit("APPROVE", ["EV-BANK-1"], injection=True))],
                   request_id="DPR-2026-0005", tmp_path=tmp_path)
    assert all(c.status.value == "PASS" for c in state["checks"])
    assert state["final"].label is Label.ESCALATE and state["final"].injection_suspected
    assert any("model flagged" in a for a in state["final"].guardrail_actions)


def test_a_model_that_approves_the_subtle_injection_is_not_stopped_by_code(tmp_path):
    """The honest limit of the design: with every check passing, only the model can catch this.
    That is exactly why the eval suite measures it instead of assuming it."""
    state, _ = run([reply(submit("APPROVE", ["EV-BANK-1"]))], request_id="DPR-2026-0005", tmp_path=tmp_path)
    assert state["final"].label is Label.APPROVE


def test_invalid_model_output_fails_closed(tmp_path):
    bad = reply(tool_use("submit_recommendation", {"label": "APPROVE"}, "tu_x"))  # no rationale
    state, _ = run([bad], tmp_path=tmp_path)
    assert state["final"].label is Label.HOLD and state.get("errors")


def test_api_failure_fails_closed(tmp_path):
    state, _ = run([RuntimeError("API down")], tmp_path=tmp_path)
    assert state["final"].label is Label.HOLD
    assert any("recommender failed" in e for e in state["errors"])


def test_no_tool_call_fails_closed(tmp_path):
    state, _ = run([reply(NS(type="text", text="I approve."))], tmp_path=tmp_path)
    assert state["final"].label is Label.HOLD


def test_tool_loop_is_bounded_and_the_last_round_forces_submission(tmp_path):
    looping = [reply(tool_use("search_policy", {"query": "x"}, f"tu_{i}")) for i in range(3)]
    state, fake = run(looping + [reply(submit("HOLD"))], tmp_path=tmp_path, max_rounds=3)
    assert len(fake.calls) == 4 and state["final"].label is Label.HOLD
    assert fake.calls[-1]["tool_choice"] == {"type": "tool", "name": "submit_recommendation"}
    assert all(c["tool_choice"] == {"type": "any"} for c in fake.calls[:-1])


def test_a_model_that_never_submits_fails_closed(tmp_path):
    looping = [reply(tool_use("search_policy", {"query": "x"}, f"tu_{i}")) for i in range(4)]
    state, fake = run(looping, tmp_path=tmp_path, max_rounds=3)
    assert state["final"].label is Label.HOLD and len(fake.calls) == 4
