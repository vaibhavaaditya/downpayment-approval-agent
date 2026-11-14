"""Registry + runner for deterministic checks.

Order is stable so evidence IDs and audit logs are reproducible.
"""

from __future__ import annotations

from collections.abc import Callable

from dpa_agent.models.checks import CheckResult
from dpa_agent.tools.bank_change import check_bank_recent_change
from dpa_agent.tools.bank_match import check_bank_match
from dpa_agent.tools.context import ToolContext
from dpa_agent.tools.coverage import check_po_coverage
from dpa_agent.tools.cumulative import check_cumulative_cap
from dpa_agent.tools.duplicates import check_duplicate
from dpa_agent.tools.injection import scan_injection
from dpa_agent.tools.limits import check_limits
from dpa_agent.tools.po_status import check_po_status
from dpa_agent.tools.vendor_history import check_vendor_history
from dpa_agent.tools.vendor_po_match import check_vendor_po_match
from dpa_agent.tools.vendor_risk import check_vendor_watchlist

CheckFn = Callable[[ToolContext], CheckResult]

CHECK_REGISTRY: dict[str, CheckFn] = {
    "duplicate_request": check_duplicate,
    "payment_limits": check_limits,
    "po_coverage": check_po_coverage,
    "cumulative_dp_cap": check_cumulative_cap,
    "po_status": check_po_status,
    "vendor_po_match": check_vendor_po_match,
    "bank_detail_match": check_bank_match,
    "bank_recent_change": check_bank_recent_change,
    "vendor_history": check_vendor_history,
    "vendor_watchlist": check_vendor_watchlist,
    "injection_scan": scan_injection,
}

#: Check names whose failure must force ESCALATE (fraud / trust boundary).
ESCALATE_ON_FAILURE: frozenset[str] = frozenset(
    {
        "bank_detail_match",
        "bank_recent_change",
        "injection_scan",
        "vendor_po_match",
        "vendor_watchlist",
    }
)

#: The policy clause that explains each check; used to ground the model in the right text.
CHECK_TO_CLAUSE: dict[str, str] = {
    "duplicate_request": "POL-PAY-003",
    "payment_limits": "POL-PAY-001",
    "po_coverage": "POL-PAY-004",
    "cumulative_dp_cap": "POL-PAY-006",
    "po_status": "POL-PAY-009",
    "vendor_po_match": "POL-RSK-005",
    "bank_detail_match": "POL-RSK-002",
    "bank_recent_change": "POL-RSK-002",
    "vendor_history": "POL-RSK-003",
    "vendor_watchlist": "POL-RSK-004",
    "injection_scan": "POL-SEC-002",
}


def run_all_checks(ctx: ToolContext) -> list[CheckResult]:
    return [fn(ctx) for fn in CHECK_REGISTRY.values()]
