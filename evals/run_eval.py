"""Synthetic decision-quality suite.

Headline metric: UNSAFE APPROVALS — the agent recommends APPROVE where approval is not an
acceptable answer. Also reported: accuracy (label within the case's acceptable set),
over-escalation (clean requests that were not approved) and how often a guardrail had to
overrule the model.

Cases flagged `llm_only` pass every deterministic check; only a model reading the vendor text
can get them right. The offline baseline (FallbackRecommender) is expected to approve them,
so its unsafe-approval count is a measurement of what a model has to add, not a bug.

Usage:
  python evals/run_eval.py                              # offline baseline
  python evals/run_eval.py --provider anthropic         # Claude on the same cases
  python evals/run_eval.py --provider anthropic --max-unsafe 0
  python evals/run_eval.py --max-unsafe 8 --min-accuracy 0.79   # CI gate for the baseline

The cases are synthetic and written by the repo author: treat results as regression tests and
a smoke test for the model, not as a statistical claim about real-world accuracy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = Path(__file__).parent / "cases.json"

sys.path.insert(0, str(REPO_ROOT / "src"))

from dpa_agent.audit import AuditLog  # noqa: E402
from dpa_agent.config import DEFAULT_MODEL, PolicyLimits  # noqa: E402
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


def make_graph(recommender, tmp: Path):
    """Graph over the eval context; `recommender` can be a real, fallback or test double."""
    return build_graph(
        client=MemorySapClient(),
        recommender=recommender,
        store=PolicyStore.from_directory(),
        audit=AuditLog(tmp / "eval_audit.jsonl"),
        limits=PolicyLimits(),
    )


def build_for(provider: str, tmp: Path):
    store = PolicyStore.from_directory()
    if provider == "fallback":
        recommender = FallbackRecommender()
    else:
        recommender = build_recommender(
            provider,
            os.environ.get("ANTHROPIC_API_KEY"),
            os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
            store=store,
        )
    return make_graph(recommender, tmp)


def run_case(graph, case: dict) -> dict:
    ctx = case["context"]
    state = evaluate(
        graph,
        DownPaymentRequest.model_validate(case["request"]),
        vendor=Vendor.model_validate(ctx["vendor"]),
        po=PurchaseOrder.model_validate(ctx["po"]),
        prior_payments=[PriorPayment.model_validate(p) for p in ctx["prior_payments"]],
    )
    final, draft = state["final"], state.get("draft")
    label = final.label.value
    acceptable = case["acceptable"]
    usage = (state.get("model_meta") or {}).get("usage") or {}
    return {
        "id": case["id"],
        "category": case["category"],
        "llm_only": case["llm_only"],
        "expected": case["expected"],
        "acceptable": acceptable,
        "label": label,
        "model_label": draft.label.value if draft else None,
        "correct": label in acceptable,
        "unsafe": label == "APPROVE" and "APPROVE" not in acceptable,
        "over_escalation": case["expected"] == "APPROVE" and label != "APPROVE",
        "guardrail_overruled": any("->" in a for a in final.guardrail_actions),
        "errors": state.get("errors", []),
        # a failed model call degrades to HOLD, which can look "safe": count it separately
        "model_error": any("recommender failed" in e for e in state.get("errors", [])),
        "tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
    }


def run_suite(provider: str = "fallback", graph=None) -> dict:
    cases = load_cases()
    with tempfile.TemporaryDirectory() as td:
        graph = graph or build_for(provider, Path(td))
        results = [run_case(graph, c) for c in cases]

    n = len(results)
    clean = [r for r in results if r["expected"] == "APPROVE"]
    llm_only = [r for r in results if r["llm_only"]]
    by_cat: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in results:
        by_cat[r["category"]][0] += 1
        by_cat[r["category"]][1] += int(r["correct"])
    return {
        "results": results,
        "total": n,
        "accuracy": sum(r["correct"] for r in results) / n,
        "unsafe_approvals": sum(r["unsafe"] for r in results),
        "over_escalations": sum(r["over_escalation"] for r in clean),
        "clean_total": len(clean),
        "guardrail_overrules": sum(r["guardrail_overruled"] for r in results),
        "model_errors": sum(r["model_error"] for r in results),
        "llm_only_total": len(llm_only),
        "llm_only_correct": sum(r["correct"] for r in llm_only),
        "rule_detectable_correct": sum(r["correct"] for r in results if not r["llm_only"]),
        "rule_detectable_total": n - len(llm_only),
        "by_category": {k: {"cases": v[0], "correct": v[1]} for k, v in sorted(by_cat.items())},
        "avg_tokens": sum(r["tokens"] for r in results) / n,
    }


def to_markdown(provider: str, s: dict) -> str:
    lines = [f"### Results: `{provider}`", ""]
    if s["model_errors"]:
        lines += [
            f"> **WARNING: {s['model_errors']} of {s['total']} cases had a model/API failure "
            "and fell back to HOLD. These results do NOT measure the model; "
            "fix the errors and re-run.**",
            "",
        ]
    lines += [
        f"- cases: **{s['total']}**, accuracy: **{s['accuracy']:.1%}**",
        f"- **unsafe approvals: {s['unsafe_approvals']}**",
        f"- rule-detectable cases correct: "
        f"{s['rule_detectable_correct']}/{s['rule_detectable_total']}",
        f"- LLM-only cases correct: **{s['llm_only_correct']}/{s['llm_only_total']}**",
        f"- over-escalations (clean requests not approved): "
        f"{s['over_escalations']}/{s['clean_total']}",
        f"- cases where a guardrail overruled the model: {s['guardrail_overrules']}",
        "",
        "| category | cases | correct |",
        "|---|---|---|",
        *[f"| {k} | {v['cases']} | {v['correct']} |" for k, v in s["by_category"].items()],
    ]
    misses = [r for r in s["results"] if not r["correct"]]
    if misses:
        lines += ["", "Misses:"]
        lines += [
            f"- {r['id']}: expected {'/'.join(r['acceptable'])}, got {r['label']}"
            + (" (unsafe)" if r["unsafe"] else "")
            for r in misses
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--provider", default="fallback", choices=["fallback", "anthropic", "auto"])
    parser.add_argument("--out", help="directory for results.json and results.md")
    parser.add_argument("--min-accuracy", type=float, help="exit 1 if accuracy is below this")
    parser.add_argument("--max-unsafe", type=int, help="exit 1 if unsafe approvals exceed this")
    args = parser.parse_args()

    summary = run_suite(args.provider)
    md = to_markdown(args.provider, summary)
    print(md)

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{args.provider}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        (out / f"{args.provider}.md").write_text(md, encoding="utf-8")

    failures = []
    if summary["model_errors"]:
        failures.append(f"{summary['model_errors']} cases had a model/API failure (see warning)")
    if args.min_accuracy is not None and summary["accuracy"] < args.min_accuracy:
        failures.append(f"accuracy {summary['accuracy']:.3f} < {args.min_accuracy}")
    if args.max_unsafe is not None and summary["unsafe_approvals"] > args.max_unsafe:
        failures.append(f"unsafe approvals {summary['unsafe_approvals']} > {args.max_unsafe}")
    if failures:
        print("FAIL: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
