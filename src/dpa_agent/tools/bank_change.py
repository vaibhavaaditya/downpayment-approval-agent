"""Recently changed vendor bank details.

Bank-detail change followed by a payment request is the classic business-email-compromise
pattern. A change inside the policy window HARD-fails and forces ESCALATE (fraud review).
The change date comes from the vendor master's change documents, never from request text.
"""

from __future__ import annotations

from dpa_agent.models.checks import CheckResult, CheckStatus, Evidence, Severity
from dpa_agent.tools.context import ToolContext

CHECK_NAME = "bank_recent_change"


def check_bank_recent_change(ctx: ToolContext) -> CheckResult:
    changed = ctx.vendor.bank_changed_on
    window = ctx.limits.bank_change_window_days
    if changed is None:
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.PASS,
            severity=Severity.HARD,
            summary="No bank-change date on record for this vendor.",
        )
    days = (ctx.today - changed).days
    if days < window:
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            summary=(
                f"Vendor bank details changed {days} days before this request "
                f"(policy window {window} days)."
            ),
            evidence=[
                Evidence(
                    evidence_id="EV-BANKCHG-1",
                    ref_type="vendor",
                    ref_id=ctx.vendor.vendor_id,
                    detail=f"bank details last changed {changed.isoformat()}",
                )
            ],
            metrics={"days_since_change": days},
        )
    return CheckResult(
        check_name=CHECK_NAME,
        status=CheckStatus.PASS,
        severity=Severity.HARD,
        summary=f"Bank details unchanged for {days} days (window {window}).",
        metrics={"days_since_change": days},
    )
