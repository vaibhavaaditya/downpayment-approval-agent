"""Append-only, hash-chained audit log (JSONL).

One line per pipeline run plus one per human decision. The log captures the checks, the
model's draft, guardrail actions and the final label — enough to reconstruct *why* a
recommendation looked the way it did, months later.

Each record carries the hash of the previous one, so editing or deleting a past entry
breaks the chain and `verify()` reports where. This is tamper-EVIDENT, not tamper-proof:
a production system would also ship records to write-once storage.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from dpa_agent.models.recommendation import HumanDecision, Recommendation
from dpa_agent.models.state import GraphState

GENESIS = "0" * 64


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    raise TypeError(f"not JSON serializable: {type(obj)}")


def _digest(record: dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()  # keeps the chain consistent across request threads

    def _tail(self) -> tuple[int, str]:
        entries = self.read_all()
        if not entries:
            return 0, GENESIS
        return entries[-1]["seq"], entries[-1]["hash"]

    def _append(self, entry: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            seq, prev = self._tail()
            record = {
                "seq": seq + 1,
                "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                "prev_hash": prev,
                **entry,
            }
            # normalise to plain JSON types first, so what we hash is exactly what we store
            record = json.loads(json.dumps(record, default=_json_default))
            record["hash"] = _digest(record)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
            return record

    def record_evaluation(self, state: GraphState) -> dict[str, Any]:
        request = state["request"]
        final: Recommendation = state["final"]
        draft: Recommendation | None = state.get("draft")
        return self._append(
            {
                "event": "recommendation",
                "request_id": request.request_id,
                "request_summary": {
                    "requested_by": request.requested_by,
                    "amount": str(request.amount),
                    "currency": request.currency,
                    "po_id": request.po_id,
                    "vendor_id": request.vendor_id,
                },
                "required_approver": state.get("required_approver"),
                "draft": draft.model_dump(mode="json") if draft else None,
                "checks": [c.model_dump(mode="json") for c in state.get("checks", [])],
                "policy_clauses": [c.clause_id for c in state.get("policy_chunks", [])],
                "final": final.model_dump(mode="json"),
                "model_meta": state.get("model_meta", {}),
                "errors": state.get("errors", []),
            }
        )

    def record_human_decision(self, decision: HumanDecision) -> dict[str, Any]:
        return self._append({"event": "human_decision", **decision.model_dump(mode="json")})

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line]

    def latest(self, request_id: str, event: str) -> dict[str, Any] | None:
        found = None
        for entry in self.read_all():
            if entry.get("request_id") == request_id and entry.get("event") == event:
                found = entry
        return found

    def verify(self) -> tuple[bool, int | None]:
        """Return (ok, first_bad_seq). Any edit, deletion or reordering breaks the chain."""
        prev = GENESIS
        for entry in self.read_all():
            if entry.get("prev_hash") != prev or entry.get("hash") != _digest(entry):
                return False, entry.get("seq")
            prev = entry["hash"]
        return True, None
