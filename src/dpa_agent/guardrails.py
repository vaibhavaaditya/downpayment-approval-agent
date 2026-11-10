"""The guardrail layer — why an unsafe approval is *impossible*, not unlikely.

Runs after the model, entirely in code, over Pydantic-validated inputs:

1. Citation hygiene  — drop citations that don't exist in the evidence pool;
                       if a hard-failed check ends up uncited, force its evidence in.
2. Label authority    — HARD failure ⇒ APPROVE is unavailable (cap to HOLD);
                       bank mismatch / injection ⇒ force ESCALATE;
                       these overrides *always* win over the model.
3. Action transparency — every mutation is recorded in `guardrail_actions` so the
                       audit log shows exactly what code changed and why.

Defense in depth: even if the model is jailbroken, it can only ever emit
APPROVE / HOLD / ESCALATE, and the first rule strips any attempt to smuggle
instructions through the rationale or citations.
"""

from __future__ import annotations

from dpa_agent.models.checks import CheckResult, CheckStatus
from dpa_agent.models.recommendation import Label, Recommendation
from dpa_agent.models.state import GraphState
from dpa_agent.tools import ESCALATE_ON_FAILURE


class GuardrailError(Exception):
    """Raised when a draft cannot be salvaged into a valid recommendation."""


def evidence_pool(state: GraphState) -> set[str]:
    ids = {
        e.evidence_id
        for c in state.get("checks", [])
        for e in c.evidence
    }
    ids |= {c.clause_id for c in state.get("policy_chunks", [])}
    return ids


def apply_guardrails(draft: Recommendation | None, state: GraphState) -> Recommendation:
    checks: list[CheckResult] = state.get("checks", [])
    pool = evidence_pool(state)
    actions: list[str] = []

    if draft is None:
        draft = Recommendation(
            label=Label.HOLD,
            rationale="No model output produced; defaulting to HOLD pending human review.",
        )
        actions.append("draft missing -> HOLD default")

    # 1. Citation hygiene
    kept = [c for c in draft.citations if c in pool]
    dropped = [c for c in draft.citations if c not in pool]
    if dropped:
        actions.append(f"dropped unknown citations: {', '.join(dropped)}")

    hard_fails = [c for c in checks if c.is_hard_failure]
    failed_ids = {e.evidence_id for c in hard_fails for e in c.evidence}
    missing = failed_ids - set(kept)
    if missing and hard_fails:
        kept.extend(sorted(missing))
        actions.append(f"force-cited failed checks: {', '.join(sorted(missing))}")
    draft_citations = list(dict.fromkeys(kept))

    # 2. Label authority
    label = draft.label
    escalate_hits = [c for c in hard_fails if c.check_name in ESCALATE_ON_FAILURE]
    if escalate_hits:
        forced = ", ".join(c.check_name for c in escalate_hits)
        if label is not Label.ESCALATE:
            actions.append(f"label {label.value} -> ESCALATE (hard failure: {forced})")
        label = Label.ESCALATE
    elif hard_fails and label is Label.APPROVE:
        actions.append("label APPROVE -> HOLD (HARD check failure present)")
        label = Label.HOLD

    # 3. Soft warnings can never be approved silently either (WARN is not PASS)
    if label is Label.APPROVE and any(c.status is CheckStatus.WARN for c in checks):
        warns = ", ".join(c.check_name for c in checks if c.status is CheckStatus.WARN)
        actions.append(f"label APPROVE -> HOLD (unresolved warning: {warns})")
        label = Label.HOLD

    # 4. Incomplete evaluation is never an approval: missing checks or load
    #    errors mean we do not actually know the facts.
    if label is Label.APPROVE and (state.get("errors") or not checks):
        actions.append("label APPROVE -> HOLD (incomplete evaluation: checks missing or errors)")
        label = Label.HOLD

    # Keep only meaningful reasons; strip anything that smells like an instruction
    # smuggled through the rationale (belt-and-braces beside POL-SEC-001).
    rationale = draft.rationale.strip()
    if not rationale:
        rationale = "No rationale provided by the recommender; human review required."
        actions.append("empty rationale -> neutral text")

    return Recommendation(
        label=label,
        rationale=rationale,
        citations=draft_citations,
        guardrail_actions=actions,
    )
