"""Prompt-injection scan over untrusted vendor document text.

The vendor free-text field (and anything pasted into the request purpose) is
attacker-controlled input. This regex detector is
deliberately coarse: a hit HARD-fails and forces ESCALATE, so a false positive
costs a human review, while a missed detection is backstopped by the guardrail
(the model cannot cite anything that authorizes ignoring policy).

This is a deterministic check, not an LLM classifier — same philosophy as the rest.
"""

from __future__ import annotations

import re

from dpa_agent.models.checks import CheckResult, CheckStatus, Evidence, Severity
from dpa_agent.tools.context import ToolContext

CHECK_NAME = "injection_scan"

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "ignore_instructions",
        re.compile(r"ignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)", re.I),
    ),
    (
        "disregard_policy",
        re.compile(r"disregard\s+(the\s+|all\s+)?(policy|rules|instructions|thresholds)", re.I),
    ),
    ("approve_regardless", re.compile(r"approve\s+[^.?!]*regardless", re.I)),
    (
        "override_controls",
        re.compile(r"override\s+[^.?!]*(control|approval|check|limit|threshold)", re.I),
    ),
    (
        "roleplay_system",
        re.compile(
            r"(you\s+are\s+now|act\s+as\s+if|pretend\s+to\s+be)"
            r"\s+(an?\s+)?(unrestricted|different|new)?\s*(system|assistant|model)",
            re.I,
        ),
    ),
    (
        "reveal_prompt",
        re.compile(r"(reveal|print|repeat|output)\s+[^.?!]*\s(system\s+prompt|instructions)", re.I),
    ),
    ("system_prompt_reference", re.compile(r"system\s+prompt", re.I)),
    ("as_an_ai", re.compile(r"\bas\s+an\s+ai\b", re.I)),
    (
        "force_status",
        re.compile(
            r"(mark|set|record|classify)\s+(this|it|the\s+request)\s+"
            r"(as\s+)?(approved|low[-\s]?risk)",
            re.I,
        ),
    ),
    ("approve_directive", re.compile(r"\bapprove\s+(this|the)\s+(request|payment)\b", re.I)),
    ("suppress_controls", re.compile(r"do\s+not\s+(flag|escalate|hold)\b", re.I)),
]


def scan_injection(ctx: ToolContext) -> CheckResult:
    # `purpose` is entered by the requester but often pastes vendor wording: scan both.
    text = f"{ctx.request.vendor_document_text or ''}\n{ctx.request.purpose or ''}"
    if not text.strip():
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.PASS,
            severity=Severity.HARD,
            summary="No vendor document text attached.",
        )

    hits = [name for name, pat in _PATTERNS if pat.search(text)]
    if hits:
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            summary=f"Vendor document text matches injection patterns: {', '.join(hits)}.",
            evidence=[
                Evidence(
                    evidence_id="EV-INJ-1",
                    ref_type="request",
                    ref_id=ctx.request.request_id,
                    detail=f"flagged patterns: {', '.join(hits)}",
                )
            ],
            metrics={"pattern_hits": len(hits)},
        )
    return CheckResult(
        check_name=CHECK_NAME,
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        summary="No injection patterns detected in vendor document text.",
    )
