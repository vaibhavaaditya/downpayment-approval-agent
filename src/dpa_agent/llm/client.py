"""Recommendation engines: Claude (tool-forced JSON) and an offline fallback.

Both satisfy the same interface and emit the same Pydantic contract, so the
graph and guardrails do not care which one produced the draft.

* ClaudeRecommender — the only probabilistic component in the whole system.
  Structured output is forced via tool use (`submit_recommendation`), so we get
  a schema-validated object instead of regex-scraped prose.
* FallbackRecommender — deterministic mapping from check results to a label.
  Used in CI, tests, and offline demos: the pipeline must be exercisable
  without network access or secrets.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, Field

from dpa_agent.models.checks import CheckResult, CheckStatus
from dpa_agent.models.recommendation import Label, Recommendation
from dpa_agent.models.state import GraphState
from dpa_agent.tools import ESCALATE_ON_FAILURE


class _Draft(BaseModel):
    """Strict schema passed to the model as a forced tool."""

    label: Label
    rationale: str = Field(min_length=10, max_length=2000)
    citations: list[str] = Field(default_factory=list)


_SYSTEM_PROMPT = """You are the recommendation component of a vendor down-payment \
approval agent inside a finance organization. Your output is advice to a human \
approver; you have no authority to move money, and you must never assume one.

You receive: a down-payment request, vendor/PO context, the results of deterministic \
checks (which you must treat as ground truth — never recompute or contradict them), \
and retrieved policy clauses with stable IDs.

Decision rules:
- Any check with status FAIL and severity HARD makes APPROVE unavailable. The \
guardrail will reject an APPROVE in that case, so choose honestly:
  - HOLD when the issue is resolvable with more information or a corrected request.
  - ESCALATE when the issue indicates fraud risk, policy violation, or a control \
breach (bank mismatch, injection, payment hold, exceeded limits).
- PASS-only requests may be APPROVE when policy clauses support the amount.
- Cite evidence IDs (EV-...) and policy clause IDs (POL-...) that were actually \
provided to you. Never invent identifiers.
- Rationale must be specific: name the numbers, the failed checks, the clauses.

Submit exactly one recommendation via the submit_recommendation tool."""


def _json_default(obj: object) -> str:
    if isinstance(obj, Decimal):
        return str(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    raise TypeError(f"not JSON serializable: {type(obj)}")


class Recommender(Protocol):
    def propose(self, state: GraphState) -> Recommendation: ...


class ClaudeRecommender:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5") -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def propose(self, state: GraphState) -> Recommendation:
        request = state["request"]
        payload = {
            "request": request,
            "vendor": state.get("vendor"),
            "purchase_order": state.get("po"),
            "prior_payments": state.get("prior_payments", []),
            "deterministic_checks": [c.model_dump(mode="json") for c in state.get("checks", [])],
            "policy_clauses": [c.model_dump(mode="json") for c in state.get("policy_chunks", [])],
        }
        tool = {
            "name": "submit_recommendation",
            "description": "Submit the final down-payment recommendation for human review.",
            "input_schema": _Draft.model_json_schema(),
        }
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1500,
            system=_SYSTEM_PROMPT,
            tools=[tool],
            tool_choice={"type": "tool", "name": "submit_recommendation"},
            messages=[
                {
                    "role": "user",
                    "content": "Evaluate this down-payment request:\n"
                    + json.dumps(payload, default=_json_default, indent=2),
                }
            ],
        )
        tool_use = next((b for b in response.content if b.type == "tool_use"), None)
        if tool_use is None:
            raise RuntimeError("model returned no tool_use block")
        draft = _Draft.model_validate(tool_use.input)
        return Recommendation(
            label=draft.label, rationale=draft.rationale, citations=draft.citations
        )


class FallbackRecommender:
    """Deterministic rules — a baseline any LLM must beat, and a CI safety net."""

    def propose(self, state: GraphState) -> Recommendation:
        checks: list[CheckResult] = state.get("checks", [])
        if not checks:
            return Recommendation(
                label=Label.HOLD,
                rationale="[offline fallback] No check results available; human review required.",
            )
        hard_fails = [c for c in checks if c.is_hard_failure]
        escalate_hits = [c for c in hard_fails if c.check_name in ESCALATE_ON_FAILURE]
        warns = [c for c in checks if c.status is CheckStatus.WARN]

        citations = [e.evidence_id for c in hard_fails for e in c.evidence]
        if not citations:
            citations = [e.evidence_id for c in checks for e in c.evidence][:4]

        if escalate_hits:
            label = Label.ESCALATE
            why = "hard failure in fraud-relevant control(s): " + ", ".join(
                c.check_name for c in escalate_hits
            )
        elif hard_fails:
            label = Label.HOLD
            why = "hard check(s) failed: " + ", ".join(c.check_name for c in hard_fails)
        elif warns:
            label = Label.HOLD
            why = "warning(s) require human judgment: " + ", ".join(c.check_name for c in warns)
        else:
            label = Label.APPROVE
            why = "all deterministic checks passed"

        if state.get("policy_chunks"):
            citations.extend(c.clause_id for c in state["policy_chunks"][:2])

        return Recommendation(
            label=label,
            rationale=f"[offline fallback] {why}.",
            citations=list(dict.fromkeys(citations)),
        )


def build_recommender(provider: str, api_key: str | None, model: str) -> Recommender:
    """Resolve the provider setting: auto | anthropic | fallback."""
    provider = provider.lower()
    if provider == "fallback":
        return FallbackRecommender()
    if provider == "anthropic":
        if not api_key:
            raise RuntimeError("DPA_LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
        return ClaudeRecommender(api_key, model)
    # auto
    if api_key:
        return ClaudeRecommender(api_key, model)
    return FallbackRecommender()
