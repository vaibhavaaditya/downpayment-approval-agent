"""Core SAP-shaped entities.

Mirrors the fields the agent needs from S/4HANA (vendor master, PO, special-GL
down-payment request, clearing document history) — flattened for the portfolio.
Monetary values are Decimal: comparisons and caps must never be float-approximate.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


def mask_identifier(value: str) -> str:
    """Keep only the last four characters: enough to recognise an account, not to use it."""
    value = "".join(value.split())
    return "****" + value[-4:] if value else "(none)"


class BankDetails(BaseModel):
    """Subset of LFBK (vendor bank) vs. the remittance bank on the request."""

    bank_country: str = ""
    bank_key: str = ""          # SWIFT/BIC or bank number
    account_number: str = ""
    iban: str = ""

    def normalized_identity(self) -> tuple[str, str]:
        """Identity used for matching: IBAN when present, else account+bank key."""
        iban = "".join(self.iban.split()).upper()
        if iban:
            return ("iban", iban)
        acct = "".join(self.account_number.split()).upper()
        return (acct, "".join(self.bank_key.split()).upper())

    def describe_masked(self) -> str:
        """Human/LLM-readable description with account identifiers masked."""
        iban = "".join(self.iban.split()).upper()
        acct = "".join(self.account_number.split()).upper()
        if iban:
            core = f"IBAN {mask_identifier(iban)}"
        elif acct:
            core = f"account {mask_identifier(acct)}"
        else:
            core = "no account"
        return f"{core} ({self.bank_country or '?'}, {self.bank_key or '?'})"


class Vendor(BaseModel):
    """Flattened LFA1/LFBK vendor master."""

    vendor_id: str
    name: str
    country: str = ""
    payment_block: bool = False          # payment hold set in vendor master
    risk_tier: str = "standard"          # standard | strategic | watchlist
    on_time_delivery_pct: Decimal = Decimal("100")
    master_bank: BankDetails = Field(default_factory=BankDetails)
    bank_changed_on: date | None = None  # last change date of the master bank (change documents)


class POLine(BaseModel):
    line_id: str
    material: str = ""
    net_value: Decimal = Decimal("0")


class PurchaseOrder(BaseModel):
    """Flattened EKKO/EKPO with down-payment terms (PO schedule EKPO-ZLSPR etc.)."""

    po_id: str
    vendor_id: str
    company_code: str = "1000"
    currency: str = "EUR"
    net_value: Decimal
    dp_percent_allowed: Decimal = Decimal("20")
    status: str = "released"             # released | not_released | closed | deleted
    lines: list[POLine] = Field(default_factory=list)


class DownPaymentRequest(BaseModel):
    """A vendor down-payment request awaiting approval (special GL, F-47-style)."""

    request_id: str
    vendor_id: str
    po_id: str
    amount: Decimal
    currency: str
    requested_by: str
    requested_on: date
    purpose: str = ""
    remittance_bank: BankDetails = Field(default_factory=BankDetails)
    vendor_document_text: str = ""  # free text attached by the vendor — untrusted


class PriorPayment(BaseModel):
    """Cleared/issued payment history used for duplicate and coverage checks."""

    payment_id: str
    vendor_id: str
    po_id: str | None = None
    amount: Decimal
    currency: str
    cleared_on: date
    doc_type: str = "KZ"  # KZ = payment, DF = down-payment invoice
