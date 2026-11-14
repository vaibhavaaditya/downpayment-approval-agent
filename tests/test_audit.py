"""The audit log is hash-chained: history cannot be edited or deleted without detection."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from dpa_agent.audit import AuditLog
from dpa_agent.models.recommendation import HumanDecision, Label


def _decision(i: int) -> HumanDecision:
    return HumanDecision(
        request_id="R1", decided_label=Label.HOLD, decided_by="alice", justification=f"reason {i}"
    )


def _log_with(n: int, tmp_path: Path) -> AuditLog:
    log = AuditLog(tmp_path / "audit.jsonl")
    for i in range(n):
        log.record_human_decision(_decision(i))
    return log


def test_chain_verifies_and_empty_log_is_valid(tmp_path):
    assert AuditLog(tmp_path / "none.jsonl").verify() == (True, None)
    assert _log_with(3, tmp_path).verify() == (True, None)


def test_records_are_sequenced_and_linked(tmp_path):
    entries = _log_with(3, tmp_path).read_all()
    assert [e["seq"] for e in entries] == [1, 2, 3]
    assert entries[1]["prev_hash"] == entries[0]["hash"]


def test_editing_a_past_record_is_detected(tmp_path):
    log = _log_with(3, tmp_path)
    lines = log.path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[1])
    rec["justification"] = "rewritten history"
    lines[1] = json.dumps(rec, sort_keys=True)
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert log.verify() == (False, 2)


def test_deleting_a_record_is_detected(tmp_path):
    log = _log_with(3, tmp_path)
    lines = log.path.read_text(encoding="utf-8").splitlines()
    log.path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
    assert log.verify()[0] is False


def test_latest_returns_the_newest_matching_event(tmp_path):
    log = _log_with(2, tmp_path)
    assert log.latest("R1", "human_decision")["justification"] == "reason 1"
    assert log.latest("R2", "human_decision") is None
    assert log.latest("R1", "recommendation") is None


def test_concurrent_appends_keep_the_chain_intact(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    threads = [threading.Thread(target=log.record_human_decision, args=(_decision(i),)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(log.read_all()) == 12 and log.verify() == (True, None)
