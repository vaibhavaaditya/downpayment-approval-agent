"""Append-only audit log (JSONL).

One file line per pipeline run plus one per human decision. The log captures the
checks, the model's draft, guardrail actions, and the final label — enough to
reconstruct *why* a recommendation looked the way it did, months later.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from dpa_agent.models.recommendation import HumanDecision, Recommendation
from dpa_agent.models.state import GraphState


def _json_default(obj: Any) -> str:
    if isinstance(obj, Decimal):
        return str(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    raise TypeError(f"not JSON serializable: {type(obj)}")


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": datetime.now(UTC).isoformat(), **entry}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=_json_default) + "\n")

    def record_evaluation(self, state: GraphState) -> None:
        final: Recommendation = state["final"]
        draft: Recommendation | None = state.get("draft")
        self._append(
            {
                "event": "recommendation",
                "request_id": state["request"].request_id,
                "draft": draft.model_dump(mode="json") if draft else None,
                "checks": [c.model_dump(mode="json") for c in state.get("checks", [])],
                "policy_clauses": [c.clause_id for c in state.get("policy_chunks", [])],
                "final": final.model_dump(mode="json"),
                "errors": state.get("errors", []),
            }
        )

    def record_human_decision(self, decision: HumanDecision) -> None:
        self._append({"event": "human_decision", **decision.model_dump(mode="json")})

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line]
