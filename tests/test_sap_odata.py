"""The OData client against the bundled mock server: same data, same decisions, safe keys."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dpa_agent.audit import AuditLog
from dpa_agent.config import PolicyLimits
from dpa_agent.graph.graph import build_graph, evaluate
from dpa_agent.llm.client import FallbackRecommender
from dpa_agent.rag.store import PolicyStore
from dpa_agent.sap import mock_data
from dpa_agent.sap.client import MemorySapClient, ODataSapClient, parse_sap_date, sap_date
from dpa_agent.sap.mock_odata import make_server


class _Resp:
    def __init__(self, status: int, body: bytes) -> None:
        self.status_code, self._body = status, body

    def json(self):
        return json.loads(self._body)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class UrllibHttp:
    """Minimal httpx.Client look-alike, so the client logic runs without extra dependencies."""

    def get(self, url, params=None):
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url) as r:
                return _Resp(r.status, r.read())
        except urllib.error.HTTPError as exc:
            return _Resp(exc.code, exc.read())


def _serve():
    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/odata"


def test_sap_date_round_trip():
    assert parse_sap_date(sap_date(parse_sap_date("2026-11-02"))).isoformat() == "2026-11-02"
    assert parse_sap_date("2026-11-02").isoformat() == "2026-11-02"
    assert parse_sap_date(None) is None


def test_odata_client_returns_exactly_what_the_in_memory_client_returns():
    server, base = _serve()
    try:
        odata, memory = ODataSapClient(base, http=UrllibHttp()), MemorySapClient()
        for vendor_id in mock_data.VENDORS:
            assert odata.get_vendor(vendor_id) == memory.get_vendor(vendor_id)
            assert odata.list_prior_payments(vendor_id, None) == sorted(
                memory.list_prior_payments(vendor_id, None), key=lambda p: (p.cleared_on, p.payment_id)
            )
        for po_id in mock_data.POS:
            assert odata.get_po(po_id) == memory.get_po(po_id)
        for req_id in mock_data.REQUESTS:
            assert odata.get_request(req_id) == memory.get_request(req_id)
        assert len(odata.list_requests()) == len(mock_data.REQUESTS)
    finally:
        server.shutdown()
        server.server_close()


def test_unknown_resources_raise_keyerror_like_the_in_memory_client():
    server, base = _serve()
    try:
        odata = ODataSapClient(base, http=UrllibHttp())
        for call in (odata.get_vendor, odata.get_po, odata.get_request):
            try:
                call("NOPE-1")
            except KeyError:
                continue
            raise AssertionError("expected KeyError")
    finally:
        server.shutdown()
        server.server_close()


def test_keys_that_could_alter_the_query_are_refused():
    odata = ODataSapClient("http://127.0.0.1:1/odata", http=UrllibHttp())
    for bad in ("V-1' or '1'='1", "a/b", "x')", ""):
        try:
            odata.get_vendor(bad)
        except ValueError:
            continue
        raise AssertionError(f"unsafe key accepted: {bad!r}")


def test_pipeline_decisions_are_identical_over_http_and_in_memory(tmp_path: Path):
    server, base = _serve()
    try:
        def graph_for(client, name):
            return build_graph(client=client, recommender=FallbackRecommender(),
                               store=PolicyStore.from_directory(),
                               audit=AuditLog(tmp_path / name), limits=PolicyLimits())

        memory = MemorySapClient()
        g_mem = graph_for(memory, "mem.jsonl")
        g_http = graph_for(ODataSapClient(base, http=UrllibHttp()), "http.jsonl")
        for req_id in mock_data.REQUESTS:
            request = memory.get_request(req_id)
            a = evaluate(g_mem, request)["final"]
            b = evaluate(g_http, request)["final"]
            assert (a.label, a.citations, a.guardrail_actions) == (b.label, b.citations, b.guardrail_actions)
    finally:
        server.shutdown()
        server.server_close()
