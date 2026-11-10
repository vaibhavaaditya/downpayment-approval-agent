"""Vendor history: payment block (hard) and delivery performance (soft warning)."""

from __future__ import annotations

from dpa_agent.models.checks import CheckResult, CheckStatus, Evidence, Severity
from dpa_agent.tools.context import ToolContext

CHECK_NAME = "vendor_history"


def check_vendor_history(ctx: ToolContext) -> CheckResult:
    vendor, limits = ctx.vendor, ctx.limits

    if vendor.payment_block:
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            summary=f"Vendor {vendor.vendor_id} is on payment hold in vendor master.",
            evidence=[
                Evidence(
                    evidence_id="EV-VEND-1",
                    ref_type="vendor",
                    ref_id=vendor.vendor_id,
                    detail="payment_block = true",
                )
            ],
        )

    if vendor.on_time_delivery_pct < limits.min_on_time_delivery_pct:
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.WARN,
            severity=Severity.SOFT,
            summary=(
                f"On-time delivery {vendor.on_time_delivery_pct}% is below "
                f"{limits.min_on_time_delivery_pct}% — factor into risk judgment."
            ),
            evidence=[
                Evidence(
                    evidence_id="EV-VEND-1",
                    ref_type="vendor",
                    ref_id=vendor.vendor_id,
                    detail=f"on_time_delivery_pct = {vendor.on_time_delivery_pct}",
                )
            ],
            metrics={"on_time_delivery_pct": float(vendor.on_time_delivery_pct)},
        )

    return CheckResult(
        check_name=CHECK_NAME,
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        summary=(
            f"Vendor in good standing (on-time delivery "
            f"{vendor.on_time_delivery_pct}%, risk tier {vendor.risk_tier})."
        ),
        metrics={"on_time_delivery_pct": float(vendor.on_time_delivery_pct)},
    )
