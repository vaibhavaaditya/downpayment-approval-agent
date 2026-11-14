"""The vendor on the request must be the vendor on the purchase order.

A mismatch means the payment would go to a supplier the PO was never placed with —
a trust-boundary failure, so it forces ESCALATE.
"""

from __future__ import annotations

from dpa_agent.models.checks import CheckResult, CheckStatus, Evidence, Severity
from dpa_agent.tools.context import ToolContext

CHECK_NAME = "vendor_po_match"


def check_vendor_po_match(ctx: ToolContext) -> CheckResult:
    req, po = ctx.request, ctx.po
    if req.vendor_id == po.vendor_id:
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.PASS,
            severity=Severity.HARD,
            summary="Request vendor matches the PO vendor.",
        )
    return CheckResult(
        check_name=CHECK_NAME,
        status=CheckStatus.FAIL,
        severity=Severity.HARD,
        summary=f"Request vendor {req.vendor_id} differs from PO vendor {po.vendor_id}.",
        evidence=[
            Evidence(
                evidence_id="EV-VPO-1",
                ref_type="po",
                ref_id=po.po_id,
                detail=f"PO vendor {po.vendor_id}; request vendor {req.vendor_id}",
            )
        ],
    )
