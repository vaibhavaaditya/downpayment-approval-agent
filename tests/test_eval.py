"""Eval gates.

The suite has two kinds of cases: rule-detectable ones (the deterministic checks and guardrails
must get every one right) and `llm_only` ones (every check passes; only a model reading the
vendor text can get them right). These tests pin down both halves of that claim.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "evals"))

from run_eval import load_cases, make_graph, run_suite, to_markdown  # noqa: E402

from dpa_agent.llm.client import Proposal  # noqa: E402
from dpa_agent.models.recommendation import Label, Recommendation  # noqa: E402


def test_suite_has_the_expected_shape():
    cases = load_cases()
    assert len(cases) >= 40
    assert sum(1 for c in cases if c["llm_only"]) >= 8
    assert len({c["id"] for c in cases}) == len(cases)
    assert {"APPROVE"} == {c["expected"] for c in cases if c["category"].startswith("clean_")}


def test_baseline_handles_every_rule_detectable_case():
    summary = run_suite(provider="fallback")
    wrong = [r["id"] for r in summary["results"] if not r["llm_only"] and not r["correct"]]
    assert wrong == [], f"deterministic layer missed: {wrong}"
    assert summary["over_escalations"] == 0


def test_baseline_is_blind_to_exactly_the_llm_only_cases():
    """The offline baseline must approve every LLM-only case. If it stops doing so, the case no
    longer measures what the model adds and needs rewriting."""
    summary = run_suite(provider="fallback")
    llm_only = [r for r in summary["results"] if r["llm_only"]]
    assert llm_only and all(r["label"] == "APPROVE" and r["unsafe"] for r in llm_only)
    assert summary["unsafe_approvals"] == len(llm_only)


class _AlwaysApprove:
    """A misbehaving model: approves everything, with a plausible citation."""

    def propose(self, state):
        return Proposal(
            draft=Recommendation(label=Label.APPROVE, rationale="looks fine to me", citations=[]),
            model="always-approve",
        )


def test_guardrails_contain_a_model_that_always_approves(tmp_path):
    """Even a model that approves everything cannot get an unsafe approval past the guardrails
    on any case the deterministic checks can see."""
    summary = run_suite(graph=make_graph(_AlwaysApprove(), tmp_path))
    leaked = [r["id"] for r in summary["results"] if r["unsafe"] and not r["llm_only"]]
    assert leaked == [], f"guardrails let these through: {leaked}"
    assert summary["guardrail_overrules"] >= summary["rule_detectable_total"] - 8


class _Exploding:
    def propose(self, state):
        raise RuntimeError("API down")


def test_a_model_outage_is_reported_not_mistaken_for_a_safe_run(tmp_path):
    """If every model call fails, everything degrades to HOLD: zero unsafe approvals, but the run
    measures nothing. The eval must say so loudly instead of looking perfect."""
    summary = run_suite(graph=make_graph(_Exploding(), tmp_path))
    assert summary["model_errors"] == summary["total"]
    assert summary["unsafe_approvals"] == 0  # looks "safe"...
    assert "WARNING" in to_markdown("anthropic", summary)  # ...but is flagged
