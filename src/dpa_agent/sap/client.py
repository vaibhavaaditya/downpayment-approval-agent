"""SAP access layer.

Two implementations behind one protocol:

* MemorySapClient  — in-memory mock dataset (portfolio default, zero infra).
* ODataSapClient   — talks to an OData-style HTTP service. The bundled mock server
                     (`python -m dpa_agent.sap.mock_odata`) speaks the same dialect: V2
                     `d` envelope, `/Date(ms)/` dates, decimals as strings. Entity shapes
                     are simplified and only loosely modelled on S/4HANA API entities.

The client is also the anti-corruption layer: SAP response quirks are handled here, so
the rest of the code only ever sees clean domain objects. It is READ-ONLY by construction:
there is no method that writes to SAP.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any, Protocol

import httpx

from dpa_agent.models.request import (
    BankDetails,
    DownPaymentRequest,
    POLine,
    PriorPayment,
    PurchaseOrder,
    Vendor,
)
from dpa_agent.sap import mock_data

_SAFE_KEY = re.compile(r"^[A-Za-z0-9_\-]+$")
_SAP_DATE = re.compile(r"^/Date\((-?\d+)\)/$")


class SapClient(Protocol):
    def get_vendor(self, vendor_id: str) -> Vendor: ...

    def get_po(self, po_id: str) -> PurchaseOrder: ...

    def list_prior_payments(self, vendor_id: str, po_id: str | None) -> list[PriorPayment]: ...

    def get_request(self, request_id: str) -> DownPaymentRequest: ...

    def list_requests(self) -> list[DownPaymentRequest]: ...


class MemorySapClient:
    def __init__(self) -> None:
        self._vendors = dict(mock_data.VENDORS)
        self._pos = dict(mock_data.POS)
        self._payments = list(mock_data.PRIOR_PAYMENTS)
        self._requests = dict(mock_data.REQUESTS)

    def get_vendor(self, vendor_id: str) -> Vendor:
        return self._vendors[vendor_id]

    def get_po(self, po_id: str) -> PurchaseOrder:
        return self._pos[po_id]

    def list_prior_payments(self, vendor_id: str, po_id: str | None) -> list[PriorPayment]:
        return [p for p in self._payments if p.vendor_id == vendor_id]

    def get_request(self, request_id: str) -> DownPaymentRequest:
        return self._requests[request_id]

    def list_requests(self) -> list[DownPaymentRequest]:
        return list(self._requests.values())


def sap_date(value: date) -> str:
    """Encode a date the way OData V2 services do: /Date(milliseconds since epoch)/."""
    moment = datetime(value.year, value.month, value.day, tzinfo=UTC)
    return f"/Date({int(moment.timestamp() * 1000)})/"


def parse_sap_date(value: str | None) -> date | None:
    """Accepts SAP V2 '/Date(1700000000000)/' and plain ISO dates; None stays None."""
    if not value:
        return None
    m = _SAP_DATE.match(value)
    if m:
        return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=UTC).date()
    return date.fromisoformat(value[:10])


def _safe(key: str) -> str:
    """Keys go into URLs and OData filters: refuse anything that could alter the query."""
    if not _SAFE_KEY.match(key or ""):
        raise ValueError(f"unsafe key for OData request: {key!r}")
    return key


class ODataSapClient:
    """OData-style read client (mock server now; real APIs or a custom RAP service later).

    `http` is any object with `.get(url, params=...)` returning something httpx-like
    (`status_code`, `json()`, `raise_for_status()`); it defaults to an `httpx.Client`.
    """

    def __init__(self, base_url: str, http: Any = None, timeout: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._http = http if http is not None else httpx.Client(timeout=timeout)

    def _get(self, path: str, params: dict | None = None) -> Any:
        resp = self._http.get(f"{self._base}/{path}", params=params)
        if resp.status_code == 404:
            raise KeyError(path)
        resp.raise_for_status()
        return resp.json()["d"]

    # -- mapping: SAP-shaped payloads -> domain objects -----------------------------------
    @staticmethod
    def _bank(d: dict) -> BankDetails:
        return BankDetails(
            bank_country=d.get("BankCountry") or "",
            bank_key=d.get("BankKey") or "",
            account_number=d.get("BankAccount") or "",
            iban=d.get("IBAN") or "",
        )

    def get_vendor(self, vendor_id: str) -> Vendor:
        d = self._get(f"Suppliers('{_safe(vendor_id)}')")
        banks = d["to_Bank"]["results"]
        first = banks[0] if banks else {}
        return Vendor(
            vendor_id=d["Supplier"],
            name=d["SupplierName"],
            country=d["Country"],
            payment_block=bool(d["PaymentIsBlockedForSupplier"]),
            risk_tier=d["RiskTier"],
            on_time_delivery_pct=d["OnTimeDeliveryPct"],
            master_bank=self._bank(first),
            bank_changed_on=parse_sap_date(first.get("ValidityStartDate")),
        )

    def get_po(self, po_id: str) -> PurchaseOrder:
        d = self._get(f"PurchaseOrders('{_safe(po_id)}')")
        return PurchaseOrder(
            po_id=d["PurchaseOrder"],
            vendor_id=d["Supplier"],
            company_code=d["CompanyCode"],
            currency=d["DocumentCurrency"],
            net_value=d["NetAmount"],
            dp_percent_allowed=d["DownPaymentPercent"],
            status=d["Status"],
            lines=[
                POLine(
                    line_id=i["PurchaseOrderItem"],
                    material=i["Material"],
                    net_value=i["NetAmount"],
                )
                for i in d["to_Item"]["results"]
            ],
        )

    def list_prior_payments(self, vendor_id: str, po_id: str | None) -> list[PriorPayment]:
        d = self._get("SupplierPayments", params={"$filter": f"Supplier eq '{_safe(vendor_id)}'"})
        payments = [
            PriorPayment(
                payment_id=r["DocumentId"],
                vendor_id=r["Supplier"],
                po_id=r["PurchaseOrder"] or None,
                amount=r["Amount"],
                currency=r["Currency"],
                cleared_on=parse_sap_date(r["ClearingDate"]),
                doc_type=r["DocumentType"],
            )
            for r in d["results"]
        ]
        # same contract as MemorySapClient: all of the vendor's payments, checks filter by PO
        return sorted(payments, key=lambda p: (p.cleared_on, p.payment_id))

    @staticmethod
    def _request(d: dict) -> DownPaymentRequest:
        return DownPaymentRequest(
            request_id=d["RequestId"],
            vendor_id=d["Supplier"],
            po_id=d["PurchaseOrder"],
            amount=d["Amount"],
            currency=d["Currency"],
            requested_by=d["RequestedBy"],
            requested_on=parse_sap_date(d["RequestDate"]),
            purpose=d["Purpose"],
            remittance_bank=BankDetails(
                bank_country=d["PayeeBankCountry"],
                bank_key=d["PayeeBankKey"],
                account_number=d["PayeeBankAccount"],
                iban=d["PayeeIban"],
            ),
            vendor_document_text=d["VendorMessage"],
        )

    def get_request(self, request_id: str) -> DownPaymentRequest:
        return self._request(self._get(f"DownPaymentRequests('{_safe(request_id)}')"))

    def list_requests(self) -> list[DownPaymentRequest]:
        return [self._request(r) for r in self._get("DownPaymentRequests")["results"]]


#: Backwards-compatible name (the original client was called HttpSapClient).
HttpSapClient = ODataSapClient


def build_sap_client(base_url: str | None) -> SapClient:
    return ODataSapClient(base_url) if base_url else MemorySapClient()
