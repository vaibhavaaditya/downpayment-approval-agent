"""Mock S/4HANA OData-style server. Standard library only.

    python -m dpa_agent.sap.mock_odata --port 8090
    SAP_ODATA_BASE_URL=http://localhost:8090/odata uvicorn dpa_agent.api.main:app

Serves the in-memory mock dataset in an OData V2 dialect (`d` envelope, `/Date(ms)/` dates,
decimals as strings, SAP-style field names and 404 error bodies). Entity shapes are
SIMPLIFIED and loosely modelled on S/4HANA API entities, not byte-for-byte SAP payloads.
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from dpa_agent.models.request import DownPaymentRequest, PriorPayment, PurchaseOrder, Vendor
from dpa_agent.sap import mock_data
from dpa_agent.sap.client import sap_date

_SINGLE = re.compile(r"^/odata/(PurchaseOrders|Suppliers|DownPaymentRequests)\('([^']+)'\)$")
_FILTER = re.compile(r"^Supplier eq '([A-Za-z0-9_\-]+)'$")


def enc_vendor(v: Vendor) -> dict:
    bank = v.master_bank
    return {
        "Supplier": v.vendor_id,
        "SupplierName": v.name,
        "Country": v.country,
        "PaymentIsBlockedForSupplier": v.payment_block,
        "RiskTier": v.risk_tier,
        "OnTimeDeliveryPct": str(v.on_time_delivery_pct),
        "to_Bank": {
            "results": [
                {
                    "BankCountry": bank.bank_country,
                    "BankKey": bank.bank_key,
                    "BankAccount": bank.account_number,
                    "IBAN": bank.iban,
                    "ValidityStartDate": sap_date(v.bank_changed_on) if v.bank_changed_on else None,
                }
            ]
        },
    }


def enc_po(po: PurchaseOrder) -> dict:
    return {
        "PurchaseOrder": po.po_id,
        "Supplier": po.vendor_id,
        "CompanyCode": po.company_code,
        "DocumentCurrency": po.currency,
        "NetAmount": str(po.net_value),
        "DownPaymentPercent": str(po.dp_percent_allowed),
        "Status": po.status,
        "to_Item": {
            "results": [
                {
                    "PurchaseOrderItem": line.line_id,
                    "Material": line.material,
                    "NetAmount": str(line.net_value),
                }
                for line in po.lines
            ]
        },
    }


def enc_payment(p: PriorPayment) -> dict:
    return {
        "DocumentId": p.payment_id,
        "Supplier": p.vendor_id,
        "PurchaseOrder": p.po_id or "",
        "Amount": str(p.amount),
        "Currency": p.currency,
        "ClearingDate": sap_date(p.cleared_on),
        "DocumentType": p.doc_type,
    }


def enc_request(r: DownPaymentRequest) -> dict:
    bank = r.remittance_bank
    return {
        "RequestId": r.request_id,
        "Supplier": r.vendor_id,
        "PurchaseOrder": r.po_id,
        "Amount": str(r.amount),
        "Currency": r.currency,
        "RequestedBy": r.requested_by,
        "RequestDate": sap_date(r.requested_on),
        "Purpose": r.purpose,
        "PayeeBankCountry": bank.bank_country,
        "PayeeBankKey": bank.bank_key,
        "PayeeBankAccount": bank.account_number,
        "PayeeIban": bank.iban,
        "VendorMessage": r.vendor_document_text,
    }


def _error(message: str) -> dict:
    return {"error": {"code": "404", "message": {"lang": "en", "value": message}}}


def make_server(host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    entities = {
        "PurchaseOrders": {k: enc_po(v) for k, v in mock_data.POS.items()},
        "Suppliers": {k: enc_vendor(v) for k, v in mock_data.VENDORS.items()},
        "DownPaymentRequests": {k: enc_request(v) for k, v in mock_data.REQUESTS.items()},
    }
    payments = [enc_payment(p) for p in mock_data.PRIOR_PAYMENTS]

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            parsed = urlparse(self.path)
            path = unquote(parsed.path).rstrip("/")
            single = _SINGLE.match(path)
            if single:
                entity, key = single.groups()
                obj = entities[entity].get(key)
                if obj is None:
                    return self._send(404, _error("Resource not found"))
                return self._send(200, {"d": obj})
            if path == "/odata/DownPaymentRequests":
                rows = list(entities["DownPaymentRequests"].values())
                return self._send(200, {"d": {"results": rows}})
            if path == "/odata/SupplierPayments":
                flt = parse_qs(parsed.query).get("$filter", [""])[0]
                match = _FILTER.match(flt)
                if not match:
                    return self._send(400, _error("Unsupported $filter"))
                rows = [p for p in payments if p["Supplier"] == match.group(1)]
                return self._send(200, {"d": {"results": rows}})
            return self._send(404, _error("Unknown resource"))

        def log_message(self, *args) -> None:  # keep test/CLI output quiet
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock S/4HANA OData-style server")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    server = make_server(port=args.port)
    print(f"Mock S/4HANA OData server on http://127.0.0.1:{args.port}/odata (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
