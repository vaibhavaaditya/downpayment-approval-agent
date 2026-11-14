"""PO eligibility: down payments are only allowed against a released purchase order."""

from __future__ import annotations

from dpa_agent.models.checks import CheckResult, CheckStatus, Evidence, Severity
from dpa_agent.tools.context import ToolContext

CHECK_NAME = "po_status"


def check_po_status(ctx: ToolContext) -> CheckResult:
    po = ctx.po
    if po.status == "released":
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.PASS,
            severity=Severity.HARD,
            summary=f"PO {po.po_id} is released.",
        )
    return CheckResult(
        check_name=CHECK_NAME,
        status=CheckStatus.FAIL,
        severity=Severity.HARD,
        summary=f"PO {po.po_id} has status '{po.status}'; down payments need a released PO.",
        evidence=[
            Evidence(
                evidence_id="EV-PO-1",
                ref_type="po",
                ref_id=po.po_id,
                detail=f"status = {po.status}",
            )
        ],
    )
