"""PO coverage: total down payments (prior + this request) vs. PO net value."""

from __future__ import annotations

from decimal import Decimal

from dpa_agent.models.checks import CheckResult, CheckStatus, Evidence, Severity
from dpa_agent.tools.context import ToolContext

CHECK_NAME = "po_coverage"


def check_po_coverage(ctx: ToolContext) -> CheckResult:
    req, po = ctx.request, ctx.po
    if req.currency != po.currency:
        # Currency mismatch already fails the limits check; skip to avoid noise.
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.PASS,
            severity=Severity.HARD,
            summary="Skipped: currencies not comparable (handled by payment_limits).",
        )

    prior = sum(
        (p.amount for p in ctx.prior_payments if p.po_id == po.po_id and p.doc_type == "DF"),
        Decimal("0"),
    )
    total = prior + req.amount
    remaining = po.net_value - prior
    metrics = {
        "prior_dp": str(prior),
        "request_amount": str(req.amount),
        "po_net_value": str(po.net_value),
        "total_after": str(total),
    }
    if total > po.net_value:
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            summary=(
                f"Prior down payments ({prior}) + request ({req.amount}) = {total} "
                f"exceeds PO net value {po.net_value}."
            ),
            evidence=[
                Evidence(
                    evidence_id="EV-COV-1",
                    ref_type="po",
                    ref_id=po.po_id,
                    detail=f"remaining PO value before this request: {remaining}",
                )
            ],
            metrics=metrics,
        )
    if req.amount > remaining:
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            summary=f"Request {req.amount} exceeds remaining PO value {remaining}.",
            evidence=[
                Evidence(
                    evidence_id="EV-COV-1",
                    ref_type="po",
                    ref_id=po.po_id,
                    detail=f"remaining PO value before this request: {remaining}",
                )
            ],
            metrics=metrics,
        )
    return CheckResult(
        check_name=CHECK_NAME,
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        summary=f"Request fits within remaining PO value ({remaining} left before request).",
        metrics=metrics,
    )
