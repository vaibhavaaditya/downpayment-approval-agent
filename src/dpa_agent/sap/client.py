"""SAP access layer.

Two implementations behind one protocol:

* MemorySapClient  — in-memory mock dataset (portfolio default, zero infra).
* HttpSapClient    — talks to OData-style REST endpoints (the mock server exposes
                     the same shapes; swapping in real S/4HANA released APIs or a
                     custom CDS view keeps this interface — see docs/sap-integration.md).
"""

from __future__ import annotations

from typing import Protocol

import httpx

from dpa_agent.models.request import DownPaymentRequest, PriorPayment, PurchaseOrder, Vendor
from dpa_agent.sap import mock_data


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


class HttpSapClient:
    """OData-flavored REST client (mock server now; real APIs later).

    Paths follow the OData convention (`/sap/opu/odata/...`) so the URL contract
    is the same shape you would configure against API Business Hub services.
    """

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self._base, timeout=timeout)

    def _get_json(self, path: str, params: dict | None = None) -> dict:
        resp = self._http.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def get_vendor(self, vendor_id: str) -> Vendor:
        return Vendor.model_validate(self._get_json(f"/api/v1/vendors/{vendor_id}"))

    def get_po(self, po_id: str) -> PurchaseOrder:
        return PurchaseOrder.model_validate(self._get_json(f"/api/v1/purchase-orders/{po_id}"))

    def list_prior_payments(self, vendor_id: str, po_id: str | None) -> list[PriorPayment]:
        data = self._get_json(f"/api/v1/vendors/{vendor_id}/prior-payments")
        payments = [PriorPayment.model_validate(p) for p in data]
        if po_id is None:
            return payments
        return [p for p in payments if p.po_id == po_id]

    def get_request(self, request_id: str) -> DownPaymentRequest:
        return DownPaymentRequest.model_validate(
            self._get_json(f"/api/v1/downpayment-requests/{request_id}")
        )

    def list_requests(self) -> list[DownPaymentRequest]:
        data = self._get_json("/api/v1/downpayment-requests")
        return [DownPaymentRequest.model_validate(d) for d in data]


def build_sap_client(base_url: str | None) -> SapClient:
    return HttpSapClient(base_url) if base_url else MemorySapClient()
