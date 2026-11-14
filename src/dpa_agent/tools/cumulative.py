"""Cumulative down-payment cap: prior down payments + this request vs. the percentage cap.

`payment_limits` caps a single request; this closes the gap where several requests, each
under the cap, add up past it. `po_coverage` only stops the total exceeding 100% of the PO.
"""

from __future__ import annotations

from decimal import Decimal

from dpa_agent.models.checks import CheckResult, CheckStatus, Evidence, Severity
from dpa_agent.tools.context import ToolContext

CHECK_NAME = "cumulative_dp_cap"


def check_cumulative_cap(ctx: ToolContext) -> CheckResult:
    req, po = ctx.request, ctx.po
    if req.currency != po.currency or po.net_value <= 0:
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.PASS,
            severity=Severity.HARD,
            summary="Skipped: currencies not comparable or PO value invalid (see payment_limits).",
        )

    prior = sum(
        (p.amount for p in ctx.prior_payments if p.po_id == po.po_id and p.doc_type == "DF"),
        Decimal("0"),
    )
    if prior == 0:
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.PASS,
            severity=Severity.HARD,
            summary="No prior down payments on this PO (single-request cap: payment_limits).",
            metrics={"prior_dp": "0"},
        )

    allowed_pct = min(po.dp_percent_allowed, ctx.limits.max_dp_percent)
    total = prior + req.amount
    total_pct = (total / po.net_value * 100).quantize(Decimal("0.01"))
    metrics = {
        "prior_dp": str(prior),
        "request_amount": str(req.amount),
        "cumulative_percent": float(total_pct),
        "allowed_percent": float(allowed_pct),
    }
    if total_pct > allowed_pct:
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            summary=(
                f"Prior down payments ({prior}) + request ({req.amount}) = {total_pct}% of PO "
                f"net value; policy allows {allowed_pct}% cumulatively."
            ),
            evidence=[
                Evidence(
                    evidence_id="EV-CUM-1",
                    ref_type="po",
                    ref_id=po.po_id,
                    detail=f"{total} of {po.net_value} = {total_pct}% > {allowed_pct}%",
                )
            ],
            metrics=metrics,
        )
    return CheckResult(
        check_name=CHECK_NAME,
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        summary=f"Cumulative down payments {total_pct}% within the {allowed_pct}% cap.",
        metrics=metrics,
    )
