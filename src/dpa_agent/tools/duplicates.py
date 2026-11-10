"""Duplicate down-payment detection — deterministic, windowed, ID-based."""

from __future__ import annotations

from dpa_agent.models.checks import CheckResult, CheckStatus, Evidence, Severity
from dpa_agent.tools.context import ToolContext

CHECK_NAME = "duplicate_request"


def check_duplicate(ctx: ToolContext) -> CheckResult:
    window_start = date_minus_days(ctx.today, ctx.limits.duplicate_window_days)
    hits = []
    for p in ctx.prior_payments:
        if p.vendor_id != ctx.request.vendor_id or p.cleared_on < window_start:
            continue
        same_po = p.po_id is not None and p.po_id == ctx.request.po_id
        if same_po and (p.amount == ctx.request.amount or p.doc_type == "DF"):
            hits.append(p)
    evidence = [
        Evidence(
            evidence_id=f"EV-DUP-{i + 1}",
            ref_type="payment",
            ref_id=p.payment_id,
            detail=(
                f"Prior payment {p.payment_id}: {p.amount} {p.currency} on PO "
                f"{p.po_id or '-'} cleared {p.cleared_on.isoformat()}"
            ),
        )
        for i, p in enumerate(hits)
    ]
    if hits:
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            summary=(
                f"Found {len(hits)} prior payment(s) for vendor {ctx.request.vendor_id} "
                f"on PO {ctx.request.po_id} within {ctx.limits.duplicate_window_days} days."
            ),
            evidence=evidence,
            metrics={"duplicate_count": len(hits)},
        )
    return CheckResult(
        check_name=CHECK_NAME,
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        summary="No duplicate down payment found in the lookback window.",
        metrics={"duplicate_count": 0},
    )


def date_minus_days(d, days: int):
    from datetime import timedelta

    return d - timedelta(days=days)
