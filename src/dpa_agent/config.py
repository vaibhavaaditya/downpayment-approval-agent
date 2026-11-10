"""Runtime configuration read from the environment.

Deliberately dependency-light: a dataclass + os.environ, not a settings framework.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class PolicyLimits:
    """Deterministic payment-policy numbers. These are code on purpose.

    The model never computes these; it only reasons about results the checks produce.
    """

    max_dp_percent: Decimal = Decimal("20")       # max % of PO net value as down payment
    absolute_request_cap: Decimal = Decimal("500000")  # per-request cap in request currency
    duplicate_window_days: int = 90               # lookback for duplicate down payments
    min_on_time_delivery_pct: Decimal = Decimal("80")


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "auto"  # auto | anthropic | fallback
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-5"
    sap_base_url: str | None = None  # empty => in-memory mock dataset
    audit_log_path: Path = Path("audit/decisions.jsonl")
    limits: PolicyLimits = field(default_factory=PolicyLimits)

    @classmethod
    def from_env(cls) -> Settings:
        key = os.environ.get("ANTHROPIC_API_KEY") or None
        base = (os.environ.get("SAP_ODATA_BASE_URL") or "").strip() or None
        return cls(
            llm_provider=os.environ.get("DPA_LLM_PROVIDER", "auto").strip().lower(),
            anthropic_api_key=key,
            anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
            sap_base_url=base,
            audit_log_path=Path(os.environ.get("AUDIT_LOG_PATH", "audit/decisions.jsonl")),
        )
