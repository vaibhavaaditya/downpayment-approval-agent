"""Watchlist vendors: policy POL-RSK-004 requires escalation regardless of amount.

The risk tier is read from the vendor master, never from request text.
"""

from __future__ import annotations

from dpa_agent.models.checks import CheckResult, CheckStatus, Evidence, Severity
from dpa_agent.tools.context import ToolContext

CHECK_NAME = "vendor_watchlist"


def check_vendor_watchlist(ctx: ToolContext) -> CheckResult:
    vendor = ctx.vendor
    if vendor.risk_tier != "watchlist":
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.PASS,
            severity=Severity.HARD,
            summary=f"Vendor risk tier is '{vendor.risk_tier}'.",
        )
    return CheckResult(
        check_name=CHECK_NAME,
        status=CheckStatus.FAIL,
        severity=Severity.HARD,
        summary=f"Vendor {vendor.vendor_id} is on the watchlist; escalation is mandatory.",
        evidence=[
            Evidence(
                evidence_id="EV-WATCH-1",
                ref_type="vendor",
                ref_id=vendor.vendor_id,
                detail="risk_tier = watchlist",
            )
        ],
    )
