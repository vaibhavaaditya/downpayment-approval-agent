"""Registry + runner for deterministic checks.

Order is stable so evidence IDs and audit logs are reproducible.
"""

from __future__ import annotations

from collections.abc import Callable

from dpa_agent.models.checks import CheckResult
from dpa_agent.tools.bank_match import check_bank_match
from dpa_agent.tools.context import ToolContext
from dpa_agent.tools.coverage import check_po_coverage
from dpa_agent.tools.duplicates import check_duplicate
from dpa_agent.tools.injection import scan_injection
from dpa_agent.tools.limits import check_limits
from dpa_agent.tools.vendor_history import check_vendor_history

CheckFn = Callable[[ToolContext], CheckResult]

CHECK_REGISTRY: dict[str, CheckFn] = {
    "duplicate_request": check_duplicate,
    "payment_limits": check_limits,
    "po_coverage": check_po_coverage,
    "bank_detail_match": check_bank_match,
    "vendor_history": check_vendor_history,
    "injection_scan": scan_injection,
}

#: Check names whose failure must force ESCALATE (fraud / trust boundary).
ESCALATE_ON_FAILURE: frozenset[str] = frozenset({"bank_detail_match", "injection_scan"})


def run_all_checks(ctx: ToolContext) -> list[CheckResult]:
    return [fn(ctx) for fn in CHECK_REGISTRY.values()]
