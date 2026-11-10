"""Synthetic decision-accuracy suite.

Metrics:
  * accuracy            — exact-label matches (only where `expected` is set)
  * unsafe_approvals    — APPROVE on a case whose `forbidden` list contains it
                          (CI gate: must be 0, always)

The gate runs offline (FallbackRecommender) by default so CI never needs secrets.
Pass `--provider anthropic` to evaluate the live Claude path on the same cases.

Usage:
  python evals/run_eval.py
  python evals/run_eval.py --provider anthropic
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = Path(__file__).parent / "cases.json"

sys.path.insert(0, str(REPO_ROOT / "src"))

from dpa_agent.audit import AuditLog  # noqa: E402
from dpa_agent.config import PolicyLimits  # noqa: E402
from dpa_agent.graph.graph import build_graph, evaluate  # noqa: E402
from dpa_agent.llm.client import FallbackRecommender, build_recommender  # noqa: E402
from dpa_agent.models.request import (  # noqa: E402
    DownPaymentRequest,
    PriorPayment,
    PurchaseOrder,
    Vendor,
)
from dpa_agent.rag.store import PolicyStore  # noqa: E402
from dpa_agent.sap.client import MemorySapClient  # noqa: E402


def load_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def build_for(provider: str, tmp: Path):
    if provider == "fallback":
        recommender = FallbackRecommender()
    else:
        import os

        recommender = build_recommender(
            provider, os.environ.get("ANTHROPIC_API_KEY"),
            os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        )
    return build_graph(
        client=MemorySapClient(),
        recommender=recommender,
        store=PolicyStore.from_directory(),
        audit=AuditLog(tmp / "eval_audit.jsonl"),
        limits=PolicyLimits(),
        today=date(2026, 11, 10),
    )


def run_case(graph, case: dict) -> dict:
    ctx, req = case["context"], case["request"]
    state = evaluate(
        graph,
        DownPaymentRequest.model_validate(req),
        vendor=Vendor.model_validate(ctx["vendor"]),
        po=PurchaseOrder.model_validate(ctx["po"]),
        prior_payments=[PriorPayment.model_validate(p) for p in ctx["prior_payments"]],
    )
    label = state["final"].label.value
    forbidden = case.get("forbidden") or []
    expected = case.get("expected")
    return {
        "id": case["id"],
        "label": label,
        "expected": expected,
        "correct": (label == expected) if expected else None,
        "unsafe": label in forbidden,
        "guardrail_actions": state["final"].guardrail_actions,
    }


def run_suite(provider: str = "fallback") -> dict:
    cases = load_cases()
    with tempfile.TemporaryDirectory() as td:
        graph = build_for(provider, Path(td))
        results = [run_case(graph, c) for c in cases]

    evaluated = [r for r in results if r["expected"]]
    return {
        "results": results,
        "total": len(results),
        "accuracy": (sum(1 for r in evaluated if r["correct"]) / len(evaluated))
        if evaluated else None,
        "unsafe_approvals": sum(1 for r in results if r["unsafe"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="fallback",
                        choices=["fallback", "anthropic", "auto"])
    args = parser.parse_args()

    summary = run_suite(args.provider)
    print(f"{'case':38} {'label':10} {'expected':10} {'safe':6}")
    print("-" * 68)
    for r in summary["results"]:
        exp = r["expected"] or "-"
        print(f"{r['id']:38} {r['label']:10} {exp:10} {'OK' if not r['unsafe'] else 'UNSAFE':6}")
    print("-" * 68)
    if summary["accuracy"] is not None:
        print(f"accuracy (labeled cases): {summary['accuracy']:.0%}")
    print(f"unsafe approvals: {summary['unsafe_approvals']}")

    if summary["unsafe_approvals"] > 0:
        print("FAIL: unsafe approval detected")
        return 1
    print("PASS: no unsafe approvals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
