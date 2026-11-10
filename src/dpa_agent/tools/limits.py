"""Down-payment limit checks: PO-percentage cap, absolute cap, currency sanity.

All arithmetic here is Decimal — the model never sees a raw number it must trust.
"""

from __future__ import annotations

from decimal import Decimal

from dpa_agent.models.checks import CheckResult, CheckStatus, Evidence, Severity
from dpa_agent.tools.context import ToolContext

CHECK_NAME = "payment_limits"


def check_limits(ctx: ToolContext) -> CheckResult:
    req, po = ctx.request, ctx.po
    failures: list[str] = []
    evidence: list[Evidence] = []
    metrics: dict[str, str | int | float] = {}

    if req.currency != po.currency:
        failures.append(
            f"request currency {req.currency} != PO currency {po.currency}; amounts not comparable"
        )
        evidence.append(
            Evidence(
                evidence_id="EV-LIM-1",
                ref_type="po",
                ref_id=po.po_id,
                detail=f"PO currency {po.currency}, request currency {req.currency}",
            )
        )
    else:
        allowed_pct = min(po.dp_percent_allowed, ctx.limits.max_dp_percent)
        if po.net_value <= 0:
            failures.append("PO net value is not positive")
        else:
            pct = (req.amount / po.net_value * 100).quantize(Decimal("0.01"))
            metrics["dp_percent"] = float(pct)
            metrics["allowed_percent"] = float(allowed_pct)
            if pct > allowed_pct:
                failures.append(
                    f"down payment is {pct}% of PO net value; policy allows {allowed_pct}%"
                )
                evidence.append(
                    Evidence(
                        evidence_id="EV-LIM-1",
                        ref_type="po",
                        ref_id=po.po_id,
                        detail=(
                            f"{req.amount} of {po.net_value} = {pct}% > {allowed_pct}%"
                        ),
                    )
                )

    metrics["absolute_cap"] = float(ctx.limits.absolute_request_cap)
    if req.amount > ctx.limits.absolute_request_cap:
        failures.append(
            f"amount {req.amount} exceeds per-request cap {ctx.limits.absolute_request_cap}"
        )
        evidence.append(
            Evidence(
                evidence_id="EV-LIM-2",
                ref_type="request",
                ref_id=req.request_id,
                detail=f"requested {req.amount} {req.currency}",
            )
        )

    if failures:
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            summary="; ".join(failures),
            evidence=evidence,
            metrics=metrics,
        )
    return CheckResult(
        check_name=CHECK_NAME,
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        summary=(
            f"Amount within PO percentage cap ({metrics.get('allowed_percent', 'n/a')}%) "
            f"and absolute cap {ctx.limits.absolute_request_cap}."
        ),
        metrics=metrics,
    )
