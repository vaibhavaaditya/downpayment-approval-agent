"""Bank-detail match: remittance bank on the request vs. vendor master (LFBK).

A mismatch is the classic vendor-fraud vector — it HARD-fails and forces ESCALATE.
"""

from __future__ import annotations

from dpa_agent.models.checks import CheckResult, CheckStatus, Evidence, Severity
from dpa_agent.tools.context import ToolContext

CHECK_NAME = "bank_detail_match"


def check_bank_match(ctx: ToolContext) -> CheckResult:
    req_bank = ctx.request.remittance_bank
    master = ctx.vendor.master_bank

    if not req_bank.iban and not req_bank.account_number and not req_bank.bank_key:
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.FAIL,
            severity=Severity.HARD,
            summary="Request carries no remittance bank details.",
            evidence=[
                Evidence(
                    evidence_id="EV-BANK-1",
                    ref_type="vendor",
                    ref_id=ctx.vendor.vendor_id,
                    detail="vendor master bank on file: " + _describe(master),
                )
            ],
        )

    if req_bank.normalized_identity() == master.normalized_identity():
        return CheckResult(
            check_name=CHECK_NAME,
            status=CheckStatus.PASS,
            severity=Severity.HARD,
            summary="Remittance bank matches vendor master.",
            evidence=[
                Evidence(
                    evidence_id="EV-BANK-1",
                    ref_type="vendor",
                    ref_id=ctx.vendor.vendor_id,
                    detail="matched: " + _describe(master),
                )
            ],
        )

    return CheckResult(
        check_name=CHECK_NAME,
        status=CheckStatus.FAIL,
        severity=Severity.HARD,
        summary="Remittance bank on request does not match vendor master bank details.",
        evidence=[
            Evidence(
                evidence_id="EV-BANK-1",
                ref_type="vendor",
                ref_id=ctx.vendor.vendor_id,
                detail="master: " + _describe(master),
            ),
            Evidence(
                evidence_id="EV-BANK-2",
                ref_type="request",
                ref_id=ctx.request.request_id,
                detail="request: " + _describe(req_bank),
            ),
        ],
        metrics={"match": 0},
    )


def _describe(b) -> str:
    ident, second = b.normalized_identity()
    return f"{ident} / {second} ({b.bank_country or '?'}, {b.bank_key or '?'})"
